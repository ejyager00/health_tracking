#!/usr/bin/env python3
"""
fix_weights.py
Round all weight_lbs values in strength.json to the nearest 0.5 lb.

Run from the scripts/garmin_sync/ directory, or set DATA_DIR:
    python fix_weights.py
    DATA_DIR=/path/to/data python fix_weights.py
"""

import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_FILE = DATA_DIR / "strength.json"


def round_half(x: float) -> float:
    return round(x * 2) / 2


data = json.loads(DATA_FILE.read_text())
changes = 0

for record in data:
    for lift in record.get("lifts", []):
        for s in lift.get("sets", []):
            w = s.get("weight_lbs")
            if w is not None:
                rounded = round_half(w)
                if rounded != w:
                    s["weight_lbs"] = rounded
                    changes += 1

DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(f"Done. Fixed {changes} weight value(s) in {DATA_FILE}.")
