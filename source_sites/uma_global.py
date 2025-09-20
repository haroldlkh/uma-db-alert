from __future__ import annotations
import re
from typing import Dict, List, Any
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

_ID_RE = re.compile(r"(\d{6,})")  # trainer IDs are long digit runs

def _first_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    m = _ID_RE.search(href)
    return m.group(1) if m else None

def _wait_for_first_results(page, search_timeout_ms: int):
    """
    After clicking Search, wait (generously) until at least one result anchor appears.
    """
    try:
        # Single, robust condition: we see at least one anchor containing '#/user/'
        page.wait_for_function(
            """() => document.querySelectorAll("a[href*='#/user/']").length > 0""",
            timeout=search_timeout_ms,
        )
    except PWTimeoutError:
        # Nothing showed up within the timeout
        return False
    return True

def _collect_first_on_page(page) -> dict | None:
    """
    Return the first valid record on the *current* page, or None if not found.
    """
    # Grab all hrefs that look like user profile links
    hrefs: List[str] = page.eval_on_selector_all(
        "a[href*='#/user/']",
        "els => els.map(e => e.getAttribute('href'))",
    ) or []

    trainer_id, id_url = None, None
    for h in hrefs:
        tid = _first_id_from_href(h or "")
        if tid:
            trainer_id = tid
            if h and h.startswith("http"):
                id_url = h
            else:
                id_url = f"https://uma-global.pure-db.com/#/user/{tid}"
            break

    if trainer_id and id_url:
        return {
            "site_id": "uma_global",
            "trainer_id": trainer_id,
            "blue_list":   [],  # fill later when you parse chips
            "pink_list":   [],
            "unique_list": [],
            "white_list":  [],
            "white_count": 0,
            "g1_count":    0,
            "id_url":      id_url,
        }
    return None

def _click_next_and_wait(page, prev_first_href: str | None, per_page_timeout_ms: int = 8000) -> bool:
    """
    Click the 'next page' control and wait until the first result changes.
    Returns True if we navigated to a new page, False if no 'next' available.
    """
    # Common 'next' variants
    candidates = [
        "a[rel='next']",
        "a[aria-label='Next']",
        "button[aria-label='Next']",
        ".pagination a:has-text('>')",
        ".pagination a:has-text('»')",
        "a.page-link[rel='next']",
    ]

    for sel in candidates:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            # Click next
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
                    timeout=per_page_timeout_ms,
                )
            except PWTimeoutError:
                # No change -> assume no more pages
                return False
            return True
    return False

def scrape(
    search: Dict[str, Any],
    *,
    headless: bool = True,
    search_timeout_ms: int = 90000,  # slow first load
    settle_ms: int = 300,            # tiny settle after first results
    max_pages: int = 1,              # 1 = just the first page; 0 = all pages
) -> List[Dict]:
    """
    Navigate to UMA Global search, click Search, wait for the *first* page results,
    then optionally page through quickly. Returns a list of records (first item on
    each page), but your orchestrator can still post just the first overall.
    """
    url = search["url"]
    if "max_pages" in search:
        # allow override from sites.yaml
        mp = int(search["max_pages"])
        max_pages = mp

    out: List[Dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()

        page.goto(url, wait_until="domcontentloaded")

        # Click "Search" (several fallbacks)
        clicked = False
        for locator in (
            lambda: page.get_by_role("button", name="Search"),
            lambda: page.locator("button:has-text('Search')"),
            lambda: page.locator(".btn-success:has-text('Search')"),
            lambda: page.locator("text=Search").locator("xpath=ancestor::button[1]"),
        ):
            try:
                btn = locator()
                if btn and btn.first.is_visible():
                    btn.first.click()
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            try:
                page.locator("button").first.click()
                clicked = True
            except Exception:
                pass

        if not _wait_for_first_results(page, search_timeout_ms=search_timeout_ms):
            print("[uma_global] No results appeared within search timeout.")
            ctx.close(); browser.close()
            return []

        page.wait_for_timeout(settle_ms)

        # Page 1
        first_link = page.locator("a[href*='#/user/']").first
        prev_first_href = first_link.get_attribute("href") if first_link.count() else None

        rec = _collect_first_on_page(page)
        if rec:
            rec["source_url"] = url
            out.append(rec)
            print(f"[uma_global] Page 1 id={rec['trainer_id']}")
        else:
            print("[uma_global] Page 1 had no parsable first record.")

        # Further pages (fast)
        pages_seen = 1
        while (max_pages == 0 or pages_seen < max_pages):
            navigated = _click_next_and_wait(page, prev_first_href)
            if not navigated:
                break

            # Update previous-href tracker
            first_link = page.locator("a[href*='#/user/']").first
            prev_first_href = first_link.get_attribute("href") if first_link.count() else prev_first_href

            rec = _collect_first_on_page(page)
            pages_seen += 1
            if rec:
                rec["source_url"] = url
                out.append(rec)
                print(f"[uma_global] Page {pages_seen} id={rec['trainer_id']}")
            else:
                print(f"[uma_global] Page {pages_seen} had no parsable first record.")

        ctx.close()
        browser.close()

    return out
