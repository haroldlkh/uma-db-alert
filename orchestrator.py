import argparse, importlib, time, yaml
from formatters.discord_forum import make_title_and_body
from utils import state as st

def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def send_to_all_outputs(title: str, body: str, outputs_cfg: dict, dry_run: bool):
    for spec in outputs_cfg.get("outputs", []):
        mod = importlib.import_module(f"outputs.{spec['type']}")  # e.g. outputs.discord
        mod.send(title, body, spec.get("settings", {}), dry_run=dry_run)

def filter_new_or_changed(site_id: str, search: dict, records: list[dict]) -> list[dict]:
    url   = search["url"]
    state = st.load(site_id, url)
    opts  = (search.get("options") or {})

    detect_updates = bool(opts.get("detect_updates", True))
    bootstrap      = bool(opts.get("bootstrap", False))
    per_run_max    = int(opts.get("per_run_max", 50))

    if not detect_updates:
        return records[:per_run_max]

    # First run seeding: record, don't emit
    if not state.get("seeded") and bootstrap:
        for r in records:
            fid = str(r["trainer_id"])
            fp  = st.whites_fingerprint(r.get("white_list", []))
            state["digests"][fid] = fp
        state["seeded"] = True
        st.trim_window(state); st.save(site_id, url, state)
        print(f"[state] seeded {len(records)} rows for {site_id}")
        return []

    out = []
    digests = state.get("digests", {})
    for r in records:
        fid = str(r["trainer_id"])
        fp  = st.whites_fingerprint(r.get("white_list", []))
        if digests.get(fid) != fp:
            out.append(r)
            digests[fid] = fp
            if len(out) >= per_run_max:
                break

    state["digests"] = digests
    st.trim_window(state); st.save(site_id, url, state)
    return out

def run(sites_cfg_path: str, outputs_cfg_path: str, dry_run: bool) -> int:
    sites_cfg = load_yaml(sites_cfg_path)
    outputs_cfg = load_yaml(outputs_cfg_path)

    # For now: call each configured source_site, but only take the FIRST record overall.
    for site in sites_cfg.get("sites", []):
        source_site_mod = importlib.import_module(f"source_sites.{site['source_site']}")  # e.g., source_sites.uma_global
        for search in site.get("searches", []):
            site_id = site['source_site']
            records = source_site_mod.scrape(search)
            if not records:
                print("No records for search:", search.get("url"))
                continue
            r = next((x for x in records if x.get("trainer_id") and x.get("id_url")), None)
            if not r:
                print("No valid records (missing trainer_id/id_url) for:", search.get("url"))
                continue

            to_post = filter_new_or_changed(site_id, search, records)

            for r in to_post:  # post every record from the page
                title, body = make_title_and_body(r)
                send_to_all_outputs(title, body, outputs_cfg, dry_run=dry_run)
                time.sleep(1.0)  # polite throttle to avoid rate limits
                print("Posted trainer:", r["trainer_id"], "(dry_run=" + str(dry_run) + ")")
    print("No placeholder records produced.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", required=True)
    ap.add_argument("--outputs", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(args.sites, args.outputs, args.dry_run)

if __name__ == "__main__":
    raise SystemExit(main())
