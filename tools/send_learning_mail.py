"""Explicit one-pass learning-mail reconciliation; requires an explicit project root."""

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    from reel.db import DB
    from reel.student_mail import run_pending

    root = args.root.expanduser().resolve()
    if not root.is_dir() or not (root / "reel.db").is_file():
        parser.error("root must contain an existing Reel database")
    database = DB(root / "reel.db")
    with closing(database.conn):
        print(json.dumps(run_pending(database, root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
