import argparse, importlib, time, yaml
from formatters.discord_forum import make_title_and_body

def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def send_to_all_outputs(title: str, body: str, outputs_cfg: dict, dry_run: bool):
    for spec in outputs_cfg.get("outputs", []):
        mod = importlib.import_module(f"outputs.{spec['type']}")  # e.g. outputs.discord
        mod.send(title, body, spec.get("settings", {}), dry_run=dry_run)

def run_once(sites_cfg_path: str, outputs_cfg_path: str, dry_run: bool) -> int:
    sites_cfg = load_yaml(sites_cfg_path)
    outputs_cfg = load_yaml(outputs_cfg_path)

    # For now: call each configured scraper, but only take the FIRST record overall.
    for site in sites_cfg.get("sites", []):
        scraper_mod = importlib.import_module(f"scrapers.{site['scraper']}")  # e.g., scrapers.uma_global
        for search in site.get("searches", []):
            records = scraper_mod.scrape(search)
            if not records:
                continue
            r = records[0]  # placeholder: first result only
            title, body = make_title_and_body(r)
            send_to_all_outputs(title, body, outputs_cfg, dry_run=dry_run)
            time.sleep(1.0)  # be polite to Discord
            print("Posted placeholder for trainer:", r["trainer_id"], "(dry_run=" + str(dry_run) + ")")
            return 0  # stop after first post for placeholder runs
    print("No placeholder records produced.")
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", required=True)
    ap.add_argument("--outputs", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run_once(args.sites, args.outputs, args.dry_run)

if __name__ == "__main__":
    raise SystemExit(main())
