"""M4 join step: bake a CBSA code onto each map metro in data/byage_min.json.

The map's geography track (byage_min.json) is keyed by metro *name* + lon/lat; the
PUMS rebuild (pums_metro_m3.json) is keyed by *CBSA code*. To wire M3's cross-tabs
into the map we need a join key, so this assigns each metro its CBSA `code` from the
Census CBSA Gazetteer (name match, coordinate fallback). City insets (Census places,
not CBSAs) get `code: null` and keep published-table numbers.

Run once; the result is committed (additive `code` field), so build_map.py stays
offline. Re-runnable / idempotent. Reports M3 coverage. Gazetteer is cached under
pipeline/pums_cache/ (git-ignored).
"""
import csv, json, math, re, sys
from pathlib import Path
import requests
import config

ROOT  = Path(__file__).resolve().parent.parent
DATA  = ROOT / 'data'
CACHE = ROOT / 'pipeline' / 'pums_cache'
CACHE.mkdir(parents=True, exist_ok=True)

def load_gazetteer():
    import io, zipfile
    txt = CACHE / config.GAZ_TXT
    if not txt.exists():
        r = requests.get(config.GAZ_URL, timeout=120); r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        member = next(n for n in z.namelist() if n.lower().endswith('.txt'))
        txt.write_bytes(z.read(member))
    gaz = {}
    with open(txt, encoding='latin-1') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            row = {k.strip(): v for k, v in row.items()}
            name = re.sub(r' (Metro|Micro) Area$', '', row['NAME']).strip()
            gaz[row['GEOID']] = {'name': name,
                                 'lat': float(row['INTPTLAT']),
                                 'lon': float(row['INTPTLONG'])}
    return gaz

def main():
    gaz = load_gazetteer()
    name2code = {g['name']: c for c, g in gaz.items()}
    d = json.load(open(DATA / 'byage_min.json'))
    d['metros'] = [m for m in d['metros'] if not m.get('pumsOnly')]  # idempotent re-run
    m3full = json.load(open(DATA / 'pums_metro_m3.json'))
    m3 = set(m3full['metros'])

    def nearest(m):
        return min(gaz, key=lambda c: (gaz[c]['lat']-m['lat'])**2 + (gaz[c]['lon']-m['lon'])**2)

    real = covered = name_hit = coord_hit = 0
    no_m3 = []
    for m in d['metros']:
        if m.get('city'):
            m['code'] = None                       # Census place, not a CBSA
            continue
        real += 1
        code = name2code.get(m['full'])
        if code: name_hit += 1
        else:    code = nearest(m); coord_hit += 1
        m['code'] = code
        if code in m3: covered += 1
        else:          no_m3.append(m['full'])

    # NY (35620) and LA (31080) appear on the map only as city-place insets
    # (code:null), so they have no PUMS view. Add metro-level circles that the map
    # renders ONLY under the PUMS lenses (pumsOnly flag); the base/age view keeps
    # the insets. m/w are the M3 base counts (so hover / any base render work).
    PUMS_ONLY = {'35620': 'New York', '31080': 'Los Angeles'}
    for code, short in PUMS_ONLY.items():
        g, base = gaz[code], m3full['metros'][code]['base']
        d['metros'].append({'n': short, 'full': g['name'], 'code': code,
                            'lon': g['lon'], 'lat': g['lat'], 'pumsOnly': True,
                            'm': base['m'], 'w': base['w']})

    json.dump(d, open(DATA / 'byage_min.json', 'w'))
    print(f'metros: {len(d["metros"])}  ({real} real + {len(d["metros"])-real-len(PUMS_ONLY)} city insets + {len(PUMS_ONLY)} pums-only)')
    print(f'pums-only metro circles: {list(PUMS_ONLY.values())}')
    print(f'CBSA code assigned: {name_hit} by name, {coord_hit} by coordinate fallback')
    print(f'covered by M3 cross-tabs: {covered}/{real}')
    if no_m3:
        print(f'no M3 data ({len(no_m3)}) -> published-table fallback: {no_m3}')

if __name__ == '__main__':
    main()
