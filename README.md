# Singles Map

**🔗 Live site: https://sage-mochi.github.io/singles-map/**

An interactive, **city/metro-level** tool that singles — particularly young men — can use to compare U.S. metros by single-population sex ratio, built on U.S. Census ACS data. An evolution of the 2007 National Geographic / Richard Florida "Singles Map," rebuilt on current data with explicit margins of error.

**Framing:** geography is the *secondary* lever — age and life stage move the single sex ratio far more than which metro you pick, and every metro still has more single men than women at 25–34. The tool surfaces the few places the ratio measurably tilts favorably; it does not promise that moving transforms anyone's odds.

## What's served

The site is static, self-contained HTML (data baked in — no runtime fetches, no backend):

- `site/index.html` — the main map: metro circles by single-gender gap, age slider (5-yr bands 20–64), three sizing modes, race filter, ranked tables, and an analysis section.
- `site/three-cities.html`, `site/bay-area.html` — supporting demographic visualizations.

## Repo structure

```
site/        the ONLY thing GitHub Pages serves (assembled HTML)
pipeline/    build tooling — runs locally, never on Pages
  build_map.py            assemble site/index.html from template + data
  build_xwalk.py          build the PUMA→CBSA crosswalk (PUMS rebuild)
  build_pums_metro.py     PUMS → metro single counts, reconciled vs published
  pums_milestone1.py      PUMS recode/weighting validation + cross-tab demos
  template/singles_age2_template.html
data/        generated, version-controlled (small JSON the map embeds)
docs/        markdown docs (PROJECT_STATE.md is the keystone)
```

## Setup

```bash
pip install pandas requests openpyxl
export CENSUS_API_KEY=your_free_key   # https://api.census.gov/data/key_signup.html
# node is optional — only used for a JS syntax check during the map build
```

The Census API key is read from the `CENSUS_API_KEY` environment variable. Do not hardcode or commit it.

## Rebuild the map

The deployed map is assembled by injecting `data/*.json` into the template:

```bash
python pipeline/build_map.py        # writes site/index.html
```

It fills the template's `__DATA__` / `__STATES__` / `__RTABLES__` / `__ANALYSIS__` placeholders, checks none remain, runs `node --check` on the embedded script (if node is present), and re-verifies the embedded marker count.

## Regenerate the data

Two tracks:

**Current map data** (`byage_min.json`, `ratio_tables2.json`, `analysis_data.json`, `states_v2.json`) comes from the published ACS tables. These are committed; regenerate only when moving to a new ACS year.

**PUMS rebuild** (in progress — the future data foundation, validated through Milestone 2):

```bash
python pipeline/build_xwalk.py        # → data/puma_cbsa_xwalk.json
python pipeline/build_pums_metro.py   # → data/pums_metro_singles.json (reconciles vs published)
```

`build_xwalk.py` downloads and caches large reference files (tract→PUMA, OMB delineation, 2020 tract populations); `build_pums_metro.py` caches PUMS per state under `pipeline/pums_cache/`. All of these are git-ignored — they re-download/regenerate. The PUMS output is validated but not yet wired into the map (that's Milestone 3/4).

## Deploy (GitHub Pages)

Pages serves static files only and never runs Python. Deploy = commit the assembled HTML:

```bash
python pipeline/build_map.py
git add site/index.html && git commit -m "rebuild map" && git push
```

Point Pages at the `site/` folder (Settings → Pages → Source). Optionally, a GitHub Actions workflow can run the pipeline on a schedule (API key as a repo secret) and commit a refreshed `site/index.html` when new ACS data lands — the automated version of the "annually refreshing" advantage.

## Data & methodology

- **Source:** 2024 ACS 1-year (tables + PUMS microdata).
- **"Single"** = not currently married (`MAR ≠ 1`: never-married + divorced + separated + widowed).
- **Age bands:** 5-year, 20–64.
- **Margins of error** for custom PUMS cross-tabs use successive-difference replication over the 80 replicate weights: `SE = sqrt(0.05·Σ(Xr−X)²)`, `MOE90 = 1.645·SE`.
- **Validation:** PUMS recode/weighting matches published tables to ±0.1% at state level; metro allocation matches to +0.02% nationally and ±0.3% for large metros. Details in `docs/pums-milestone1-results.md` and `docs/pums-milestone2-results.md`.

## Docs

- `docs/PROJECT_STATE.md` — **start here**: status, file inventory, conventions, open decisions, next steps.
- `docs/pums-pipeline-sketch.md` — PUMS rebuild design.
- `docs/pums-milestone1-results.md`, `docs/pums-milestone2-results.md` — validation records.
- `docs/singles-map-ideas-backlog.md` — feature roadmap.
- `docs/singles-map-competitive-analysis.md` — competitive landscape and differentiation.
```
