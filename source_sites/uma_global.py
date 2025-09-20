# source_sites/uma_global.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Dict, List, Any, Optional
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError, Page

# ──────────────────────────────────────────────────────────────────────────────
# Switches in ONE place (defaults & presets). Override in sites.yaml.
DEFAULTS = {
    "mode": "first",            # 'first' | 'first_per_page' | 'all' (all = placeholder)
    "max_pages": 1,             # 0 = all pages
    "headless": True,
    "search_timeout_ms": 90_000,  # slow first Search
    "settle_ms": 250,
    "verbose": False,
}

PRESETS = {
    "staging": { "headless": True,  "mode": "first", "verbose": True },
    "prod":    { "headless": True,  "mode": "first", "verbose": False },
}

ALLOWED_KEYS = set(DEFAULTS.keys())  # only these may override

# ──────────────────────────────────────────────────────────────────────────────
# Small helpers

_ID_RE = re.compile(r"(\d{6,})")  # trainer ids are long digit runs

def _first_id_from_href(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    m = _ID_RE.search(href)
    return m.group(1) if m else None

def _log(enabled: bool, *args):
    if enabled:
        print("[uma_global]", *args)

def _merge_options(search: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge DEFAULTS <- optional preset <- per-search overrides.
    In sites.yaml you can do:
      options: { preset: staging, mode: first_per_page, headless: false, max_pages: 3 }
    or directly on the search entry.
    """
    # allow site-level "options" block (the orchestrator passes search only; we still support search['options'])
    opts = dict(DEFAULTS)
    preset_name = None
    for src in (search.get("options") or {}, search):  # search.options first, then search
        if "preset" in src:
            preset_name = src["preset"]
    if preset_name and preset_name in PRESETS:
        opts.update(PRESETS[preset_name])
    # explicit overrides
    for k, v in (search.get("options") or {}).items():
        if k in ALLOWED_KEYS:
            opts[k] = v
    for k, v in search.items():
        if k in ALLOWED_KEYS:
            opts[k] = v
    # coerce types
    opts["max_pages"] = int(opts.get("max_pages", 1))
    opts["headless"] = bool(opts.get("headless", True))
    opts["verbose"]  = bool(opts.get("verbose", False))
    opts["mode"]     = str(opts.get("mode", "first")).lower()
    return opts

# ──────────────────────────────────────────────────────────────────────────────
# 1) Open & trigger the search (long wait only here)
def open_search(page: Page, url: str, *, timeout_ms: int, verbose: bool) -> bool:
    page.goto(url, wait_until="domcontentloaded")

    # click Search (several fallbacks)
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

    _log(verbose, "clicked Search:", clicked)

    try:
        page.wait_for_function(
            """() => document.querySelectorAll("a[href*='#/user/']").length > 0""",
            timeout=timeout_ms,
        )
        _log(verbose, "first results detected")
        return True
    except PWTimeoutError:
        _log(verbose, "no results within timeout")
        return False

# ──────────────────────────────────────────────────────────────────────────────
# 2) Scrape current page (first result only for now)
def _parse_chips_and_counts(page: Page, verbose: bool) -> Dict[str, Any]:
    """
    Placeholder parser. Keep structure stable; fill later.
    """
    def grab_list(cls: str) -> List[str]:
        try:
            return [t.strip() for t in page.locator(f".{cls}").all_inner_texts() if t.strip()]
        except Exception:
            return []
    def grab_count(sel: str) -> int:
        try:
            txt = page.locator(sel).first.inner_text().strip()
        except Exception:
            return 0
        m = re.search(r"(\d+)", txt.replace(",", ""))
        return int(m.group(1)) if m else 0

    blue  = grab_list("factor1")
    pink  = grab_list("factor2")
    uniq  = grab_list("factor3")
    white = grab_list("factor4")
    white_count = grab_count(".white_factor_count")
    g1_count    = grab_count(".g1_win_count")

    _log(verbose, f"chips: blue={len(blue)} pink={len(pink)} unique={len(uniq)} white={len(white)} wc={white_count} g1={g1_count}")
    return {
        "blue_list": blue, "pink_list": pink, "unique_list": uniq, "white_list": white,
        "white_count": white_count, "g1_count": g1_count
    }

def scrape_page_first(page: Page, *, verbose: bool) -> Optional[Dict]:
    link = page.locator("a[href*='#/user/']").first
    if link.count() == 0:
        _log(verbose, "no user link on page")
        return None

    href = link.get_attribute("href")
    tid  = _first_id_from_href(href or "")
    if not tid:
        _log(verbose, "could not parse trainer id from href:", href)
        return None

    id_url = href if (href and href.startswith("http")) else f"https://uma-global.pure-db.com/#/user/{tid}"
    meta = _parse_chips_and_counts(page, verbose)

    rec = {
        "site_id": "uma_global",
        "trainer_id": tid,
        "id_url": id_url,
        **meta,
    }
    _log(verbose, "first record:", tid)
    return rec

# ──────────────────────────────────────────────────────────────────────────────
# 3) Try go to next page; True only if first result actually changes
def go_next_page(page: Page, *, prev_first_href: str, verbose: bool, timeout_ms: int = 8000) -> bool:
    def current_first_href() -> str:
        a = page.locator("a[href*='#/user/']").first
        return a.get_attribute("href") or ""

    # Prefer numbered page N = current+1 (loose heuristic: try clicking 2..9 if visible)
    for n in range(2, 10):
        loc = page.locator(f"xpath=(//a[normalize-space(text())='{n}'])[last()]")
        if loc.count() > 0 and loc.first.is_enabled():
            before = current_first_href()
            loc.first.click()
            try:
                page.wait_for_function(
                    """(prev) => {
                        const a = document.querySelector("a[href*='#/user/']");
                        if (!a) return false;
                        const href = a.getAttribute('href') || '';
                        return prev ? href !== prev : href.length > 0;
                    }""",
                    arg=before,
                    timeout=timeout_ms,
                )
                after = current_first_href()
                changed = (after != prev_first_href)
                _log(verbose, f"clicked page {n}, changed={changed}")
                return changed
            except PWTimeoutError:
                pass
            
    _log(verbose, "no pager control found")
    return False

# ──────────────────────────────────────────────────────────────────────────────
# Public entry point: mini-orchestrator for this site
def scrape(search: Dict[str, Any]) -> List[Dict]:
    """
    Uses options from DEFAULTS/PRESETS with per-search overrides.
    Modes:
      - 'first':          only first page's first record
      - 'first_per_page': first record from each page (up to max_pages/0=all)
      - 'all':            placeholder for future: all items per page
    """
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

        # Page 1
        rec = scrape_page_first(page, verbose=opts["verbose"])
        if rec:
            rec["source_url"] = url
            out.append(rec)
        else:
            _log(opts["verbose"], "page 1 had no parsable first record")

        # Early exit for mode='first'
        if opts["mode"] == "first":
            ctx.close(); browser.close(); return out

        # Otherwise loop pages
        pages_seen = 1
        first_href = page.locator("a[href*='#/user/']").first.get_attribute("href") or ""

        while (opts["max_pages"] == 0 or pages_seen < opts["max_pages"]):
            moved = go_next_page(page, prev_first_href=first_href, verbose=opts["verbose"])
            if not moved:
                break
            page.wait_for_timeout(opts["settle_ms"])
            first_href = page.locator("a[href*='#/user/']").first.get_attribute("href") or first_href
            pages_seen += 1

            if opts["mode"] in ("first_per_page", "all"):
                rec = scrape_page_first(page, verbose=opts["verbose"])  # for 'all', replace later
                if rec:
                    rec["source_url"] = url
                    out.append(rec)
                else:
                    _log(opts["verbose"], f"page {pages_seen} had no parsable first record")

        ctx.close(); browser.close()

    return out
