"""One-time per-listing enrichment from Redfin's detail endpoints.

Redfin blocks scripted detail requests, but the same endpoints answer from a
real browser session, and browsers refuse to let a redfin.com page talk to a
local server. So the hand-off is the clipboard:

    python -m tracker.enrich todo [--set defaults] > todo.json
    (in a Chrome tab on any redfin.com page) run tools/redfin_details.js with
    that list; it fetches every endpoint per listing and copies the batch to
    the clipboard as JSON
    python -m tracker.enrich ingest --clipboard      # or --file batch.json

Everything Redfin returns is stored untouched in data/details/<property_id>.json
so more fields can be parsed later without another visit. ``parse_details``
derives the fields the dashboard uses; ``load_parsed`` is what metrics.py calls.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tracker.collect import DEFAULT_CONFIG, load_config, read_rows

DETAILS_DIR = Path("data/details")
ENDPOINTS = ["mainHouseInfoPanelInfo", "belowTheFold", "aboveTheFold", "avm", "propertyParcelInfo"]


def property_id(url: str) -> str | None:
    m = re.search(r"/home/(\d+)", url or "")
    return m.group(1) if m else None


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------

def _payload(raw: dict, endpoint: str) -> dict:
    body = raw.get(endpoint)
    if isinstance(body, str):
        if body.startswith("{}&&"):
            body = body[4:]
        try:
            body = json.loads(body)
        except ValueError:
            return {}
    if not isinstance(body, dict):
        return {}
    return body.get("payload", body) or {}


def flatten_amenities(below: dict) -> dict[str, dict[str, str]]:
    """{group title: {amenity name: 'value, value'}} from belowTheFold.amenitiesInfo."""
    out: dict[str, dict[str, str]] = {}
    for sg in (below.get("amenitiesInfo") or {}).get("superGroups", []) or []:
        for group in sg.get("amenityGroups", []) or []:
            title = group.get("groupTitle") or group.get("referenceName") or "Other"
            entries = out.setdefault(title, {})
            for e in group.get("amenityEntries", []) or []:
                name = (e.get("amenityName") or "").strip()
                vals = [str(v) for v in (e.get("amenityValues") or []) if v not in (None, "")]
                if not vals:
                    continue
                if name:
                    entries[name] = ", ".join(vals)
                else:
                    entries["_"] = (entries.get("_", "") + "; " if entries.get("_") else "") + ", ".join(vals)
    return out


def _amenity_text(amen: dict[str, dict[str, str]]) -> str:
    return "\n".join(f"{g} | {n}: {v}" for g, entries in amen.items() for n, v in entries.items())


def _first_int(text: str):
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

OUTBUILDING_TERMS = [
    ("workshop", r"\b(work ?shops?|shop building|detached shop|metal shop|shops?)\b(?! (?:for|around|nearby|and dining|and restaurants|centers?|ping))"),
    ("shed", r"\bsheds?\b|storage building"),
    ("barn", r"\bbarns?\b"),
    ("pool house", r"\bpool ?house\b"),
    ("guest house", r"\b(guest ?house|guest quarters|casita|granny (flat|unit)|in-?law (unit|suite|quarters))\b"),
    ("ADU", r"\b(adu|accessory dwelling)\b"),
    ("RV parking", r"\b(rv (parking|access|hookups?|pad|storage)|boat parking)\b"),
    ("detached garage", r"\bdetached (\d|two|three|four)?[- ]?car garage|detached garage\b"),
    ("horse facilities", r"\b(horse (property|barn|stalls?|facilit)|stables?|arena)\b"),
]


HYPOTHETICAL = re.compile(r"(build|room (to|for)|space (to|for)|potential|could|whether|possib|imagine|add an?|future|-like)[^.]{0,30}$", re.I)


def find_outbuildings(text: str) -> list[str]:
    found = []
    for label, pat in OUTBUILDING_TERMS:
        for m in re.finditer(pat, text, re.I):
            before = text[max(0, m.start() - 40):m.start()]
            after = text[m.end():m.end() + 8]
            if HYPOTHETICAL.search(before) or after.startswith("-like"):
                continue
            found.append(label)
            break
    return found


def _find(amen: dict, name_pat: str, group_pat: str | None = None):
    for group, entries in amen.items():
        if group_pat and not re.search(group_pat, group, re.I):
            continue
        for name, val in entries.items():
            if re.search(name_pat, name, re.I):
                return val
    return None


def parse_details(raw: dict) -> dict:
    main = _payload(raw, "mainHouseInfoPanelInfo").get("mainHouseInfo", {}) or {}
    below = _payload(raw, "belowTheFold")
    amen = flatten_amenities(below)
    remarks = html.unescape(" ".join(r.get("marketingRemark", "") for r in main.get("marketingRemarks", []) or [])).strip()
    text = f"{remarks}\n{_amenity_text(amen)}"
    low = text.lower()

    garage_val = _find(amen, r"garage spaces|# of garage|garage \(spaces\)|number of garage")
    garage_spaces = _first_int(garage_val) if garage_val else None
    garage_source = "field" if garage_spaces is not None else None
    parking_type = _find(amen, r"parking type|garage ?/ ?carport type|parking features", r"parking|garage") or ""
    if garage_spaces is None and re.search(r"garage", parking_type, re.I):
        spaces = _find(amen, r"# of parking spaces|parking spaces", r"parking|garage")
        garage_spaces = _first_int(spaces) if spaces else None
        garage_source = "field" if garage_spaces is not None else None
    if garage_spaces is None:
        m = re.search(r"\b(\d|one|two|three|four|five|six)[- ]?(car|bay|vehicle)\b", low)
        if m:
            garage_spaces = WORD_NUM.get(m.group(1)) or int(m.group(1))
            garage_source = "prose"
    garage_sqft_val = _find(amen, r"garage.*sq", r"parking|garage")
    garage_sqft = _first_int(garage_sqft_val) if garage_sqft_val else None
    if garage_spaces is None and garage_sqft:
        garage_spaces = 1 if garage_sqft < 340 else 2 if garage_sqft < 600 else 3 if garage_sqft < 850 else max(4, round(garage_sqft / 250))
        garage_source = "sqft"
    has_garage = garage_spaces is not None or bool(re.search(r"\bgarage\b", low))
    if re.search(r"\bno garage\b|garage: none|garage: no\b", low):
        has_garage = False
    parking = _find(amen, r"^parking features|^parking$|parking type", r"parking|garage")

    solar = "none"
    if re.search(r"\bsolar\b", low):
        solar = "mentioned"
        if re.search(r"\b(paid|owned|own(s|ed)? the|purchased|no (solar )?lease|free and clear|fully paid)\b.{0,40}solar|solar.{0,40}\b(paid|is owned|owned|purchased|no lease|fully paid)\b", low):
            solar = "owned"
        elif re.search(r"(leas|ppa|power purchase|assume|assumable|transfer).{0,40}solar|solar.{0,40}(leas|ppa|power purchase|assum|transfer)", low):
            solar = "leased"

    pool_val = _find(amen, r"\bpool\b")
    pool = None
    if pool_val is not None:
        pool = not re.search(r"^(none|no)\b", pool_val.strip(), re.I)
    elif re.search(r"\b(swimming )?pool\b", low) and not re.search(r"\bcarpool|liverpool|pool table\b", low):
        pool = True

    adu_val = _find(amen, r"\badu\b|accessory dwelling")
    adu = None if adu_val is None else not re.search(r"^(no|none|false)\b", adu_val, re.I)

    return {
        "remarks": remarks,
        "garage_spaces": garage_spaces,
        "garage_source": garage_source,
        "garage_sqft": garage_sqft,
        "has_garage": has_garage,
        "parking": parking,
        "solar": solar,
        "pool": pool,
        "outbuildings": find_outbuildings(text),
        "adu": adu,
        "sewer": _find(amen, r"^sewer"),
        "water": _find(amen, r"^water source|^water$"),
        "stories": _find(amen, r"stories"),
        "lot_features": _find(amen, r"^lot features"),
        "hoa": _find(amen, r"hoa"),
        "amenity_groups": sorted(amen),
    }


def load_raw(directory: Path = DETAILS_DIR) -> dict[str, dict]:
    out = {}
    if not directory.exists():
        return out
    for p in sorted(directory.glob("*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("listing_id"):
            out[d["listing_id"]] = d
    return out


def load_parsed(directory: Path = DETAILS_DIR) -> dict[str, dict]:
    """listing_id -> parsed detail fields, for metrics.py."""
    out = {}
    for lid, d in load_raw(directory).items():
        parsed = parse_details(d.get("raw", {}))
        parsed["fetched_at"] = d.get("fetched_at")
        out[lid] = parsed
    return out


# ----------------------------------------------------------------------------
# Batch hand-off
# ----------------------------------------------------------------------------

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1


def todo(config: dict, only_defaults: bool = False) -> list[dict]:
    rows = read_rows(Path(config["data_file"]))
    if not rows:
        return []
    latest = max(r["snapshot_date"] for r in rows)
    done = {p.stem for p in DETAILS_DIR.glob("*.json")} if DETAILS_DIR.exists() else set()
    items = []
    for r in rows:
        if r["snapshot_date"] != latest:
            continue
        pid = property_id(r["url"])
        if not pid or pid in done:
            continue
        if only_defaults and (
            "single family" not in r["property_type"].lower()
            or _num(r["beds"]) < 3 or _num(r["baths"]) < 2 or _num(r["sqft"]) < 1800
        ):
            continue
        items.append({"listing_id": r["listing_id"], "url": r["url"], "property_id": pid, "address": r["address"]})
    return items


GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_\-]{35}")


def scrub(text: str) -> str:
    """Redfin embeds its own Google Maps API key in map image URLs. Not ours,
    not secret, but it trips GitHub's secret scanner on a public repo."""
    return GOOGLE_KEY.sub("REDACTED", text)


