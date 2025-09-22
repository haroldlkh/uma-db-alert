from __future__ import annotations
import re
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
    "staging": { "headless": True,  "mode": "first", "verbose": True },
    "prod":    { "headless": True,  "mode": "first", "verbose": False },
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
    # normalize types
    opts["mode"] = str(opts["mode"]).lower()
    opts["max_pages"] = int(opts["max_pages"])
    opts["headless"] = bool(opts["headless"])
    opts["verbose"] = bool(opts["verbose"])
    return opts

# ----------------------------- DOM helpers -----------------------------------
_ID_RE = re.compile(r"(\d{6,})")
def _first_id_from_href(href: Optional[str]) -> Optional[str]:
    if not href: return None
    m = _ID_RE.search(href)
    return m.group(1) if m else None

def _log(v: bool, *a): 
    if v: print("[uma_global]", *a)

# One row = one result card
ROW_XPATH = "//table[contains(@class,'b-table')]//tbody//tr[.//a[contains(@href,'#/user/')]]"

def find_result_cards(page):
    # all rows that have a profile link
    return page.locator(f"xpath=({ROW_XPATH})")

_WHITE_REP = re.compile(r"\(Representative\s*(\d+)\)", re.I)

def _count_white(white_items: list[str]) -> int:
    # Count items that contain the word "Representative" (case-insensitive)
    return sum(1 for s in (white_items or []) if "representative" in (s or "").lower())


def parse_card(ctx, page, *, verbose=False):
    # profile link from this row only
    link = ctx.locator("a[href*='#/user/']").first
    if link.count() == 0:
        return None
    href = link.get_attribute("href") or ""
    tid  = _first_id_from_href(href)
    if not tid:
        return None
    id_url = href if href.startswith("http") else f"https://uma-global.pure-db.com/#/user/{tid}"

    # chips scoped to this row
    def chips(cls: str):
        try:
            return [t.strip() for t in ctx.locator(f".{cls}").all_inner_texts() if t.strip()]
        except Exception:
            return []

    blue  = chips("factor1")
    pink  = chips("factor2")
    uniq  = chips("factor3")
    white_skills = chips("factor4")
    white_races = chips("factor5")
    white = white_skills + white_races

    # counts:
    white_skills_count, white_races_count = _count_white(white_skills), _count_white(white_races)
    white_count = white_skills_count + white_races_count

    # grab "G1 Win countNN" text from within the row
    g1_count = 0
    try:
        g1_node = ctx.locator(".g1_win_count").first  # e.g., <div class="g1_win_count text_black">G1 Win count13</div>
        if g1_node.count() > 0:
            txt = g1_node.inner_text().strip()
            m = re.search(r'(\d+)$', txt)  # capture the trailing number (13)
            if not m:
                m = re.search(r'G1\s*Win\s*count\s*(\d+)', txt, re.I)
            if m:
                g1_count = int(m.group(1))
    except Exception:
        pass

    _log(verbose, f"row id={tid} blue={len(blue)} pink={len(pink)} uniq={len(uniq)} white={len(white)} g1={g1_count}")

    return {
        "site_id": "uma_global",
        "trainer_id": tid,
        "id_url": id_url,
        "blue_list":   blue,
        "pink_list":   pink,
        "unique_list": uniq,
        "white_list":  white,
        "white_count": white_count,
        "g1_count":    g1_count,
    }

def collect_page_records(page: Page, mode: str, *, verbose=False) -> List[Dict]:
    cards = find_result_cards(page)
    n = cards.count()
    if n == 0:
        _log(verbose, "no cards on page")
        return []
    if mode == "first":
        rec = parse_card(cards.first, page, verbose=verbose)
        return [rec] if rec else []
    # mode = first_per_page or all (for now both mean “one record per card”)
    out: List[Dict] = []
    for i in range(n):
        rec = parse_card(cards.nth(i), page, verbose=verbose)
        if rec: out.append(rec)
    return out

