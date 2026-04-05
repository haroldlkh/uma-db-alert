from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError, Page

# ---------------------- switches live in config only --------------------------
DEFAULTS = {
    "mode": "first",            # 'first' | 'first_per_page' | 'all'
    "max_pages": 1,             # 0 = all pages
    "headless": True,
    "verbose": False,
    "search_timeout_ms": 30_000,      # wait for results after search has started
    "trigger_timeout_ms": 1_200,      # short wait for table to flip aria-busy="true"
    "max_click_retries": 3,           # how many times to retry clicking Search
    "settle_ms": 250,
}

PRESETS = {
    "staging": {"headless": True, "mode": "first", "verbose": True},
    "prod": {"headless": True, "mode": "first", "verbose": False},
}
ALLOWED_KEYS = set(DEFAULTS.keys())


def merge_site_options(site_options: Dict[str, Any]) -> Dict[str, Any]:
    """Merge DEFAULTS <- PRESET <- site_options (filtered to keys this site understands)."""
    opts = dict(DEFAULTS)
    preset = (site_options or {}).get("preset")
    if preset in PRESETS:
        opts.update(PRESETS[preset])
    for k, v in (site_options or {}).items():
        if k in ALLOWED_KEYS:
            opts[k] = v

    opts["mode"] = str(opts["mode"]).lower()
    opts["max_pages"] = int(opts["max_pages"])
    opts["headless"] = bool(opts["headless"])
    opts["verbose"] = bool(opts["verbose"])
    return opts


# ----------------------------- DOM helpers -----------------------------------
_ID_RE = re.compile(r"(\d{6,})")


