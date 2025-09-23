# UMA DB Alert

Watches **UMA Global DB** searches and posts only **new/changed** trainers to a Discord **forum** channel.

- **First run seeds only** (no posts)
- **Adding a new search** seeds that search (no posts)
- **Subsequent runs** post **deltas** only
- State is cached between runs via GitHub Actions cache
- Pluggable outputs via a small **catalogue** (`output_catalogue.yaml`)
- Designed so **external repos** can run this

> Targets Python 3.11 + Playwright (Chromium). All deps are installed inside the workflow.

---

## Who is this for?

- **Users** who just want alerts in their own Discord: see **[External usage](docs/USAGE_EXTERNAL.md)**.
- **Developers** who want to change/extend the logic, outputs, or workflows: see **[Developer guide](docs/DEVELOPERS.md)**.

---

## High-level flow

source_sites/uma_global.py → records
│
▼
utils/state.py (per-search seeded cache; dedup by trainer_id + whitelist fingerprint)
│
▼
formatters/discord_forum.py → outputs/discord.py
│
▼
Discord forum post(s)

---

## Key ideas

- **Per-search state**: each search is keyed by `site_id::search_name` + canonical URL.
- **Outputs**: choose via `outputs_profiles` (CSV). Today the profile is `discord_forum`.
- **Secrets model**: the catalogue lists which env var each output needs (e.g. `DISCORD_WEBHOOK_FORUM`).  
  The workflow exports that from your repo/environment secret, and preflights it.

---

## Links

- **External usage (copy-paste friendly)** → [`docs/USAGE_EXTERNAL.md`](docs/USAGE_EXTERNAL.md)  
- **Developer guide** → [`docs/DEVELOPERS.md`](docs/DEVELOPERS.md)
