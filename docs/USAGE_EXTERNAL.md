# UMA DB Alert — External Usage

Run the monitor **from your own repo**. You keep control of schedules, secrets, and compute.

---

## 1) Create a Discord webhook (Forum)

Currently only Discord forums posts are available. More options may be added in the future.

Create a **Forum** webhook in your Discord server and copy the URL.

Add this as a **repo secret**:  
**Settings → Secrets and variables → Actions → New repository secret**

- Name: `MY_DISCORD_WEBHOOK`
- Value: (your Discord webhook URL)

---

## 2) Add your searches

Create `config/sites.yaml` in **your** repo. Give each search a **stable** `name`.

```yaml
sites:
  - id: uma-global
    source_site: uma_global
    options:
      headless: true
      verbose: true
      search_timeout_ms: 30000
      settle_ms: 250
      per_run_max: 10000
    searches:
      - name: MySearch1
        url: https://uma-global.pure-db.com/#/search?...      # paste full search URL
      - name: MySearch2
        url: https://uma-global.pure-db.com/#/search?...
```
Renaming a search creates a fresh seed (no posts). Keep names stable.

### 2.1) Caveats of searches added

Currently only works with https://uma-global.pure-db.com/. Other DB sites may be added in the future.

Choose your search options and copy the URL into the yaml.

uma-global returns a max of 100 results for any search, ordered by ascending trainer ID. It is recommended to use searches that return less than 100 results each.

## 3) Add the workflow

Create .github/workflows/uma-monitor.yml in your repo:

```yaml
name: UMA Monitor (uses haroldlkh/uma-db-alert)

permissions:
  actions: write   # lets the reusable workflow prune old caches
  contents: read

on:
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Run without posting"
        type: boolean
        default: false
  schedule:
    - cron: "0 * * * *"   # every 1h (UTC). Adjust as needed. Github may delay or reject request, keep it reasonable.

jobs:
  # Manual trigger
  monitor_dispatch:
    if: github.event_name == 'workflow_dispatch'
    uses: haroldlkh/uma-db-alert/.github/workflows/external-run-monitor.yaml@main
    with:
      dry_run: ${{ inputs.dry_run }}
      sites_path: config/sites.yaml
      tool_ref: main
      outputs_profiles: discord_forum
    secrets:
      CALLER_DISCORD_WEBHOOK_FORUM: ${{ secrets.MY_DISCORD_WEBHOOK }}

  # Scheduled trigger
  monitor_schedule:
    if: github.event_name == 'schedule'
    uses: haroldlkh/uma-db-alert/.github/workflows/external-run-monitor.yaml@main
    with:
      sites_path: config/sites.yaml
      tool_ref: main
      outputs_profiles: discord_forum
    secrets:
      CALLER_DISCORD_WEBHOOK_FORUM: ${{ secrets.MY_DISCORD_WEBHOOK }}
```

## 4) First run behavior

- First ever run → seeds cache (no posts).
- Adding a new search later → seeds just that search (no posts).
- Existing searches → post only new/changed trainers.