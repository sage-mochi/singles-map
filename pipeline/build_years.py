"""Roadmap #3 (time slider): historical B12002 single counts per metro, 2006-2023.

Fetches published ACS 1-year B12002 (sex x marital status x age) for every metro
and the 4 city insets, for each year 2006..2023 (2020 skipped: no standard 1-year
ACS). 2024 is not stored -- the live map already carries it (byage_min) -- but IS
fetched for validation: this script's 2024 numbers must match byage_min exactly.

Estimates: single = never-married + separated + widowed + divorced, 9 five-year
bands 20-64, by sex. MOE: RSS over the four components' published MOEs (the
standard Census approximation for a sum).

Cross-year geography: metros are matched to the 2024 map list by CBSA code, then
by a small alias map (codes that changed across delineations, e.g. LA 31100->
31080), then by normalized first-principal-city + first-state name matching
(historic metro names drift, e.g. the 2006 New York MSA title). Metros without
1-year data in a given year (below 65K population, or delineation gaps like
Waterbury 2013-2022) get null rows -- the UI shows "no 1-yr estimate".

Variable numbering is stable across vintages (verified via the keyless variables
metadata): --verify-labels asserts, for every year, that each of the 144 vars
used carries the expected sex/status/age label before any data are trusted.

Requires CENSUS_API_KEY (data endpoints; metadata is keyless). Raw per-year
responses cache under pipeline/pums_cache/. Output: data/years_min.json.
"""
import json, os, re, sys, time
from pathlib import Path
import requests
import config

ROOT  = Path(__file__).resolve().parent.parent
DATA  = ROOT / 'data'
CACHE = ROOT / 'pipeline' / 'pums_cache'
CACHE.mkdir(parents=True, exist_ok=True)

KEY   = os.environ.get('CENSUS_API_KEY')
CUR   = config.ACS_YEAR                 # the current vintage (carried by the live map, validated here)
YEARS = config.HISTORY_YEARS            # historical years stored in years_min.json
MSA_GEO = 'metropolitan statistical area/micropolitan statistical area'
# city insets: (state fips, place fips) -> byage_min name
PLACES = {('36','51000'):'New York', ('06','44000'):'Los Angeles',
          ('06','43000'):'Long Beach', ('06','02000'):'Anaheim'}
# CBSA codes that changed across delineations: 2024 code -> historic code
ALIAS = {'31080':'31100'}                     # Los Angeles (2013 delineation)

# B12002 single-component offsets, band i=0..8 (20-24..60-64)
M_OFF = [6, 38, 68, 83]                       # male: NM, SEP, WID, DIV
W_OFF = [99, 131, 161, 176]                   # female
STATUS = {6:'Never married', 38:'Separated', 68:'Widowed', 83:'Divorced',
          99:'Never married', 131:'Separated', 161:'Widowed', 176:'Divorced'}
BANDS9 = ['20 to 24','25 to 29','30 to 34','35 to 39','40 to 44',
          '45 to 49','50 to 54','55 to 59','60 to 64']
EVARS = [f'B12002_{o+i:03d}E' for o in M_OFF+W_OFF for i in range(9)]
MVARS = [v[:-1]+'M' for v in EVARS]

def get(url, params, tries=5):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=180)
            if r.status_code == 200: return r.json()
            if r.status_code == 204: return None          # no content for geography
        except Exception:
            pass
        time.sleep(2*(k+1))
    raise RuntimeError(f'failed: {url} {str(params)[:120]}')

def verify_labels(year):
    """Keyless assertion that every var used carries the expected label this year."""
    js = get(f'https://api.census.gov/data/{year}/acs/acs1/variables.json', {})
    vs = js['variables']
    for offs, sex in ((M_OFF,'Male'), (W_OFF,'Female')):
        for o in offs:
            for i, band in enumerate(BANDS9):
                lab = vs[f'B12002_{o+i:03d}E']['label'].replace(':','')
                assert sex in lab and STATUS[o] in lab and band in lab, \
                    f'{year} B12002_{o+i:03d}E label mismatch: {lab}'
    return True

