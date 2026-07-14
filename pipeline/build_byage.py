"""Reconstructed published-table builder -> data/byage_min.json (the base map).

This was the one pipeline stage missing from the repo (docs/REFRESH.md #2): the base
map runs on PUBLISHED ACS B12002 and its generator was gone, so a vintage bump could
not regenerate it. This rebuilds it from scratch and validates it reproduces the
committed file exactly.

Selection rule (self-maintaining — new metros appear automatically): every
METROPOLITAN (not micro) non-PR MSA that has non-null 1-year B12002 this vintage.
Empirically the metros without 1-year detailed data return null and drop out; NY
(35620), LA (31080) and SF (41860) are pulled out and REPLACED by 6 Census-place
insets (New York / Los Angeles / Long Beach / Anaheim / San Francisco / Oakland
cities), which don't map to PUMAs. attach_cbsa.py then re-adds `code` + the
NY/LA/SF pumsOnly circles.

Per metro: n, full, lon/lat (CBSA Gazetteer; place Gazetteer for insets), m[9]/w[9]
single by 5-yr band 20-64, and race{White/Black/Asian/Hispanic/Two+} = all-ages-15+
single m/w from the B12002 race iterations (H/B/D/I/G). "single" = NM+SEP+WID+DIV.
natM/natW = national single by band.

NOT regenerated here: analysis_data.json (the decomposition chart + pctmarried). Its
decomp uses an original methodology not recovered; it stays a hand-maintained artifact
(see REFRESH.md). Run: build_byage.py -> attach_cbsa.py -> build_map.py.
"""
import csv, io, json, os, re, time, zipfile
from pathlib import Path
import requests
import config

ROOT  = Path(__file__).resolve().parent.parent
DATA  = ROOT / 'data'
CACHE = ROOT / 'pipeline' / 'pums_cache'
KEY   = os.environ.get('CENSUS_API_KEY')
if not KEY:
    print('warning: CENSUS_API_KEY not set — running keyless (Census allows ~500 anonymous '
          'queries/day per IP; this build makes ~50). Get a free key: '
          'https://api.census.gov/data/key_signup.html')
MSA_GEO = 'metropolitan statistical area/micropolitan statistical area'

M_OFF = [6, 38, 68, 83]        # B12002 single male: NM, SEP, WID, DIV; band i -> off+i
W_OFF = [99, 131, 161, 176]    # female
RACE_TBL = {'White':'B12002H','Black':'B12002B','Asian':'B12002D',
            'Hispanic':'B12002I','Two or more':'B12002G'}
RACE_M = [3,5,6,7]; RACE_W = [9,11,12,13]     # race-iteration all-ages single vars
PLACE_FULL = {'New York':'New York city, NY','Los Angeles':'Los Angeles city, CA',
              'Long Beach':'Long Beach city, CA','Anaheim':'Anaheim city, CA',
              'San Francisco':'San Francisco city, CA','Oakland':'Oakland city, CA'}
PLACES = {('36','51000'):'New York', ('06','44000'):'Los Angeles',
          ('06','43000'):'Long Beach', ('06','02000'):'Anaheim',
          ('06','67000'):'San Francisco', ('06','53000'):'Oakland'}
NULLS = (None, '', '-666666666', '-555555555', '-999999999')

def get(params, tries=5):
    for k in range(tries):
        try:
            r = requests.get(config.ACS1_API,
                             params={**params, 'key': KEY} if KEY else params, timeout=180)
            if r.status_code == 200: return r.json()
        except Exception: pass
        time.sleep(2*(k+1))
    raise RuntimeError(f'failed: {str(params)[:120]}')

def fetch_group(table, geo_for, geo_in=None):
    p = {'get': f'group({table})', 'for': geo_for}
    if geo_in: p['in'] = geo_in
    rows = get(p); idx = {h: i for i, h in enumerate(rows[0])}
    gcol = next(h for h in rows[0] if h in (MSA_GEO, 'place', 'us'))
    return {row[idx[gcol]]: {h: row[idx[h]] for h in rows[0]} for row in rows[1:]}

def single_bands(rec):
    def v(n):
        x = rec.get(f'B12002_{n:03d}E')
        return max(int(x), 0) if x not in NULLS else 0
    return ([sum(v(o+i) for o in M_OFF) for i in range(9)],
            [sum(v(o+i) for o in W_OFF) for i in range(9)])

def race_of(recs, code):
    out = {}
    for lab, tbl in RACE_TBL.items():
        rec = recs[lab].get(code)
        if not rec or rec.get(f'{tbl}_001E') in NULLS:   # no data for this race -> omit (matches original)
            continue
        def v(n):
            x = rec.get(f'{tbl}_{n:03d}E')
            return max(int(x), 0) if x not in NULLS else 0
        out[lab] = {'m': sum(v(n) for n in RACE_M), 'w': sum(v(n) for n in RACE_W)}
    return out

def load_cbsa_gaz():
    txt = CACHE / config.GAZ_TXT
    if not txt.exists():
        z = zipfile.ZipFile(io.BytesIO(requests.get(config.GAZ_URL, timeout=120).content))
        txt.write_bytes(z.read(next(n for n in z.namelist() if n.lower().endswith('.txt'))))
    gaz = {}
    for row in csv.DictReader(open(txt, encoding='latin-1'), delimiter='\t'):
        row = {k.strip(): v for k, v in row.items()}
        raw = row['NAME']
        gaz[row['GEOID']] = {'raw': raw, 'name': re.sub(r' (Metro|Micro) Area$', '', raw).strip(),
                             'lat': round(float(row['INTPTLAT']), 4), 'lon': round(float(row['INTPTLONG']), 4)}
    return gaz

