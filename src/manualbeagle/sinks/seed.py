"""Seed Homebox from a YAML list of items: `manualbeagle homebox seed items.yaml`.

    - {name: Washer, brand: LG, model: WM5700HVA, location: Laundry}

Locations are created when missing. An item is skipped when one with the same name and model already exists, so
the file can be run again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from .homebox import Homebox


def seed(hb: Homebox, path: Path, dry_run: bool) -> int:
    """Add the items listed in the YAML file at `path`, creating missing locations. Returns the exit code."""
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    locs = hb.locations()
    have = {(i.name.lower(), i.model.lower()) for i in hb.items()}
    made = 0
    for r in rows:
        name, brand, model = r["name"], r.get("brand", ""), str(r.get("model", ""))
        loc = r.get("location") or ""
        if (name.lower(), model.lower()) in have:
            sys.stdout.write(f"– exists   {name} ({brand} {model})\n")
            continue
        if dry_run:
            sys.stdout.write(f"+ would add {name} ({brand} {model}) in {loc or 'no location'}\n")
            continue
        parent = None
        if loc:
            parent = locs.get(loc) or hb.create_location(loc)
            locs[loc] = parent
        hb.create_item(name, brand, model, parent, notes=r.get("notes", ""))
        made += 1
        sys.stdout.write(f"✓ added    {name} ({brand} {model}) in {loc or 'no location'}\n")
    sys.stdout.write(f"{'DRY RUN. ' if dry_run else ''}{made} added\n")
    return 0
