"""Create a verified project-local copy from an official Sideloadly installation."""
import argparse
import json
from pathlib import Path
from core import STATE, MANIFEST_NAMES, atomic_json
from update import fetch_manifests, opener, settings, stage_update, update_lock

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(r'D:\Sideloadly'))
    parser.add_argument('--manifests-dir', type=Path, help='Use locally cached official manifests')
    args = parser.parse_args()
    if STATE.exists():
        parser.error('Already initialized. Use update.cmd --source PATH to stage a replacement.')
    config = settings()
    with update_lock():
        if args.manifests_dir:
            manifests = {name: json.loads((args.manifests_dir / name).read_text(encoding='utf-8'))
                         for name in MANIFEST_NAMES}
        else:
            manifests = fetch_manifests(opener(config), float(config['timeout_seconds']))
        candidate = stage_update(args.source, manifests)
        atomic_json(STATE, candidate)
        print(f'Prepared version {candidate["version"]}. Run start.cmd.')

if __name__ == '__main__':
    main()
