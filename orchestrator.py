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

def filter_new_or_changed(site_id: str, url: str, opts: dict, records: list[dict]) -> list[dict]:
    state = st.load(site_id, url)

    detect_updates = bool(opts.get("detect_updates", True))
    per_run_max    = int(opts.get("per_run_max", 50))

    # If the state file is empty/unseeded, seed and return nothing.
    if not state.get("seeded"):
        st.seed_from_records(state, records)
        st.trim_window(state)
        st.save(site_id, url, state)
        print(f"[state] auto-seeded {len(records)} rows for {site_id}")
        return []

    if not detect_updates:
        return records[:per_run_max]

    # change detection
    out, digests = [], state.get("digests", {})
    for r in records:
        fid = str(r["trainer_id"])
        fp  = st.whites_fingerprint(r.get("white_list", []))
        if digests.get(fid) != fp:  # new or changed
            out.append(r)
            digests[fid] = fp
            if len(out) >= per_run_max:
                break

    state["digests"] = digests
    st.trim_window(state)
    st.save(site_id, url, state)
    return out

def run(sites_cfg_path: str, outputs_cfg_path: str, dry_run: bool) -> int:
    sites_cfg   = load_yaml(sites_cfg_path)
    outputs_cfg = load_yaml(outputs_cfg_path)

    for site in sites_cfg.get("sites", []):
        # INDENT everything in this block
        site_mod      = importlib.import_module(f"source_sites.{site['source_site']}")
        site_opts_cfg = site.get("options") or {}
        eff_opts      = site_mod.merge_site_options(site_opts_cfg)  # once per site

        for idx, search in enumerate(site.get("searches", []), start=1):
            url = search["url"]
            state_site_id = f"{site['source_site']}::{search.get('name') or f'search{idx}'}"
            # You kept the name `scrape`, so call it with (url, eff_opts)
            records = site_mod.scrape(url, eff_opts)
            if not records:
                print("No records for search:", url)
                continue

            # DEBUG: which state file & seeded? (use per-search site id)
            spath = st.state_path(state_site_id, url)
            s0 = st.load(state_site_id, url)
            print(f"[debug] state_file={spath} seeded={s0.get('seeded')} seen={len(s0.get('digests',{}))}")

            to_post = filter_new_or_changed(state_site_id, url, eff_opts, records)
            print(f"[state] site={state_site_id} search={search.get('name')} scanned={len(records)} emit={len(to_post)}")

            for r in to_post:
                title, body = make_title_and_body(r, state_site_id)
                send_to_all_outputs(title, body, outputs_cfg, dry_run=dry_run)
                time.sleep(1.0)
                print("Posted trainer:", r["trainer_id"], f"(dry_run={dry_run})")
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", required=True)
    ap.add_argument("--outputs", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(args.sites, args.outputs, args.dry_run)

if __name__ == "__main__":
    raise SystemExit(main())
