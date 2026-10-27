"""Download the Himalayan Database and write data/summits.json.

For each of the 14 eight-thousanders, finds the most recent successful
summit recorded in the HDB. Peaks outside the HDB's coverage get null.

Also aggregates per-peak stats from the members table: summits and deaths
per year, summits without supplemental oxygen, and the latest season.

Usage: python scripts/update_summits.py [--force] [--tables DIR]
  --tables DIR  read already extracted HIMDATA tables instead of downloading
"""

import json
import re
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from dbfread import DBF

HDB_BASE = "https://www.himalayandatabase.com/"
HDB_UPDATES_PAGE = HDB_BASE + "updates.html"
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "summits.json"

# (name, height in metres, HDB PEAKID or None if not covered by the HDB,
#  range, countries, year of first ascent)
PEAKS = [
    ("Everest", 8849, "EVER", "Mahalangur Himal", ["Nepal", "China"], 1953),
    ("K2", 8611, None, "Karakoram", ["Pakistan", "China"], 1954),
    ("Kangchenjunga", 8586, "KANG", "Kangchenjunga Himal", ["Nepal", "India"], 1955),
    ("Lhotse", 8516, "LHOT", "Mahalangur Himal", ["Nepal", "China"], 1956),
    ("Makalu", 8485, "MAKA", "Mahalangur Himal", ["Nepal", "China"], 1955),
    ("Cho Oyu", 8188, "CHOY", "Mahalangur Himal", ["Nepal", "China"], 1954),
    ("Dhaulagiri I", 8167, "DHA1", "Dhaulagiri Himal", ["Nepal"], 1960),
    ("Manaslu", 8163, "MANA", "Mansiri Himal", ["Nepal"], 1956),
    ("Nanga Parbat", 8126, None, "Western Himalaya", ["Pakistan"], 1953),
    ("Annapurna I", 8091, "ANN1", "Annapurna Himal", ["Nepal"], 1950),
    ("Gasherbrum I", 8080, None, "Karakoram", ["Pakistan", "China"], 1958),
    ("Broad Peak", 8051, None, "Karakoram", ["Pakistan", "China"], 1957),
    ("Gasherbrum II", 8035, None, "Karakoram", ["Pakistan", "China"], 1956),
    ("Shishapangma", 8027, None, "Jugal Himal", ["China"], 1964),
]

SEASONS = {0: None, 1: "Spring", 2: "Summer", 3: "Autumn", 4: "Winter"}
HOSTS = {1: "Nepal", 2: "China", 3: "India"}
NEEDED_FILES = {"exped.dbf", "exped.fpt", "members.dbf", "members.fpt"}
# The site rejects urllib's default User-Agent
HEADERS = {"User-Agent": "summit-tracker (+https://github.com)"}


def fetch(url, timeout):
    return urlopen(Request(url, headers=HEADERS), timeout=timeout)


def find_release():
    """Return (release name, zip URL) of the current HIMDATA package."""
    html = fetch(HDB_UPDATES_PAGE, 60).read().decode("cp1252")
    match = re.search(r'href="(downloads/(HIMDATA[^"]*)\.zip)"', html, re.IGNORECASE)
    if not match:
        sys.exit(f"No HIMDATA zip link found on {HDB_UPDATES_PAGE}")
    return match.group(2), urljoin(HDB_BASE, quote(match.group(1)))