def ingest(records: list[dict]) -> tuple[int, list[str]]:
    """Write one raw file per record. Returns (saved, rejected reasons)."""
    DETAILS_DIR.mkdir(parents=True, exist_ok=True)
    saved, rejected = 0, []
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for body in records:
        pid = str(body.get("property_id") or "")
        if not pid.isdigit() or not body.get("listing_id") or not isinstance(body.get("raw"), dict):
            rejected.append(f"{body.get('address', '?')}: missing property_id/listing_id/raw")
            continue
        record = {
            "listing_id": body["listing_id"],
            "property_id": pid,
            "url": body.get("url", ""),
            "address": body.get("address", ""),
            "fetched_at": body.get("fetched_at") or now,
            "raw": body["raw"],
        }
        with open(DETAILS_DIR / f"{pid}.json", "w", encoding="utf-8") as f:
            f.write(scrub(json.dumps(record, ensure_ascii=False, separators=(",", ":"))))
        saved += 1
    return saved, rejected


def read_clipboard() -> str:
    if sys.platform != "win32":
        raise RuntimeError("clipboard read is only wired up for Windows; use --file")
    # Go through PowerShell but force UTF-8 on its output; the default console
    # code page mangles anything outside Latin-1.
    script = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Clipboard -Raw"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, check=True).stdout
    return out.decode("utf-8", errors="replace")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["todo", "ingest", "status", "show"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--set", choices=["all", "defaults"], default="all", help="todo: which listings")
    parser.add_argument("--file", help="ingest: JSON array file produced by tools/redfin_details.js")
    parser.add_argument("--clipboard", action="store_true", help="ingest: read the JSON array from the clipboard")
    parser.add_argument("--id", help="show: listing_id or property_id")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(Path(args.config))

    if args.command == "todo":
        items = todo(config, only_defaults=args.set == "defaults")
        print(json.dumps(items, separators=(",", ":")))
    elif args.command == "ingest":
        text = read_clipboard() if args.clipboard else Path(args.file).read_text(encoding="utf-8")
        try:
            records = json.loads(text)
        except ValueError as e:
            print(f"input is not JSON ({e}); first 80 chars: {text[:80]!r}")
            return 1
        if isinstance(records, dict):
            records = records.get("results", [])
        saved, rejected = ingest(records)
        print(f"saved {saved} listing(s) to {DETAILS_DIR}; rejected {len(rejected)}")
        for r in rejected:
            print("  -", r)
        for body in records[:saved]:
            p = parse_details(body["raw"])
            print(f"  {body.get('address', '')[:30]:30} garage={p['garage_spaces']} solar={p['solar']} pool={p['pool']} out={p['outbuildings']}")
    elif args.command == "status":
        parsed = load_parsed()
        print(f"{len(parsed)} listings enriched; {len(todo(config))} active listings still without details "
              f"({len(todo(config, only_defaults=True))} of them match the default filters)")
        print("solar:", dict(Counter(p["solar"] for p in parsed.values())))
        print("garage spaces:", dict(Counter(p["garage_spaces"] for p in parsed.values())))
        print("pool:", dict(Counter(p["pool"] for p in parsed.values())))
        print("outbuildings:", dict(Counter(o for p in parsed.values() for o in p["outbuildings"])))
    elif args.command == "show":
        for lid, d in load_raw().items():
            if args.id in (lid, d["property_id"]):
                print(json.dumps(parse_details(d["raw"]), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
