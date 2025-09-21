from typing import Dict, Tuple, List
import re

# Minimal markdown escaping so chips render as literal text
_MD_ESC = str.maketrans({
    "*": r"\*",
    "_": r"\_",
    "~": r"\~",
    "`": r"\`",
    "|": r"\|",
    ">": r"\>",
})

def _escape_md(s: str) -> str:
    return s.translate(_MD_ESC)

def _clean_ws(s: str) -> str:
    """
    Normalize whitespace:
      - convert NBSP to normal space
      - drop zero-width chars
      - collapse runs of whitespace to single spaces
      - trim ends
    """
    if not s:
        return ""
    s = s.replace("\u00A0", " ")   # NBSP
    s = s.replace("\u200B", "")    # zero-width space
    s = s.replace("\uFEFF", "")    # BOM/ZWNBS
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _sanitize(s: str) -> str:
    return _escape_md(_clean_ws(s or ""))

def _join(xs: List[str], sep: str = ", ") -> str:
    """Join list items with a separator after sanitizing each token."""
    tokens = [_sanitize(x) for x in xs if _clean_ws(x)]
    return sep.join(tokens)

def make_title_and_body(r: Dict) -> Tuple[str, str]:
    """
    Expected record fields:
      trainer_id, blue_list, pink_list, unique_list, white_list, white_count, g1_count, id_url
    """
    required = ["trainer_id", "blue_list", "pink_list", "unique_list",
                "white_list", "white_count", "g1_count", "id_url"]
    missing = [k for k in required if k not in r]
    if missing:
        raise ValueError(f"Formatter missing fields: {missing}")

    trainer_id = _clean_ws(str(r["trainer_id"]))

    blue  = _join(r.get("blue_list", []),  sep=", ")
    pink  = _join(r.get("pink_list", []),  sep=", ")
    uniq  = _join(r.get("unique_list", []), sep=", ")
    white = _join(r.get("white_list", []), sep=", ")

    white_count = int(r.get("white_count", 0))
    g1_count    = int(r.get("g1_count", 0))
    id_url      = _clean_ws(r.get("id_url", ""))

    # Title: trainer id, then Blue/Pink combined (comma-separated), then White/G1 (comma)
    name_bits: List[str] = []
    if blue: name_bits.append(blue)
    if pink: name_bits.append(pink)
    sparks = ", ".join(name_bits)

    title_parts: List[str] = [trainer_id]
    if sparks:
        title_parts.append(sparks)
    title_parts.append(f"White {white_count}, G1 {g1_count}")
    title = " — ".join(title_parts)

    # Body: all lists comma-separated (no pipes)
    body = (
        f"Blue:   {blue}\n"
        f"Pink:   {pink}\n"
        f"Unique: {uniq}\n"
        f"White:  {white}\n\n"
        f"{id_url}"
    ).strip()

    return title, body