def load_place_gaz():
    """place GEOID (state+place fips) -> lon/lat, for the city insets."""
    url = config.GAZ_URL.replace('_cbsa_', '_place_')
    txt = CACHE / config.GAZ_TXT.replace('_cbsa_', '_place_')
    if not txt.exists():
        z = zipfile.ZipFile(io.BytesIO(requests.get(url, timeout=180).content))
        txt.write_bytes(z.read(next(n for n in z.namelist() if n.lower().endswith('.txt'))))
    out = {}
    for row in csv.DictReader(open(txt, encoding='latin-1'), delimiter='\t'):
        row = {k.strip(): v for k, v in row.items()}
        out[row['GEOID']] = (round(float(row['INTPTLONG']), 4), round(float(row['INTPTLAT']), 4))
    return out

def main():
    gaz = load_cbsa_gaz(); pgaz = load_place_gaz()
    print('fetching B12002 for all MSAs ...')
    msa = fetch_group('B12002', f'{MSA_GEO}:*')
    sel = [c for c, rec in msa.items()
           if c in gaz and gaz[c]['raw'].endswith('Metro Area') and ', PR' not in gaz[c]['raw']
           and rec.get('B12002_001E') not in NULLS]
    print(f'selected: {len(sel)} metropolitan non-PR MSAs with data')

    print('fetching race iterations ...')
    race_recs = {lab: fetch_group(tbl, f'{MSA_GEO}:*') for lab, tbl in RACE_TBL.items()}

    metros = []
    for code in sorted(sel):
        if code in ('35620', '31080', '41860'): continue  # NY/LA/SF -> replaced by insets
        g = gaz[code]; m, w = single_bands(msa[code])
        metros.append({'n': g['name'].split(',')[0].split('-')[0].strip(), 'full': g['name'],
                       'lon': g['lon'], 'lat': g['lat'], 'm': m, 'w': w,
                       'race': race_of(race_recs, code)})

    print('fetching city insets ...')
    for (st, pl), name in PLACES.items():
        rec = fetch_group('B12002', f'place:{pl}', f'state:{st}')[pl]
        m, w = single_bands(rec)
        rr = {lab: fetch_group(tbl, f'place:{pl}', f'state:{st}') for lab, tbl in RACE_TBL.items()}
        lon, lat = pgaz[st + pl]
        metros.append({'n': name, 'full': PLACE_FULL[name], 'lon': lon, 'lat': lat,
                       'm': m, 'w': w, 'race': race_of(rr, pl), 'city': True})

    # natM/natW: single by band summed across the SELECTED metros — NY/LA/SF counted as
    # their full MSAs here (the flip chart's "summed across all metros"), not the insets.
    natM = [sum(single_bands(msa[c])[0][i] for c in sel) for i in range(9)]
    natW = [sum(single_bands(msa[c])[1][i] for c in sel) for i in range(9)]
    out = {'bands': ['20-24','25-29','30-34','35-39','40-44','45-49','50-54','55-59','60-64'],
           'metros': metros, 'natM': natM, 'natW': natW,
           'races': ['White','Black','Asian','Hispanic','Two or more']}
    json.dump(out, open(DATA / 'byage_min.json', 'w'))
    print(f'wrote byage_min.json ({len(metros)} metros incl 4 insets)')
    validate(out)

def validate(new):
    """Reproduce the committed byage_min exactly (ignoring attach_cbsa's added code/pumsOnly)."""
    ref = json.load(open(DATA / 'byage_min.json.ref')) if (DATA/'byage_min.json.ref').exists() else None
    import subprocess
    old = json.loads(subprocess.run(['git','show','HEAD:data/byage_min.json'],
                                    capture_output=True, text=True).stdout)
    oldreal = [m for m in old['metros'] if not m.get('pumsOnly')]
    print('\n=== validation vs committed (pre-pumsOnly) ===')
    print(f'  metros: new {len(new["metros"])}  committed {len(oldreal)}')
    print(f'  natM match: {new["natM"]==old["natM"]}   natW match: {new["natW"]==old["natW"]}')
    byfull = {m['full']: m for m in oldreal}
    worst = 0; miss = []; coordmiss = 0; racemiss = 0; nmiss = []
    for m in new['metros']:
        o = byfull.get(m['full'])
        if not o: miss.append(m['full']); continue
        for k in ('m','w'):
            worst = max(worst, max(abs(a-b) for a,b in zip(m[k], o[k])))
        if m.get('n') != o.get('n'): nmiss.append((m['n'], o.get('n')))
        if abs((m['lon'] or 0)-(o['lon'] or 0))>0.0001 or abs((m['lat'] or 0)-(o['lat'] or 0))>0.0001: coordmiss += 1
        if m['race'] != o['race']: racemiss += 1
    print(f'  max |m/w cell diff|: {worst}   (expect 0)')
    print(f'  coord mismatches: {coordmiss}   race mismatches: {racemiss}   name mismatches: {len(nmiss)}')
    if nmiss[:5]: print(f'    n examples: {nmiss[:5]}')
    if miss: print(f'  in new but not committed ({len(miss)}): {miss[:6]}')
    onlyold = [f for f in byfull if f not in {m['full'] for m in new['metros']}]
    if onlyold: print(f'  in committed but not new ({len(onlyold)}): {onlyold[:6]}')

if __name__ == '__main__':
    main()