def fetch_year(year):
    """-> {'metros': {cbsa: row}, 'cities': {name: row}, 'names': {cbsa: NAME}}
    row = {'m':[9],'w':[9],'m_moe':[9],'w_moe':[9]}"""
    fn = CACHE / f'years_raw_{year}.json'
    if fn.exists():
        return json.load(open(fn))
    if not KEY:
        raise SystemExit('Set CENSUS_API_KEY (free: https://api.census.gov/data/key_signup.html)')
    base = f'https://api.census.gov/data/{year}/acs/acs1'
    allv = EVARS + MVARS
    chunks = [allv[i:i+45] for i in range(0, len(allv), 45)]

    def pull(geo_for, geo_in=None):
        merged = {}
        for ch in chunks:
            p = {'get': 'NAME,' + ','.join(ch), 'for': geo_for, 'key': KEY}
            if geo_in: p['in'] = geo_in
            rows = get(base, p)
            if not rows: return {}
            hdr = {h: j for j, h in enumerate(rows[0])}
            gcols = [h for h in rows[0] if h not in ch and h != 'NAME']
            for row in rows[1:]:
                gid = '|'.join(row[hdr[c]] for c in gcols)
                d = merged.setdefault(gid, {'NAME': row[hdr['NAME']]})
                for v in ch:
                    d[v] = row[hdr[v]]
        return merged

    def build(rec):
        out = {'m':[0]*9, 'w':[0]*9, 'm_moe':[0]*9, 'w_moe':[0]*9}
        for offs, sk in ((M_OFF,'m'), (W_OFF,'w')):
            for i in range(9):
                e = sum(max(int(float(rec.get(f'B12002_{o+i:03d}E') or 0)), 0) for o in offs)
                v = sum(max(float(rec.get(f'B12002_{o+i:03d}M') or 0), 0)**2 for o in offs)
                out[sk][i] = e
                out[f'{sk}_moe'][i] = round(v**0.5)
        return out

    metros = pull(f'{MSA_GEO}:*')
    out = {'metros': {}, 'cities': {}, 'names': {}}
    for gid, rec in metros.items():
        out['metros'][gid] = build(rec)
        out['names'][gid] = rec['NAME']
    for (st, pl), nm in PLACES.items():
        got = pull(f'place:{pl}', f'state:{st}')
        for gid, rec in got.items():
            out['cities'][nm] = build(rec)
    json.dump(out, open(fn, 'w'))
    return out

NORM_RE = re.compile(r'[-–,/].*$')
def normname(full):
    """'New York-Northern New Jersey..., NY-NJ-PA Metro Area' -> 'new york|NY'"""
    full = re.sub(r'\s+(Metro|Micro) Area$', '', full.strip())
    city = NORM_RE.sub('', full).strip().lower()
    st = full.split(',')[-1].strip().split('-')[0].strip()[:2]
    return f'{city}|{st}'

def main():
    if '--verify-labels' in sys.argv:
        for y in YEARS + [CUR]:
            verify_labels(y); print(f'{y}: labels OK')
        print('all vintages verified: fixed offsets are safe')
        return

    byage = json.load(open(DATA / 'byage_min.json'))
    targets = [m for m in byage['metros'] if m.get('code') and not m.get('pumsOnly')]
    t_codes = {m['code'] for m in targets}
    for y in YEARS + [CUR]:
        verify_labels(y)
    print('labels verified for all years.')

    out = {'years': YEARS,
           'meta': {'latest': CUR,       # current vintage the live map carries (UI appends it)
                    'source': 'ACS 1-year B12002 per vintage; single = NM+SEP+WID+DIV; '
                              'MOE = RSS over the four components (90%).',
                    'note': '2020 skipped (no standard 1-year ACS); null = no 1-yr '
                            'estimate that year (pop<65K or delineation gap).'},
           'metros': {c: {'m':[], 'w':[], 'm_moe':[], 'w_moe':[]} for c in t_codes},
           'cities': {nm: {'m':[], 'w':[], 'm_moe':[], 'w_moe':[]} for nm in PLACES.values()}}

    for y in YEARS:
        yr = fetch_year(y)
        byname = {normname(nm): gid for gid, nm in yr['names'].items()}
        matched = 0
        for t in targets:
            code = t['code']
            row = (yr['metros'].get(code) or yr['metros'].get(ALIAS.get(code, ''))
                   or yr['metros'].get(byname.get(normname(t['full']), '')))
            node = out['metros'][code]
            for k in ('m','w','m_moe','w_moe'):
                node[k].append(row[k] if row else None)
            if row: matched += 1
        for nm in PLACES.values():
            row = yr['cities'].get(nm)
            node = out['cities'][nm]
            for k in ('m','w','m_moe','w_moe'):
                node[k].append(row[k] if row else None)
        print(f'{y}: matched {matched}/{len(targets)} metros, '
              f'{sum(1 for nm in PLACES.values() if yr["cities"].get(nm))}/4 cities')

    # ---- validation ----
    print(f'\n=== validation: {CUR} fetch must reproduce byage_min exactly ===')
    ycur = fetch_year(CUR)
    worst = 0; checked = 0
    for t in targets:
        row = ycur['metros'].get(t['code'])
        if not row: continue
        checked += 1
        for i in range(9):
            worst = max(worst, abs(row['m'][i]-t['m'][i]), abs(row['w'][i]-t['w'][i]))
    print(f'  {checked} metros checked, max |cell diff| = {worst}  (expect 0)')

    ny = out['metros'].get('35620')
    if ny and ny['m'][0]:
        gap06 = sum(ny['w'][0]) - sum(ny['m'][0])
        print(f'  NY metro 2006 female surplus (20-64): {gap06:+,} (analysis card says ~+211K)')

    json.dump(out, open(DATA / 'years_min.json', 'w'))
    import os as _os
    print(f'\nwrote years_min.json ({_os.path.getsize(DATA/"years_min.json")//1024} KB, '
          f'{len(YEARS)} years x {len(t_codes)} metros + {len(PLACES)} cities)')

if __name__ == '__main__':
    main()
