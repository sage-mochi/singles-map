"""Single source of truth for data vintages. Bump ACS_YEAR each fall when the new
ACS 1-year release lands (~September), then follow docs/REFRESH.md.

What each constant tracks, and how often it moves:
- ACS_YEAR    : the current ACS 1-year vintage. Bump ANNUALLY. Drives every 1-year
                data pull (published tables + PUMS microdata) and the map's "latest".
- GAZ_YEAR    : the CBSA Gazetteer vintage (metro centroids/names). Tracks ACS_YEAR.
- DECENNIAL   : tract populations + PUMA vintage used by the crosswalk. Bumps once a
                DECADE (next: 2030, when 2020-based PUMAs are retired). Not annual.
- HISTORY_*   : the time-slider range. Historical years are everything with metro
                1-year B12002 up to (not including) ACS_YEAR; 2020 has no standard
                1-year ACS. The current year itself is carried by the live map data
                (byage_min / M3), so it is appended in the UI, not stored in history.

Validation anchors are deliberately NOT here: numbers like the national single
20-64 total or a metro's reconciliation target are vintage-specific truths that
must be re-derived, not blindly bumped. The runbook shows the one-liners.
"""

ACS_YEAR      = 2024
GAZ_YEAR      = 2024
DECENNIAL     = 2020
HISTORY_START = 2006
SKIP_YEARS    = {2020}          # years with no standard ACS 1-year release

# Derived endpoints / ranges — import these, don't re-spell the URLs.
ACS1_API       = f'https://api.census.gov/data/{ACS_YEAR}/acs/acs1'
PUMS_API       = f'{ACS1_API}/pums'
PUMS_BULK_BASE = f'https://www2.census.gov/programs-surveys/acs/data/pums/{ACS_YEAR}/1-Year'
# 5-year PUMS: same directory shape (csv_p{ab}.zip), ~5x the sample. Used ONLY for
# the thin race cross-tabs (build_pums_bulk/age --5yr), where 1-year cells are too
# noisy; base/econ/edu/seeker stay 1-year for currency. The 2020-2024 5-year is the
# first to use 2020 PUMAs exclusively, so the existing crosswalk applies unchanged.
PUMS5_BULK_BASE = f'https://www2.census.gov/programs-surveys/acs/data/pums/{ACS_YEAR}/5-Year'
ACS5_START      = ACS_YEAR - 4
ACS5_RANGE      = f'{ACS5_START}–{ACS_YEAR}'          # e.g. 2020–2024
VINTAGE5        = f'ACS {ACS5_RANGE} 5-year'
GAZ_URL        = (f'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/'
                  f'{GAZ_YEAR}_Gazetteer/{GAZ_YEAR}_Gaz_cbsa_national.zip')
GAZ_TXT        = f'{GAZ_YEAR}_Gaz_cbsa_national.txt'
DECENNIAL_PL   = f'https://api.census.gov/data/{DECENNIAL}/dec/pl'
HISTORY_YEARS  = [y for y in range(HISTORY_START, ACS_YEAR) if y not in SKIP_YEARS]
VINTAGE        = f'ACS {ACS_YEAR} 1-year'