def _first_id_from_href(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    m = _ID_RE.search(href)
    return m.group(1) if m else None


def _log(v: bool, *a):
    if v:
        print("[uma_global]", *a)


ROW_XPATH = "//table[contains(@class,'b-table')]//tbody//tr[.//a[contains(@href,'#/user/')]]"


def find_result_cards(page: Page):
    return page.locator(f"xpath=({ROW_XPATH})")


def _count_white(white_items: list[str]) -> int:
    return sum(1 for s in (white_items or []) if "representative" in (s or "").lower())


def parse_card(ctx, page: Page, *, verbose: bool = False):
    link = ctx.locator("a[href*='#/user/']").first
    if link.count() == 0:
        return None

    href = link.get_attribute("href") or ""
    tid = _first_id_from_href(href)
    if not tid:
        return None

    id_url = href if href.startswith("http") else f"https://uma-global.pure-db.com/#/user/{tid}"

    def chips(cls: str):
        try:
            return [t.strip() for t in ctx.locator(f".{cls}").all_inner_texts() if t.strip()]
        except Exception:
            return []

    blue = chips("factor1")
    pink = chips("factor2")
    uniq = chips("factor3")
    white_skills = chips("factor4")
    white_races = chips("factor5")
    scenario = chips("factor6")
    white = white_skills + white_races + scenario

    white_skills_count = _count_white(white_skills)
    white_races_count = _count_white(white_races)
    scenario_count = _count_white(scenario)
    white_count = white_skills_count + white_races_count + scenario_count

    g1_count = 0
    try:
        g1_node = ctx.locator(".g1_win_count").first
        if g1_node.count() > 0:
            txt = g1_node.inner_text().strip()
            m = re.search(r"(\d+)$", txt)
            if not m:
                m = re.search(r"G1\s*Win\s*count\s*(\d+)", txt, re.I)
            if m:
                g1_count = int(m.group(1))
    except Exception:
        pass

    _log(verbose, f"row id={tid} blue={len(blue)} pink={len(pink)} uniq={len(uniq)} white={len(white)} g1={g1_count}")

    return {
        "site_id": "uma_global",
        "trainer_id": tid,
        "id_url": id_url,
        "blue_list": blue,
        "pink_list": pink,
        "unique_list": uniq,
        "white_list": white,
        "white_count": white_count,
        "g1_count": g1_count,
    }


def collect_page_records(page: Page, mode: str, *, verbose: bool = False) -> List[Dict]:
    cards = find_result_cards(page)
    n = cards.count()
    if n == 0:
        _log(verbose, "no cards on page")
        return []

    if mode == "first":
        rec = parse_card(cards.first, page, verbose=verbose)
        return [rec] if rec else []

    out: List[Dict] = []
    for i in range(n):
        rec = parse_card(cards.nth(i), page, verbose=verbose)
        if rec:
            out.append(rec)
    return out


# -------------------------- navigation/search --------------------------------
def open_search(
    page: Page,
    url: str,
    *,
    trigger_timeout_ms: int,
    results_timeout_ms: int,
    max_click_retries: int,
    verbose: bool,
) -> str:
    """
    Navigate to the search URL and try to trigger the search.
    Returns one of: "results" (>=1 row), "empty" (0 rows), "failed" (couldn't trigger).
    """
    page.goto(url, wait_until="domcontentloaded")

    try:
        page.wait_for_selector(".btn-group .btn-success", timeout=10_000)
        page.wait_for_selector("table.b-table", timeout=10_000)
    except PWTimeoutError:
        _log(verbose, "UI not ready (no button/table)")
        return "failed"

    btn = page.locator(".btn-group .btn-success").first

    for attempt in range(1, max_click_retries + 1):
        try:
            btn.wait_for(state="visible", timeout=1_000)
            if not btn.is_enabled():
                page.wait_for_timeout(150)

            btn.click()

            try:
                page.wait_for_selector("table.b-table[aria-busy='true']", timeout=trigger_timeout_ms)
            except PWTimeoutError:
                _log(verbose, f"Search click attempt {attempt}: no busy=true -> retry")
                page.wait_for_timeout(200 * attempt)
                continue

            page.wait_for_function(
                "() => { const t=document.querySelector('table.b-table');"
                "return t && t.getAttribute('aria-busy')==='false'; }",
                timeout=results_timeout_ms,
            )

            rows = page.locator("table.b-table tbody tr").count()
            _log(verbose, f"results appeared; rows={rows}")
            return "results" if rows > 0 else "empty"

        except Exception as e:
            _log(verbose, f"Search click attempt {attempt} raised: {e!r}")
            page.wait_for_timeout(200 * attempt)

    _log(verbose, "exhausted search click retries -> failed")
    return "failed"


# -------------------------- pagination debug helpers --------------------------
DEBUG_DIR = Path("debug") / "uma_global"


def ensure_debug_dir():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def debug_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def capture_pagination_debug(page: Page, next_btn, label: str = "pagination"):
    """
    Writes:
      debug/uma_global/<timestamp>_<label>.png
      debug/uma_global/<timestamp>_<label>.json
    """
    ensure_debug_dir()
    stamp = debug_timestamp()
    base = DEBUG_DIR / f"{stamp}_{label}"

    result = {
        "label": label,
        "timestamp": stamp,
        "next_button": None,
        "element_from_point": None,
        "overlays": [],
    }

    try:
        box = next_btn.bounding_box()
        result["next_button"] = box
    except Exception as e:
        result["next_button_error"] = str(e)
        box = None

    try:
        overlays = page.evaluate("""
() => {
  return [...document.querySelectorAll('.fc-dialog-overlay')].map((el, i) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
      index: i,
      className: el.className,
      display: cs.display,
      visibility: cs.visibility,
      opacity: cs.opacity,
      pointerEvents: cs.pointerEvents,
      zIndex: cs.zIndex,
      position: cs.position,
      top: r.top,
      left: r.left,
      width: r.width,
      height: r.height
    };
  });
}
""")
        result["overlays"] = overlays
    except Exception as e:
        result["overlay_error"] = str(e)

    if box:
        try:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            hit = page.evaluate(
                """([x, y]) => {
                    const el = document.elementFromPoint(x, y);
                    if (!el) return null;
                    const cs = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return {
                        tag: el.tagName,
                        className: el.className,
                        text: (el.innerText || '').trim().slice(0, 200),
                        display: cs.display,
                        visibility: cs.visibility,
                        opacity: cs.opacity,
                        pointerEvents: cs.pointerEvents,
                        zIndex: cs.zIndex,
                        top: r.top,
                        left: r.left,
                        width: r.width,
                        height: r.height
                    };
                }""",
                [cx, cy],
            )
            result["element_from_point"] = hit
        except Exception as e:
            result["element_from_point_error"] = str(e)

    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
    except Exception as e:
        result["screenshot_error"] = str(e)

    try:
        base.with_suffix(".json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[debug] failed to write json debug file: {e}")

    print(f"[debug] wrote pagination debug files to {base}")


def dismiss_fc_overlay(page: Page):
    selectors = [
        ".fc-close",
        ".fc-close-button",
        "button[aria-label*='close' i]",
        "button:has-text('Close')",
        "button:has-text('Accept')",
        "button:has-text('Agree')",
        "button:has-text('OK')",
        "button:has-text('Dismiss')",
    ]

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                print(f"[debug] dismiss_fc_overlay: clicking {sel}")
                loc.click(timeout=2000)
                page.wait_for_timeout(500)
                return
        except Exception as e:
            print(f"[debug] dismiss_fc_overlay: failed clicking {sel}: {e}")

    try:
        page.locator(".fc-dialog-overlay").first.wait_for(state="hidden", timeout=3000)
        print("[debug] dismiss_fc_overlay: overlay hidden")
    except Exception:
        pass


def go_next_page(page: Page, verbose: bool = False) -> bool:
    next_btn = page.locator(
        "ul.pagination .page-item button[role='menuitem'][aria-label*='next' i]"
    ).first

    if next_btn.count() == 0:
        _log(verbose, "next button not found")
        return False

    try:
        if not next_btn.is_visible():
            _log(verbose, "next button not visible")
            return False
    except Exception:
        return False

    prev_first_href = None
    try:
        first_link = page.locator("a[href*='#/user/']").first
        if first_link.count() > 0:
            prev_first_href = first_link.get_attribute("href")
    except Exception:
        pass

    # If an overlay is present, try to dismiss it first.
    try:
        if page.locator(".fc-dialog-overlay").count() > 0:
            _log(verbose, "fc-dialog-overlay detected before next click")
            dismiss_fc_overlay(page)
            page.wait_for_timeout(500)
    except Exception:
        pass

    try:
        next_btn.click(timeout=5000)
    except Exception as e:
        _log(verbose, f"normal next click failed: {e}")
        capture_pagination_debug(page, next_btn, label="next_click_failed")

        try:
            page.evaluate("""
() => {
  const btn = document.querySelector(
    "ul.pagination .page-item button[role='menuitem'][aria-label*='next' i]"
  );
  if (btn) btn.click();
}
""")
        except Exception as e2:
            _log(verbose, f"js next click failed: {e2}")
            return False

    try:
        page.wait_for_function(
            """prevHref => {
                const first = document.querySelector("a[href*='#/user/']");
                if (!first) return false;
                return first.getAttribute("href") !== prevHref;
            }""",
            arg=prev_first_href,
            timeout=8000,
        )
        _log(verbose, "pagination succeeded")
        return True
    except Exception as e:
        _log(verbose, f"page did not change after next click: {e}")
        capture_pagination_debug(page, next_btn, label="next_click_no_page_change")
        return False


# --------------------------- public entry point -------------------------------
def scrape(url: str, opts: Dict[str, Any]) -> List[Dict]:
    out: List[Dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=opts["headless"])
        ctx = browser.new_context()
        page = ctx.new_page()

        try:
            status = open_search(
                page,
                url,
                trigger_timeout_ms=opts["trigger_timeout_ms"],
                results_timeout_ms=opts["search_timeout_ms"],
                max_click_retries=opts["max_click_retries"],
                verbose=opts["verbose"],
            )

            if status != "results":
                _log(opts["verbose"], f"search outcome: {status}")
                return []

            page.wait_for_timeout(opts["settle_ms"])
            out.extend(collect_page_records(page, opts["mode"], verbose=opts["verbose"]))

            pages_seen = 1
            while opts["max_pages"] == 0 or pages_seen < opts["max_pages"]:
                if not go_next_page(page, verbose=opts["verbose"]):
                    break

                page.wait_for_timeout(opts["settle_ms"])
                pages_seen += 1
                out.extend(collect_page_records(page, opts["mode"], verbose=opts["verbose"]))

        finally:
            ctx.close()
            browser.close()

    for r in out:
        r["source_url"] = url
    return out