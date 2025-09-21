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
    "search_timeout_ms": 90_000,
    "settle_ms": 250,
}
PRESETS = {
    "staging": { "headless": True,  "mode": "first", "verbose": True },
    "prod":    { "headless": True,  "mode": "first", "verbose": False },
}
ALLOWED_KEYS = set(DEFAULTS.keys())

def _merge_options(search: Dict[str, Any]) -> Dict[str, Any]:
    opts = dict(DEFAULTS)
    # optional: search.options.preset
    preset = (search.get("options") or {}).get("preset")
    if preset in PRESETS:
        opts.update(PRESETS[preset])
    # allow overrides in search.options and directly on search
    for src in (search.get("options") or {}, search):
        for k, v in src.items():
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
def open_search(page: Page, url: str, *, timeout_ms: int, verbose: bool) -> bool:
    page.goto(url, wait_until="domcontentloaded")
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
        except Exception: pass
    if not clicked:
        try: page.locator("button").first.click()
        except Exception: pass
    _log(verbose, "clicked Search:", clicked)
    try:
        page.wait_for_function(
            "()=>document.querySelectorAll(\"a[href*='#/user/']\").length>0",
            timeout=timeout_ms,
        )
        _log(verbose, "results appeared")
        return True
    except PWTimeoutError:
        _log(verbose, "no results in timeout")
        return False

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
def scrape(search: Dict[str, Any]) -> List[Dict]:
    opts = _merge_options(search)
    url  = search["url"]
    out: List[Dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=opts["headless"])
        ctx = browser.new_context()
        page = ctx.new_page()

        if not open_search(page, url, timeout_ms=opts["search_timeout_ms"], verbose=opts["verbose"]):
            ctx.close(); browser.close(); return []

        page.wait_for_timeout(opts["settle_ms"])

        # collect from current page according to MODE
        out.extend(collect_page_records(page, opts["mode"], verbose=opts["verbose"]))
        first_href = page.locator("a[href*='#/user/']").first.get_attribute("href") or ""

        # pagination if requested
        pages_seen = 1
        while (opts["max_pages"] == 0 or pages_seen < opts["max_pages"]):
            if not go_next_page(page, prev_first_href=first_href, verbose=opts["verbose"]):
                break
            page.wait_for_timeout(opts["settle_ms"])
            first_href = page.locator("a[href*='#/user/']").first.get_attribute("href") or first_href
            pages_seen += 1
            out.extend(collect_page_records(page, opts["mode"], verbose=opts["verbose"]))

        ctx.close(); browser.close()

    # annotate source_url for each record
    for r in out: r["source_url"] = url
    return out
