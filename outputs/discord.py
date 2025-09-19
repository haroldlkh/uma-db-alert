# outputs/discord.py
import os
import time
import requests

DISCORD_CONTENT_LIMIT = 2000      # hard Discord limit for message content
SAFE_TITLE_LIMIT = 96             # forum thread titles are short; keep conservative

def _clip(s: str, n: int) -> str:
    if s is None:
        return ""
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"

def send(title: str, body: str, settings: dict, dry_run: bool = False) -> None:
    """
    Create a NEW post in a Discord Forum channel via webhook.
    Expects settings:
      - webhook_env: env var name that holds the webhook URL
      - channel_kind: must be 'forum'
      - max_chars: optional int for body clipping (default 1800)
      - applied_tags: optional list of forum tag IDs (ints/strings)
    """
    if settings.get("channel_kind") != "forum":
        raise ValueError("This plugin is configured for forum posts only. Set channel_kind: forum")

    url_env = settings.get("webhook_env", "")
    webhook_url = os.getenv(url_env, "")
    if not webhook_url:
        raise RuntimeError(f"Discord webhook URL not provided; expected env var {url_env}")

    max_chars = int(settings.get("max_chars", DISCORD_CONTENT_LIMIT))
    payload = {
        "thread_name": _clip(title or "", SAFE_TITLE_LIMIT),
        "content": _clip(body or "", min(max_chars, DISCORD_CONTENT_LIMIT)),
    }
    if "applied_tags" in settings:
        payload["applied_tags"] = settings["applied_tags"]

    if dry_run:
        print("[DRY RUN] Would POST to Discord Forum webhook:")
        print(" thread_name:", payload["thread_name"])
        print(" content_len:", len(payload["content"]))
        return

    # Basic post with one 429 retry respecting Retry-After
    resp = requests.post(webhook_url, json=payload, timeout=20)
    if resp.status_code == 429:
        retry_after = float(resp.headers.get("Retry-After", "1"))
        time.sleep(min(retry_after, 5))
        resp = requests.post(webhook_url, json=payload, timeout=20)
    resp.raise_for_status()
