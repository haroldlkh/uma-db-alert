from __future__ import annotations
import re
from typing import Dict, List, Any
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

_ID_RE = re.compile(r"(\d{6,})")  # trainer ids are long digit runs

def _first_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    m = _ID_RE.search(href)
    return m.group(1) if m else None

def _wait_for_results_after_search(page, timeout_ms: int) -> bool:
    try:
        page.wait_for_function(
            """() => document.querySelectorAll("a[href*='#/user/']").length > 0""",
            timeout=timeout_ms,
        )
        return True
    except PWTimeoutError:
        return False

def _collect_first_on_page(page) -> dict | None:
    hrefs: List[str] = page.eval_on_selector_all(
        "a[href*='#/user/']",
        "els => els.map(e => e.getAttribute('href'))",
    ) or []
    trainer_id, id_url = None, None
    for h in hrefs:
        tid = _first_id_from_href(h or "")
        if tid:
            trainer_id = tid
            id_url = h if (h and h.startswith("http")) else f"https://uma-global.pure-db.com/#/user/{tid}"
            break
    if trainer_id and id_url:
        return {
            "site_id": "uma_global",
            "trainer_id": trainer_id,
            "blue_list": [], "pink_list": [], "unique_list": [], "white_list": [],
            "white_count": 0, "g1_count": 0,
            "id_url": id_url,
        }
    return None

def _pager_selector(page) -> str | None:
    for sel in ["ul.pagination", "nav[aria-label*='pagination' i]", ".pagination"]:
        if page.locator(sel).count() > 0:
            return sel
    return None

def _visible_page_numbers(page, pager_sel: str) -> List[int]:
    js = """
    (sel) => {
      const root = document.querySelector(sel);
      const els = root ? Array.from(root.querySelectorAll('a,button')) : [];
      const nums = els.map(e => (e.textContent || '').trim()).filter(t => /^[0-9]+$/.test(t));
      return Array.from(new Set(nums)).map(n => parseInt(n,10)).sort((a,b)=>a-b);
    }
    """
    try:
        return page.evaluate(js, pager_sel) or []
    except Exception:
        return []

def _goto_page_number(page, n: int, prev_first_href: str | None, timeout_ms: int = 8000) -> bool:
    # try <a> then <button> then role=button
    locs = [
        page.locator(f"xpath=(//a[normalize-space(text())='{n}'])[last()]"),
        page.locator(f"xpath=(//button[normalize-space(text())='{n}'])[last()]"),
        page.get_by_role("button", name=str(n)),
    ]
    for loc in locs:
        if loc.count() > 0 and loc.first.is_enabled():
            loc.first.click()
            try:
                page.wait_for_function(
                    """(prev) => {
                        const a = document.querySelector("a[href*='#/user/']");
                        if (!a) return false;
                        const href = a.getAttribute('href') || '';
                        return prev ? href !== prev : href.length > 0;
                    }""",
                    arg=prev_first_href or "",
                    timeout=timeout_ms,
                )
                return True
            except PWTimeoutError:
                return False
    return False

def _click_next_fallback(page, prev_first_href: str | None, timeout_ms: int = 6000) -> bool:
    # handles sites that always show a 'Next' that may do nothing on last page
    for sel in ["a[rel='next']", "a[aria-label='Next']", "button[aria-label='Next']",
                ".pagination a:has-text('>')", ".pagination a:has-text('»')"]:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_enabled():
            loc.first.click()
            try:
                page.wait_for_function(
                    """(prev) => {
                        const a = document.querySelector("a[href*='#/user/']");
                        if (!a) return false;
                        const href = a.getAttribute('href') || '';
                        return prev ? href !== prev : href.length > 0;
                    }""",
                    arg=prev_first_href or "",
                    timeout=timeout_ms,
                )
                return True
            except PWTimeoutError:
                return False
    return False

def scrape(
    search: Dict[str, Any],
    *,
    headless: bool = True,
    search_timeout_ms: int = 90000,
    settle_ms: int = 250,
    max_pages: int = 1,   # 0 = all pages
) -> List[Dict]:
    url = search["url"]
    if "max_pages" in search:
        try:
            max_pages = int(search["max_pages"])
        except Exception:
            pass

    out: List[Dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")

        # Click Search
        clicked = False
        for loc in (
            lambda: page.get_by_role("button", name="Search"),
            lambda: page.locator("button:has-text('Search')"),
            lambda: page.locator(".btn-success:has-text('Search')"),
            lambda: page.locator("text=Search").locator("xpath=ancestor::button[1]"),
        ):
            try:
                btn = loc()
                if btn and btn.first.is_visible():
                    btn.first.click(); clicked = True; break
            except Exception:
                pass
        if not clicked:
            try:
                page.locator("button").first.click(); clicked = True
            except Exception:
                pass

        # Wait generously for first results
        if not _wait_for_results_after_search(page, timeout_ms=search_timeout_ms):
            print("[uma_global] No results within search timeout.")
            ctx.close(); browser.close(); return []

        page.wait_for_timeout(settle_ms)

        # Collect page 1
        first = page.locator("a[href*='#/user/']").first
        prev_first_href = first.get_attribute("href") if first.count() else None

        rec = _collect_first_on_page(page)
        if rec:
            rec["source_url"] = url
            out.append(rec)
            print(f"[uma_global] Page 1 id={rec['trainer_id']}")
        else:
            print("[uma_global] Page 1 had no parsable record.")

        # --- Pagination discovery ---
        pager = _pager_selector(page)
        numbers = _visible_page_numbers(page, pager) if pager else []
        has_next = page.locator(
            "a[rel='next'], a[aria-label='Next'], button[aria-label='Next'], "
            ".pagination a:has-text('>'), .pagination a:has-text('»')"
        ).count() > 0

        pages_seen = 1
        def want_more() -> bool:
            return max_pages == 0 or pages_seen < max_pages

        # If there are neither numbers nor a Next button, it's a single page.
        if not numbers and not has_next:
            print("[uma_global] Single page of results; no pager and no Next.")
        else:
            # Prefer numbered pages: 2..N
            if numbers:
                target_last = numbers[-1] if max_pages == 0 else min(numbers[-1], max_pages)
                while want_more() and pages_seen < target_last:
                    next_n = pages_seen + 1
                    if not _goto_page_number(page, next_n, prev_first_href):
                        print(f"[uma_global] Could not navigate to page {next_n}")
                        break
                    page.wait_for_timeout(settle_ms)
                    first = page.locator("a[href*='#/user/']").first
                    prev_first_href = first.get_attribute("href") if first.count() else prev_first_href
                    rec = _collect_first_on_page(page)
                    pages_seen += 1
                    if rec:
                        rec["source_url"] = url
                        out.append(rec)
                        print(f"[uma_global] Page {pages_seen} id={rec['trainer_id']}")
                    else:
                        print(f"[uma_global] Page {pages_seen} had no parsable record.")

        ctx.close(); browser.close()

    return out