def download_tables(url, dest):
    """Download the zip and extract only the tables we need into dest."""
    zip_path = dest / "himdata.zip"
    with fetch(url, 300) as resp, open(zip_path, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if name.lower() in NEEDED_FILES:
                (dest / name.lower()).write_bytes(zf.read(info))
    missing = NEEDED_FILES - {p.name for p in dest.iterdir()}
    if missing:
        sys.exit(f"Missing files in HDB zip: {sorted(missing)}")


def latest_ascents(tables):
    """Return {PEAKID: exped record} with the latest successful summit per peak."""
    wanted = {peak[2] for peak in PEAKS if peak[2]}
    latest = {}
    for r in DBF(tables / "exped.dbf", encoding="cp1252", load=False):
        pid = r["PEAKID"]
        if pid not in wanted or r["DISPUTED"] or r["CLAIMED"] or not r["SMTDATE"]:
            continue
        if not any(r[f"SUCCESS{i}"] for i in range(1, 5)):
            continue
        key = (r["SMTDATE"], r["EXPID"])
        if pid not in latest or key > (latest[pid]["SMTDATE"], latest[pid]["EXPID"]):
            latest[pid] = r
    return latest


def peak_stats(tables):
    """Return {PEAKID: stats} aggregated from the exped and members tables."""
    wanted = {peak[2] for peak in PEAKS if peak[2]}
    stats = {pid: {
        "years": defaultdict(lambda: [0, 0]),  # year -> [summiters, deaths]
        "seasons": defaultdict(lambda: {"expeditions": 0, "successful_expeditions": 0, "summiters": 0, "deaths": 0}),
        "summiters": 0, "deaths": 0, "no_o2": 0,
    } for pid in wanted}

    for r in DBF(tables / "exped.dbf", encoding="cp1252", load=False):
        if r["PEAKID"] not in wanted or not r["YEAR"].strip():
            continue
        season = stats[r["PEAKID"]]["seasons"][(int(r["YEAR"]), r["SEASON"])]
        season["expeditions"] += 1
        if any(r[f"SUCCESS{i}"] for i in range(1, 5)) and not (r["CLAIMED"] or r["DISPUTED"]):
            season["successful_expeditions"] += 1

    for r in DBF(tables / "members.dbf", encoding="cp1252", load=False, ignore_missing_memofile=True):
        if r["PEAKID"] not in wanted or not r["MYEAR"].strip():
            continue
        st = stats[r["PEAKID"]]
        year, season = int(r["MYEAR"]), st["seasons"][(int(r["MYEAR"]), r["MSEASON"])]
        if r["MSUCCESS"] and not (r["MCLAIMED"] or r["MDISPUTED"]):
            st["summiters"] += 1
            st["years"][year][0] += 1
            season["summiters"] += 1
            if r["MO2NONE"]:
                st["no_o2"] += 1
        if r["DEATH"]:
            st["deaths"] += 1
            st["years"][year][1] += 1
            season["deaths"] += 1

    result = {}
    for pid, st in stats.items():
        (year, season_id), last = max(st["seasons"].items())
        result[pid] = {
            "summiters": st["summiters"],
            "deaths": st["deaths"],
            "no_o2_summiters": st["no_o2"],
            "by_year": [[y, *v] for y, v in sorted(st["years"].items())],
            "last_season": {"year": year, "season": SEASONS.get(season_id), **last},
        }
    return result


def to_ascent(r):
    route = next(r[f"ROUTE{i}"] for i in range(1, 5) if r[f"SUCCESS{i}"])
    return {
        "date": r["SMTDATE"].isoformat(),
        "season": SEASONS.get(r["SEASON"]),
        "expedition_id": r["EXPID"],
        "leaders": r["LEADERS"] or None,
        "nation": r["NATION"] or None,
        "sponsor": r["SPONSOR"] or None,
        "route": route or None,
        "host_country": HOSTS.get(r["HOST"]),
        "summiters": (r["SMTMEMBERS"] or 0) + (r["SMTHIRED"] or 0),
        "source": "hdb",
    }


def main():
    force = "--force" in sys.argv
    local = Path(sys.argv[sys.argv.index("--tables") + 1]) if "--tables" in sys.argv else None
    release, url = find_release()
    print(f"Current HDB release: {release}")

    if OUTPUT.exists() and not force and not local:
        current = json.loads(OUTPUT.read_text(encoding="utf-8"))
        if current.get("sources", {}).get("hdb", {}).get("release") == release:
            print("Already up to date, nothing to do.")
            return

    with tempfile.TemporaryDirectory() as tmp:
        tables = Path(tmp)
        if local:
            for f in local.iterdir():
                if f.name.lower() in NEEDED_FILES:
                    (tables / f.name.lower()).write_bytes(f.read_bytes())
        else:
            print(f"Downloading {url}")
            download_tables(url, tables)
        latest = latest_ascents(tables)
        stats = peak_stats(tables)

    data = {
        "sources": {"hdb": {"release": release, "url": url}},
        "peaks": [
            {
                "name": name,
                "height_m": height,
                "hdb_id": pid,
                "range": mrange,
                "countries": countries,
                "first_ascent_year": first,
                "last_ascent": to_ascent(latest[pid]) if pid in latest else None,
                "stats": stats.get(pid),
            }
            for name, height, pid, mrange, countries, first in PEAKS
        ],
    }
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
