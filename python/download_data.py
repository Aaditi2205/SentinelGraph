"""Download the IEEE-CIS training tables through Kaggle.

The official competition download requires accepting its rules. For a
reproducible local build, ``--mirror`` uses a public Kaggle dataset mirror and
prints its license limitation explicitly. Raw files are never committed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
FILES = ("train_transaction.csv", "train_identity.csv")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", action="store_true", help="Use the public lnasiri007 mirror")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    if args.mirror:
        command = [sys.executable, "-m", "kaggle", "datasets", "download", "-d", "lnasiri007/ieeecis-fraud-detection"]
        print("WARNING: the mirror lists no license. Confirm data rights before production use.")
    else:
        command = [sys.executable, "-m", "kaggle", "competitions", "download", "-c", "ieee-fraud-detection"]
        print("The official source requires accepting the IEEE-CIS competition rules in Kaggle.")
    subprocess.run([*command, "-p", str(RAW)], check=True)
    for archive in RAW.glob("*.zip"):
        with zipfile.ZipFile(archive) as zipped:
            members = [name for name in zipped.namelist() if Path(name).name in FILES]
            zipped.extractall(RAW, members=members)
    missing = [name for name in FILES if not (RAW / name).exists()]
    if missing:
        raise SystemExit(f"Download completed but required files are missing: {', '.join(missing)}")
    print(f"Ready: {RAW}")


if __name__ == "__main__":
    main()
