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

# “Card” = the result block for one profile
CARD_XPATH = (
  "//div[contains(@class,'white_factor_count')]/"
  "ancestor::*[self::div or self::li][.//a[contains(@href,'#/user/')]][1]"
)

def find_result_cards(page: Page):
    return page.locator(f"xpath=({CARD_XPATH})")

def parse_card(ctx: Page, page: Page, *, verbose=False) -> Optional[Dict]:
    link = ctx.locator("a[href*='#/user/']").first
    if link.count() == 0: 
        return None
    href = link.get_attribute("href") or ""
    tid  = _first_id_from_href(href)
    if not tid: 
        return None
    id_url = href if href.startswith("http") else f"https://uma-global.pure-db.com/#/user/{tid}"

    # chips only inside this card
    def chips(cls: str) -> List[str]:
        try: raw = ctx.locator(f".{cls}").all_inner_texts()
        except Exception: raw = []
        return [t.strip() for t in raw if t and t.strip()]

    # counts: prefer inside the card; fallback to page if absent
    def count(sel: str) -> int:
        for scope in (ctx, page):
            try:
                txt = scope.locator(sel).first.inner_text().strip()
                m = re.search(r"(\d+)", txt.replace(",", ""))
                return int(m.group(1)) if m else 0
            except Exception:
                continue
        return 0

    rec = {
        "site_id": "uma_global",
        "trainer_id": tid,
        "id_url": id_url,
        "blue_list":   chips("factor1"),
        "pink_list":   chips("factor2"),
        "unique_list": chips("factor3"),
        "white_list":  chips("factor4"),
        "white_count": count(".white_factor_count"),
        "g1_count":    count(".g1_win_count"),
    }
    _log(verbose, f"card id={tid} blue={len(rec['blue_list'])} pink={len(rec['pink_list'])} uniq={len(rec['unique_list'])} white={len(rec['white_list'])}")
    return rec

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

def go_next_page(page: Page, *, prev_first_href: str, verbose: bool, timeout_ms: int = 8000) -> bool:
    def first_href() -> str:
        a = page.locator("a[href*='#/user/']").first
        return a.get_attribute("href") or ""
    # try numeric pages 2..9
    for n in range(2, 10):
        loc = page.locator(f"xpath=(//a[normalize-space(text())='{n}'])[last()]")
        if loc.count() > 0 and loc.first.is_enabled():
            before = first_href()
            loc.first.click()
            try:
                page.wait_for_function(
                    "(prev)=>{const a=document.querySelector(\"a[href*='#/user/']\");if(!a)return false;const h=a.getAttribute('href')||'';return prev?h!==prev:h.length>0;}",
                    arg=before, timeout=timeout_ms,
                )
                after = first_href()
                changed = (after != prev_first_href)
                _log(verbose, f"goto page {n}, changed={changed}")
                return changed
            except PWTimeoutError:
                pass

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
