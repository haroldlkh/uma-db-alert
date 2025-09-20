# source_sites/uma_global.py
from __future__ import annotations
import re
from typing import Dict, List, Any
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

RESULT_READY_TEXT = "Search Result"   # heading appears when results render

def _extract_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    m = re.search(r"/#/user/(\d+)", href)
    return m.group(1) if m else None

def scrape(search: Dict[str, Any], *, headless: bool = True, timeout_ms: int = 30000, settle_ms: int = 1200) -> List[Dict]:
    """
    Navigate to the UMA Global search page, click Search, wait for results,
    return ONE normalized record (first result only) so you can test the pipeline.
    """
    url = search["url"]
    records: List[Dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()

        # 1) Open the search page (SPA route)
        page.goto(url, wait_until="domcontentloaded")

        # 2) Click the "Search" button (it’s required to populate results)
        # Try by role first, fall back to text/css.
        clicked = False
        for loc in (
            lambda: page.get_by_role("button", name="Search"),
            lambda: page.locator("button:has-text('Search')"),
            lambda: page.locator(".btn-success:has-text('Search')"),
        ):
            try:
                btn = loc()
                if btn and btn.first.is_visible():
                    btn.first.click()
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            # last resort: click the first visible button on the form
            page.locator("button").first.click()

        # 3) Wait for results to render: heading text and at least one user link
        try:
            page.get_by_text(RESULT_READY_TEXT, exact=False).wait_for(timeout=timeout_ms)
        except PWTimeoutError:
            # Some loads skip the heading—fallback to a hard selector for a user link
            pass

        try:
            page.locator("a[href*='#/user/']").first.wait_for(timeout=timeout_ms)
        except PWTimeoutError:
            # No results
            ctx.close(); browser.close()
            return records

        # Give the UI a moment to settle (chips/images)
        page.wait_for_timeout(settle_ms)

        # 4) Pull the first result’s profile link & trainer id
        first_link = page.locator("a[href*='#/user/']").first
        href = first_link.get_attribute("href")
        # if framework uses router-link, resolve absolute href
        if not href:
            try:
                href = page.evaluate("(el) => el.href", first_link.element_handle())
            except Exception:
                href = None

        trainer_id = _extract_id_from_href(href)
        id_url = href if (href and href.startswith("http")) else (
            f"https://uma-global.pure-db.com/#/user/{trainer_id}" if trainer_id else None
        )

        # 5) Return a minimal normalized record (lists empty for now)
        record = {
            "site_id": "uma_global",
            "trainer_id": trainer_id or "UNKNOWN",
            "blue_list":   [],   # you’ll fill these when you parse chips later
            "pink_list":   [],
            "unique_list": [],
            "white_list":  [],
            "white_count": 0,
            "g1_count":    0,
            "id_url":      id_url or "",
            "source_url":  url,
        }
        records.append(record)

        ctx.close()
        browser.close()

    return records
