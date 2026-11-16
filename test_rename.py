#!/usr/bin/env python3
"""Rebuild the public set under new company names and check the same 60/60.

The private set is described as "the same answer key, only the names and wording
change", so this is the closest rehearsal available: every company is renamed,
its display name is replaced inside its documents, its interviews and its
`company` table, and the folders are relabelled. The numbers stay untouched, so
the verdicts must stay untouched as well.

If a rule leaned on a company name, a folder name or a ticker code, this is where
it would break. Writes to a temporary directory only; `public_test` is opened
read-only.

The one place in this repo that touches `company.db` directly is the copy made
here, and only to rename the company inside the fixture it is building. The agent
never does that: `hw3.py` reaches company data through the four tools, and the
audit scripts go through the same `sql_query` tool.
"""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

SRC = Path("public_test")
NEW_NAMES = [
    "Vantorre Systems", "Brixham Holdings", "Calderbank Labs", "Dunmow Group",
    "Ellerby Technologies", "Fenwick Dynamics", "Garroch Works", "Harrowgate Systems",
    "Invermoor Holdings", "Jessop Technologies", "Kilbride Systems", "Loxley Group",
    "Marlowe Technologies", "Netherby Works", "Ormskirk Labs", "Pendleton Systems",
    "Quarrendon Holdings", "Rothbury Technologies", "Stanmore Works", "Thurlby Group",
]


def retext(value, old: str, new: str):
    """Replace the company name inside any nested str/list/dict."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [retext(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: retext(v, old, new) for k, v in value.items()}
    return value


def rebuild(dest: Path) -> dict[str, dict]:
    truth = json.loads((SRC / "ground_truth.json").read_text(encoding="utf-8"))
    folders = sorted(p for p in SRC.iterdir() if (p / "company.db").is_file())
    mapping: dict[str, dict] = {}
    out_truth: dict[str, dict] = {}

    for i, folder in enumerate(folders):
        new_name = NEW_NAMES[i]
        slug = new_name.lower().replace(" ", "_")
        target = dest / slug
        target.mkdir(parents=True)
        old_name = truth[folder.name]["company"]
        short = old_name.split()[0]

        for src_file in folder.iterdir():
            if src_file.suffix == ".md":
                text = src_file.read_text(encoding="utf-8")
                text = text.replace(old_name, new_name).replace(short, new_name.split()[0])
                (target / src_file.name).write_text(text, encoding="utf-8")
            elif src_file.suffix == ".dat":
                raw = json.loads(zlib.decompress(src_file.read_bytes()))
                raw = retext(raw, old_name, new_name)
                raw = retext(raw, short, new_name.split()[0])
                (target / src_file.name).write_bytes(
                    zlib.compress(json.dumps(raw, ensure_ascii=False).encode())
                )
            elif src_file.suffix == ".db":
                shutil.copy2(src_file, target / src_file.name)
                conn = sqlite3.connect(target / src_file.name)
                conn.execute("UPDATE company SET name = ?", (new_name,))
                conn.commit()
                conn.close()

        entry = dict(truth[folder.name])
        entry["company"] = new_name
        out_truth[slug] = entry
        mapping[folder.name] = {"slug": slug, "name": new_name, "old": old_name}

    (dest / "ground_truth.json").write_text(json.dumps(out_truth, indent=2), encoding="utf-8")
    return mapping


def main() -> int:
    # hw3.py always writes results.csv, so the shipped file is put back afterwards.
    shipped = Path("results.csv")
    kept = shipped.read_bytes() if shipped.is_file() else None
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "renamed_test"
        dest.mkdir()
        mapping = rebuild(dest)
        print(f"rebuilt {len(mapping)} companies under new names in {dest}")
        print("  e.g. " + ", ".join(f"{m['old']} -> {m['name']}" for m in list(mapping.values())[:3]))

        result = subprocess.run(
            [sys.executable, "hw3.py", "--data-folder", str(dest)],
            capture_output=True, text=True,
        )
        print(result.stdout.strip()[-400:] or result.stderr.strip()[-2000:])

        csv_path = Path("results.csv")
        rows = csv_path.read_text(encoding="utf-8").splitlines()
        print(f"  rows written: {len(rows) - 1}")
        back = {v["slug"]: k for k, v in mapping.items()}
        truth = json.loads((SRC / "ground_truth.json").read_text(encoding="utf-8"))
        mismatches = []
        for line in rows[1:]:
            parts = next(csv.reader([line]))
            slug, points = parts[0], parts[1]
            for token in points.split():
                number, verdict = token.split(":")
                if truth[back[slug]]["points"][number] != verdict:
                    mismatches.append((back[slug], number, verdict))
        print(f"  verdicts contradicting the key after renaming: {len(mismatches)}")
        for m in mismatches[:5]:
            print("   ", m)

    if kept is not None:
        shipped.write_bytes(kept)
        print("  results.csv restored to the public-set run")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
