"""Bike definitions (standard library only): bikes/<id>.json, matched to a log by the vehicle
name set in the logger.

    name         the bike
    match        lower-case pieces of text; the bike is used when the logger's vehicle name
                 contains any of them
    gear_ratios  the gearbox ratios, lowest gear first (from the maker's specification). With these
                 the tools can work out the gear from engine revs and GPS speed when the ECU sends
                 no gear channel. Sprockets, primary drive and tyre size are not needed: they move
                 every gear by the same factor, and that factor is fitted from the data.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BIKES_DIR = os.path.join(os.path.dirname(HERE), "bikes")


def known(bikes_dir=None):
    root = bikes_dir or BIKES_DIR
    out = []
    for f in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not f.endswith(".json"):
            continue
        path = os.path.join(root, f)
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            ratios = [float(x) for x in d.get("gear_ratios") or []]
            out.append({"id": f[:-5], "name": str(d["name"]), "match": [str(m).lower() for m in d["match"]],
                        "gear_ratios": ratios})
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise SystemExit(f"{path}: not a usable bike file ({type(e).__name__}: {e})")
        if any(b <= a for a, b in zip(ratios[1:], ratios)) or any(r <= 0 for r in ratios):
            raise SystemExit(f"{path}: gear_ratios must be positive and fall from the lowest gear to the highest")
    return out


def match(vehicle, bikes_dir=None):
    """The bike whose match text is in the logger's vehicle name, or None."""
    v = (vehicle or "").lower()
    return next((b for b in known(bikes_dir) if any(m and m in v for m in b["match"])), None)
