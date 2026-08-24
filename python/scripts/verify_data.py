"""Verify raw IEEE-CIS inputs against the committed provenance manifest."""
import argparse
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", type=Path, required=True)
args = parser.parse_args()
manifest = json.loads((root / "DATA_PROVENANCE.json").read_text())
failed = False
for name, metadata in manifest["files"].items():
    path = args.data_dir / name
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest().upper()
    valid = digest == metadata["sha256"]
    print(f"{name}: {'verified' if valid else 'MISMATCH'}")
    failed |= not valid
raise SystemExit(1 if failed else 0)
