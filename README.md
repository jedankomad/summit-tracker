# summit-tracker

Tracks the most recent recorded summit of each of the 14 eight-thousanders.

**Prototype.** Data currently comes only from [The Himalayan Database](https://www.himalayandatabase.com/)
(HDB), which covers Nepal and its border peaks. That gives 8 of the 14 peaks; K2, Nanga Parbat,
Gasherbrum I/II, Broad Peak and Shishapangma are left empty for now.

## How it works

- `scripts/update_summits.py` finds the current `HIMDATA *.zip` on the HDB updates page, downloads it,
  reads `peaks.DBF` / `exped.DBF` and writes `data/summits.json`. It skips the download if the
  release name in `summits.json` is already current (`--force` overrides).
- An ascent counts if any route of the expedition was successful and it is not flagged as claimed or
  disputed. The latest `SMTDATE` wins.
- Per-peak stats come from `members.DBF` (one row per person per expedition): summits and deaths per
  year, summits without bottled oxygen, and the latest season. "Summits" counts summit events, so a
  person who summited twice counts twice.
- For local work, `--tables DIR` reads an already extracted HIMDATA folder instead of downloading.
- `.github/workflows/update.yml` runs the script monthly and commits `data/` if it changed.
- `index.html` is a static page for GitHub Pages that renders the JSON as a table.

## Data freshness

There is no real-time source of ascents. The HDB is updated twice a year (after the spring and
autumn-winter seasons), so the page usually lags by a few months.

## Local run

```sh
pip install -r requirements.txt
python scripts/update_summits.py
python -m http.server 8000   # then open http://localhost:8000
```

## GitHub Pages

Settings → Pages → Deploy from a branch → `main` / root.