# -------------------------- navigation/search --------------------------------
def open_search(page: Page, url: str, *, trigger_timeout_ms: int,
                results_timeout_ms: int, max_click_retries: int, verbose: bool) -> str:
    """
    Navigate to the search URL and try to trigger the search.
    Returns one of: "results" (>=1 row), "empty" (0 rows), "failed" (couldn't trigger).
    """
    page.goto(url, wait_until="domcontentloaded")

    # Wait until UI has the search button and the table skeleton.
    try:
        page.wait_for_selector(".btn-group .btn-success", timeout=10_000)
        page.wait_for_selector("table.b-table", timeout=10_000)
    except PWTimeoutError:
        _log(verbose, "UI not ready (no button/table)");  return "failed"

    btn = page.locator(".btn-group .btn-success").first

    for attempt in range(1, max_click_retries + 1):
        try:
            # Make sure the button is interactable, then click once.
            btn.wait_for(state="visible", timeout=1_000)
            if not btn.is_enabled():
                page.wait_for_timeout(150)
            btn.click()

            # Did the search actually start? (table goes busy=true briefly)
            try:
                page.wait_for_selector("table.b-table[aria-busy='true']",
                                       timeout=trigger_timeout_ms)
            except PWTimeoutError:
                _log(verbose, f"Search click attempt {attempt}: no busy=true → retry")
                page.wait_for_timeout(200 * attempt)
                continue  # try clicking again

            # Now wait for completion (busy=false), then count rows.
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

    _log(verbose, "exhausted search click retries → failed")
    return "failed"

def go_next_page(page: Page, *, verbose: bool, timeout_ms: int = 10_000) -> bool:
    """
    Click the 'Next' pager ONLY. Returns True if we actually navigated to a new page.
    Handles:
      - no pagination bar (single page / no results)
      - disabled next (last page)
      - busy table refresh
    """
    def first_href() -> str:
        a = page.locator("a[href*='#/user/']").first
        return a.get_attribute("href") or ""

    # If there is no pagination bar at all, we can't advance.
    if page.locator("ul.pagination.b-pagination").count() == 0:
        _log(verbose, "pagination bar not present")
        return False

    # If the 'Next' control is disabled (span, or li.page-item.disabled), stop.
    next_disabled = page.locator(
        "ul.pagination .page-item.disabled span[role='menuitem'][aria-label*='next' i]"
    )
    if next_disabled.count() > 0:
        _log(verbose, "Next is disabled (last page)")
        return False

    # Normal case: Next is a button we can click.
    next_btn = page.locator(
        "ul.pagination .page-item button[role='menuitem'][aria-label*='next' i]"
    ).first
    if next_btn.count() == 0:
        _log(verbose, "Next button not found")
        return False

    prev = first_href()
    _log(verbose, f"clicking Next; prev_first_href={prev!r}")
    next_btn.click()

    # Wait for either:
    #  - first result href changes, OR
    #  - table finishes a refresh (aria-busy flips false) and first link exists/changes.
    try:
        page.wait_for_function(
            """(prev) => {
                const table = document.querySelector("table.b-table");
                // changed first link?
                const a = document.querySelector("a[href*='#/user/']");
                const href = a ? (a.getAttribute('href') || '') : '';
                if (href && href !== prev) return true;

                // if table is busy, not ready yet
                if (table && table.getAttribute('aria-busy') === 'true') return false;

                // table claims not busy; ensure we at least have a link
                return !!href && href !== prev;
            }""",
            arg=prev,
            timeout=timeout_ms,
        )
    except PWTimeoutError:
        _log(verbose, "Next click timed out without page change")
        return False

    new = first_href()
    changed = (new != prev) and bool(new)
    _log(verbose, f"Next result: changed={changed}, new_first_href={new!r}")
    return changed

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
                results_timeout_ms=opts["search_timeout_ms"],   # reuse existing timeout
                max_click_retries=opts["max_click_retries"],
                verbose=opts["verbose"],
            )
            if status != "results":
                _log(opts["verbose"], f"search outcome: {status}")
                return []

            page.wait_for_timeout(opts["settle_ms"])
            out.extend(collect_page_records(page, opts["mode"], verbose=opts["verbose"]))

            pages_seen = 1
            while (opts["max_pages"] == 0 or pages_seen < opts["max_pages"]):
                if not go_next_page(page, verbose=opts["verbose"]):
                    break
                page.wait_for_timeout(opts["settle_ms"])
                pages_seen += 1
                out.extend(collect_page_records(page, opts["mode"], verbose=opts["verbose"]))
        finally:
            ctx.close()
            browser.close()

    # annotate source for downstream filtering/logging
    for r in out:
        r["source_url"] = url
    return out