# outputs/discord.py
import os
import requests

def send(title: str, message: str, settings: dict) -> None:
    url_env = settings.get("webhook_env", "")
    url = os.getenv(url_env, "")
    if not url:
        raise RuntimeError(f"Discord webhook URL missing; expected env var {url_env}")
    resp = requests.post(url, json={"content": f"**{title}**\n{message}"}, timeout=20)
    resp.raise_for_status()
