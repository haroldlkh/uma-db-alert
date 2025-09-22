# utils/state.py
from __future__ import annotations
import hashlib, json, os, time, unicodedata, re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from typing import Dict, List

# ---------- paths ----------
def _repo_namespace() -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "local_repo")
    return repo.replace("/", "_")

def _env_name() -> str:
    # optional: set UMA_ENV_NAME in your workflow; defaults to "repo"
    return os.getenv("UMA_ENV_NAME", "repo")

def state_dir() -> str:
    """
    Resolve a *stable* state directory.
    If UMA_STATE_DIR is provided (CI passes a per-repo/per-env root),
    we DO NOT append repo/env again — only add 'state'.
    Otherwise, default to ~/.uma_monitor/<repo>/<env>/state.
    """
    base = os.getenv("UMA_STATE_DIR")
    if base:
        p = os.path.join(base, "state")
    else:
        base = os.path.expanduser("~/.uma_monitor")
        p = os.path.join(base, _repo_namespace(), _env_name(), "state")
    os.makedirs(p, exist_ok=True)
    return p

def _canon_url(u: str) -> str:
    """Normalize URLs so tiny differences don't create new state files."""
    u = unicodedata.normalize("NFKC", (u or "").strip())
    if not u:
        return ""
    try:
        scheme, netloc, path, query, frag = urlsplit(u)
        scheme = (scheme or "").lower()
        netloc = (netloc or "").lower()
        # drop trailing slash in path (but keep root '/')
        if path.endswith("/") and len(path) > 1:
            path = path[:-1]
        # sort query params for stability
        if query:
            q = parse_qsl(query, keep_blank_values=True)
            q.sort()
            query = urlencode(q)
        # ignore fragment in identity
        frag = ""
        return urlunsplit((scheme, netloc, path, query, frag))
    except Exception:
        # fall back to trimmed
        return u.rstrip("/")

def _search_key(site_id: str, url: str) -> str:
    raw = f"{site_id}||{_canon_url(url)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def state_path(site_id: str, url: str) -> str:
    return os.path.join(state_dir(), f"{_search_key(site_id, url)}.json")

# ---------- normalization & fingerprint ----------
def _clean_token(s: str) -> str:
    if not s: return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00A0", " ").replace("\u200B", "").replace("\uFEFF", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r"\s+([)\]])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    return s

def whites_fingerprint(white_list: List[str]) -> str:
    # order-insensitive, duplicates kept via sorting
    toks = [_clean_token(x) for x in (white_list or []) if _clean_token(x)]
    toks_sorted = sorted(toks)
    blob = "\x1f".join(toks_sorted)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()

# ---------- state I/O ----------
def load(site_id: str, url: str) -> Dict:
    p = state_path(site_id, url)
    if not os.path.exists(p):
        return {
            "version": 1,
            "site_id": site_id,
            "search_url": _canon_url(url),
            "seeded": False,
            "digests": {},            # trainer_id -> fingerprint(white_list)
            "window_limit": 2000,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updated_at": None,
        }
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save(site_id: str, url: str, st: Dict) -> None:
    st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p = state_path(site_id, url)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)

def trim_window(st: Dict) -> None:
    limit = int(st.get("window_limit", 2000))
    d = st.get("digests", {})
    if len(d) <= limit: return
    keep_ids = sorted(d.keys())[-limit:]   # cheap heuristic; IDs grow
    st["digests"] = {k: d[k] for k in keep_ids}

def seed_from_records(state: dict, records: list[dict]) -> None:
    """
    Populate state['digests'] from a batch of records and mark seeded=True.
    Safe to call when already seeded (it will no-op).
    """
    if state.get("seeded"):
        return
    digs = state.setdefault("digests", {})
    for r in records or []:
        fid = str(r.get("trainer_id", "")).strip()
        if not fid:
            continue
        fp = whites_fingerprint(r.get("white_list", []))
        digs[fid] = fp
    state["seeded"] = True

