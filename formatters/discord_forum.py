# formatters/discord_forum.py

from typing import Dict, Tuple, List

# Discord can interpret *, _, ~, `, |, > etc. Escape minimally so chips render as literal text.
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

def _join(xs: List[str]) -> str:
    return " | ".join(_escape_md(x.strip()) for x in xs if x and x.strip())

def make_title_and_body(r: Dict) -> Tuple[str, str]:
    """
    r is a dict produced by your scraper for ONE result, e.g.:
      {
        "trainer_id": "133102601857",
        "blue_list":   ["Stamina9 (Representative3)"],
        "pink_list":   ["Long6 (Representative2)"],
        "unique_list": ["Blue Rose Closer2 (Representative2)", "Flowery☆Maneuver2 (Representative2)"],
        "white_list":  ["Tail Held High2 (Representative2)", "Fighter1 (Representative1)", "..."],
        "white_count": 15,
        "g1_count": 13,
        "id_url": "https://uma-global.pure-db.com/#/user/133102601857"
      }

    Returns:
      (title, body)
    """
    # Required fields (fail fast with a clear error)
    required = ["trainer_id", "blue_list", "pink_list", "unique_list",
                "white_list", "white_count", "g1_count", "id_url"]
    missing = [k for k in required if k not in r]
    if missing:
        raise ValueError(f"Formatter missing fields: {missing}")

    trainer_id = str(r["trainer_id"]).strip()
    blue = _join(r.get("blue_list", []))      # keep “(RepresentativeX)” suffixes intact
    pink = _join(r.get("pink_list", []))
    uniq = _join(r.get("unique_list", []))
    white = _join(r.get("white_list", []))
    white_count = int(r.get("white_count", 0))
    g1_count = int(r.get("g1_count", 0))
    id_url = r["id_url"].strip()

    # Title: trainer + blue/pink “as-is” + formatted White/G1 summary
    sparks = " | ".join(x for x in [blue, pink] if x)
    title_parts = [trainer_id]
    if sparks:
        title_parts.append(sparks)
    title_parts.append(f"White {white_count} | G1 {g1_count}")
    title = " — ".join(title_parts)

    # Body: exactly four lines, then the profile link
    body = (
        f"Blue:   {blue}\n"
        f"Pink:   {pink}\n"
        f"Unique: {uniq}\n"
        f"White:  {white}\n\n"
        f"{id_url}"
    ).strip()

    return title, body
