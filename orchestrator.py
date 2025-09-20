import argparse, importlib, time, yaml
from formatters.discord_forum import make_title_and_body

def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def send_to_all_outputs(title: str, body: str, outputs_cfg: dict, dry_run: bool):
    for spec in outputs_cfg.get("outputs", []):
        mod = importlib.import_module(f"outputs.{spec['type']}")  # e.g. outputs.discord
        mod.send(title, body, spec.get("settings", {}), dry_run=dry_run)

def run(sites_cfg_path: str, outputs_cfg_path: str, dry_run: bool) -> int:
    sites_cfg = load_yaml(sites_cfg_path)
    outputs_cfg = load_yaml(outputs_cfg_path)

    # For now: call each configured source_site, but only take the FIRST record overall.
    for site in sites_cfg.get("sites", []):
        source_site_mod = importlib.import_module(f"source_sites.{site['source_site']}")  # e.g., source_sites.uma_global
        for search in site.get("searches", []):
            records = source_site_mod.scrape(search)
            if not records:
                print("No records for search:", search.get("url"))
                continue
            r = next((x for x in records if x.get("trainer_id") and x.get("id_url")), None)
            if not r:
                print("No valid records (missing trainer_id/id_url) for:", search.get("url"))
                continue

            title, body = make_title_and_body(r)
            send_to_all_outputs(title, body, outputs_cfg, dry_run=dry_run)
            time.sleep(1.0)  # be polite to Discord
            print("Posted placeholder for trainer:", r["trainer_id"], "(dry_run=" + str(dry_run) + ")")
            return 0  # stop after first post for placeholder runs
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
