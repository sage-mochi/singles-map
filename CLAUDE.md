# CLAUDE.md

Guidance for Claude Code working in this repo. Read this first. For full living status (what's built, validation numbers, open decisions), see `docs/PROJECT_STATE.md` — keep that updated at the end of each session; keep *this* file for the durable rules and commands.

## What this is

An interactive, **city/metro-level** tool that singles — particularly young men — use to compare U.S. metros by single-population sex ratio, built on U.S. Census ACS data. Self-contained HTML/D3 maps with data baked in; a local Python pipeline regenerates that data.

## How to behave in this repo

- **Be direct and technically deep.** The owner is a software engineer (~8 yrs). Give honest assessments, not reassurance — explicitly flag when something is at parity with existing tools, weak, or wrong. Surface limitations rather than hiding them.
- **Data rigor is non-negotiable.** Always validate computed estimates against published ACS tables before trusting them, and state the data vintage (year, 1-yr vs 5-yr). For any custom PUMS cross-tab, report a margin of error via successive-difference replication over the 80 replicate weights: `SE = sqrt(0.05·Σ(Xr−X)²)`, `MOE90 = 1.645·SE`. **Never present a ranking whose differences fall within the MOE without saying so.**
- **Honest framing.** Geography is the *secondary* lever — age/life-stage dominates the single sex ratio, and every metro has more single men than women at 25–34. The tool surfaces "the few places this measurably tilts your way / least-bad cities," never "move here and your odds transform." Don't drift toward the overpromising the main competitor (SinglesAtlas) falls into.
- **Secrets.** The Census API key is read from `CENSUS_API_KEY`. Never hardcode or commit it.

## Commands

```bash
# setup
pip install pandas requests openpyxl       # node optional (JS syntax check during map build)
export CENSUS_API_KEY=...                   # free: https://api.census.gov/data/key_signup.html

# assemble the deployable map (template + data/*.json -> site/index.html)
python pipeline/build_map.py

# regenerate PUMS data (the in-progress rebuild foundation)
python pipeline/build_xwalk.py             # -> data/puma_cbsa_xwalk.json  (first run: large downloads + ~51 tract-pop API calls, several min)
python pipeline/build_pums_metro.py        # -> data/pums_metro_singles.json (self-generates reconciliation targets; resumable per-state cache)

# validate the recode/weighting prototype
python pipeline/pums_milestone1.py

# deploy: Pages serves only site/. Commit the assembled HTML.
git add site/index.html && git commit -m "rebuild map" && git push
```

## Architecture

Two data tracks — keep them distinct:

1. **Current map data** (`data/byage_min.json`, `ratio_tables2.json`, `analysis_data.json`, `states_v2.json`) comes from **published ACS tables**. This is what the deployed map runs on today.
2. **PUMS rebuild** (`build_xwalk.py`, `build_pums_metro.py`) is the **future foundation** — microdata-based, validated through Milestone 2, but **not yet wired into the map** (that's M3/M4). Don't conflate its output with the live map data.

Flow: `pipeline/` scripts hit the Census API → write `data/*.json` → `build_map.py` injects them into `pipeline/template/singles_age2_template.html` → `site/index.html`. **Python never runs on GitHub Pages** — it's local build tooling. Only `site/*.html` deploys.

## Conventions & definitions (keep consistent everywhere)

- **"Single"** = not currently married → `MAR ≠ 1` (never-married + divorced + separated + widowed).
- **Age bands**: 5-year, ages 20–64 → 9 bands. PUMS `AGEP` is continuous; published tables are pre-banded.
- **Race**: `RAC1P` + `HISP` → White (non-Hispanic), Black, Asian, Hispanic (any race), Two+. Currently overlapping "alone" definitions (matches published tables); switching to mutually-exclusive is an open decision.
- **Education**: `ba_plus = SCHL ≥ 21`.
- **PUMS**: no all-US API call — loop by state (`in=state:XX`). Record key = `SERIALNO` + `SPORDER` (needed to join replicate-weight chunks). Geography is PUMA → allocate to metro via `data/puma_cbsa_xwalk.json` (currently proportional, total-population weighted).
- **Published B12002, single by band i (0–8):** male NM `6+i`, SEP `38+i`, WID `68+i`, DIV `83+i`; female NM `99+i`, SEP `131+i`, WID `161+i`, DIV `176+i`. Race iterations B12002H/B/D/I/G (White/Black/Asian/Hispanic/Two+), single vars male `3,5,6,7` female `9,11,12,13` — **all-ages 15+ only, no age detail** (why the published-data race filter can't respect the age slider; PUMS fixes this).

## Map build pattern

Inject the four JSON files into the template placeholders (`__DATA__`, `__STATES__`, `__RTABLES__`, `__ANALYSIS__`), then validate: extract the `<script>` and `node --check` it, and re-verify the embedded data before shipping. `build_map.py` does all of this.

## Gotchas

- **Basemap:** use `data/states_v2.json` (us-atlas 10m, 51 features). An earlier PublicaMundi GeoJSON had a malformed zero-height ring in Virginia that d3 rendered as the complement, flooding the map. **Do not reintroduce the old basemap.**
- **City insets:** NYC, LA, Long Beach, Anaheim are shown as Census *places* (dashed outline + on-hover geography caveat), not metros. In `byage_min.json` they *replace* the LA/NY MSA circles. Places don't map cleanly to PUMAs — under the PUMS rebuild, keep published-table city numbers.
- **`build_xwalk.py` intermediates** (`tract_puma.csv`, `deli.xlsx`, `tract_pop.json`) are downloaded/cached and git-ignored — first run is slow.

## Status & next

PUMS Milestones 1 (recode/weighting) and 2 (geography) are done and validated; details in `docs/pums-milestone1-results.md` / `pums-milestone2-results.md`. **Next: Milestone 3** — the economic dimension (`ESR`/`PINCP`/`WAGP` → employed/income-filtered single men per metro, the competitive differentiator) plus race×age×metro, education×age×metro, and per-metro MOE; switch ingest to bulk CSV at that point. Open decisions are listed in `docs/PROJECT_STATE.md`.

## Docs

- `docs/PROJECT_STATE.md` — living status, full file inventory, open decisions (**update each session**).
- `docs/pums-pipeline-sketch.md` — PUMS rebuild design.
- `docs/pums-milestone1-results.md`, `docs/pums-milestone2-results.md` — validation records.
- `docs/singles-map-ideas-backlog.md` — feature roadmap.
- `docs/singles-map-competitive-analysis.md` — competitive landscape & differentiation.
