"""Market cores: functional dating-market geographies for the giant conurbations.

CBSA boundaries mislead exactly where the map matters most: the NY metro dilutes
its dating core with suburbs, while the SF metro EXCLUDES San Jose — one Caltrain/
app-radius market split across two CBSAs (see docs/market-geography-ny-vs-sf.md).
This builds county-group cores from PUMA-level aggregates (2020 PUMAs nest inside
these urban counties), giving each core the same data surface a metro has:

  - m3-style band cross-tabs (base / race / edu / emp / inc) + SDR MOEs -> tables & lenses
  - age-style single-year arrays (+ sparse race / edu cells)            -> seeker mode

Replicate weights are linear, so county-group sums keep exact SDR MOEs — the same
guarantee the CBSA allocation has. Race/edu cells here are 1-year (no 5-year splice
yet); they carry wider margins than the metro race views and say so in meta.

Reuses build_pums_bulk / build_pums_age state caches. Needs pipeline/pums_cache/
tract2puma.txt (2020_Census_Tract_to_2020_PUMA.txt from www2.census.gov rel2020).
Output: data/market_cores.json. Run: build_market_cores.py (CA + NY zips only).
"""
import json, sys
import numpy as np, pandas as pd
import build_pums_bulk as B
import build_pums_age as A

CORES = {
    'C_NYC':  {'n': 'New York City', 'full': 'New York City core (5 boroughs), NY',
               'lon': -73.9387, 'lat': 40.6627,
               'counties': ['36061', '36047', '36081', '36005', '36085']},
    'C_SFP':  {'n': 'SF Corridor', 'full': 'San Francisco–Peninsula–South Bay core, CA',
               'lon': -122.2000, 'lat': 37.5500,
               'counties': ['06075', '06081', '06085']},
    'C_EBAY': {'n': 'East Bay', 'full': 'East Bay core (Alameda–Contra Costa), CA',
               'lon': -122.0500, 'lat': 37.8500,
               'counties': ['06001', '06013']},
    'C_LAC':  {'n': 'Los Angeles County', 'full': 'Los Angeles County core, CA',
               'lon': -118.3300, 'lat': 34.0500,
               'counties': ['06037']},
    'C_OC':   {'n': 'Orange County', 'full': 'Orange County core, CA',
               'lon': -117.8500, 'lat': 33.7200,
               'counties': ['06059']},
}
STATES = ['06', '36']               # every core county lives in CA or NY
SPARSE_FLOOR = A.SPARSE_FLOOR

def puma_county_map():
    """pkey (state+puma) -> county fips, from the 2020 tract->PUMA relationship.
    Urban PUMAs nest in counties; a spanning PUMA goes to its modal county."""
    rel = pd.read_csv(B.CACHE / 'tract2puma.txt', dtype=str, encoding='utf-8-sig')
    rel['cty'] = rel.STATEFP + rel.COUNTYFP
    rel['pkey'] = rel.STATEFP + rel.PUMA5CE
    span = rel.groupby('pkey').cty.nunique()
    target = {c for core in CORES.values() for c in core['counties']}
    p2c = rel.groupby('pkey').cty.agg(lambda s: s.mode().iloc[0])
    bad = [p for p, c in p2c.items() if c in target and span[p] > 1]
    if bad:
        print(f'warning: {len(bad)} PUMAs span a core county + another: {bad}')
    return p2c

def sdr(frame, dims):
    """Sum a PUMA-filtered weight frame over `dims`, adding est + 90% MOE."""
    out = frame.groupby(dims, observed=True)[B.WCOLS].sum()
    X = out['PWGTP'].to_numpy()
    reps = out[[f'PWGTP{i}' for i in range(1, 81)]].to_numpy()
    se = np.sqrt(0.05 * ((reps - X[:, None]) ** 2).sum(axis=1))
    res = out[['PWGTP']].rename(columns={'PWGTP': 'est'})
    res['moe'] = 1.645 * se
    return res.reset_index()

def bands_node(f, extra=None):
    """rows (band,sex[,extra],est,moe) -> m3-shaped node (arrays of 9)."""
    def blank(): return {'m': [0]*9, 'w': [0]*9, 'm_moe': [0]*9, 'w_moe': [0]*9}
    if extra is None:
        rec = blank()
        for _, r in f.iterrows():
            rec[r.sex][int(r.band)] = round(r.est); rec[f'{r.sex}_moe'][int(r.band)] = round(r.moe)
        return rec
    node = {}
    for cat, s in f.groupby(extra, observed=True):
        rec = blank()
        for _, r in s.iterrows():
            rec[r.sex][int(r.band)] = round(r.est); rec[f'{r.sex}_moe'][int(r.band)] = round(r.moe)
        node[str(cat)] = rec
    return node

def main():
    p2c = puma_county_map()
    m3p, agep = {}, {}
    for st in STATES:
        m3p[st] = B.aggregate_state(st)            # band-level: base/race/edu/emp/inc
        agep[st] = A.aggregate_age_state(st)       # AGEP x sex x race x ba
        print(f'{st} aggregated')
    m3 = {k: pd.concat([m3p[st][k] for st in STATES]) for k in ('base','race','edu','emp','inc')}
    age = pd.concat([agep[st] for st in STATES])

    cores = {}
    for key, spec in CORES.items():
        pumas = {p for p, c in p2c.items() if c in set(spec['counties'])}
        sel = lambda f: f[f.index.get_level_values('pkey').isin(pumas)]

        node = {'base': bands_node(sdr(sel(m3['base']), ['band','sex'])),
                'race': bands_node(sdr(sel(m3['race']), ['band','sex','race']), 'race'),
                'edu':  bands_node(sdr(sel(m3['edu']),  ['band','sex','ba']),  'ba'),
                'emp':  bands_node(sdr(sel(m3['emp']),  ['band','sex'])),
                'inc':  bands_node(sdr(sel(m3['inc']),  ['band','sex','incb']), 'incb')}

        a = sel(age)
        an = {'m': [0]*45, 'w': [0]*45, 'm_moe': [0]*45, 'w_moe': [0]*45}
        for _, r in sdr(a, ['AGEP','sex']).iterrows():
            i = int(r.AGEP) - 20
            if 0 <= i < 45:
                an[r.sex][i] = round(r.est); an[f'{r.sex}_moe'][i] = round(r.moe)
        for grp, dims, col in (('race', ['AGEP','sex','race'], 'race'),
                               ('edu',  ['AGEP','sex','ba'],   'ba')):
            for _, r in sdr(a, dims).iterrows():
                i = int(r.AGEP) - 20
                est = round(r.est)
                if not (0 <= i < 45) or est < SPARSE_FLOOR: continue
                an.setdefault(grp, {}).setdefault(getattr(r, col), {}) \
                  .setdefault(r.sex, {})[int(r.AGEP)] = [est, round(r.moe)]

        cores[key] = {**spec, 'm': node['base']['m'], 'w': node['base']['w'],
                      'm3': node, 'age': an, 'pumas': len(pumas)}
        t20_64 = sum(node['base']['m']) + sum(node['base']['w'])
        print(f"  {spec['n']:22s} {len(pumas):3d} PUMAs  single 20-64: {t20_64:,.0f}")

    out = {'meta': {'vintage': f'{B.config.VINTAGE} PUMS',
                    'note': 'county-group functional dating-market cores from PUMA aggregates; '
                            'same shapes as pums_metro_m3 (m3) and pums_metro_age (age); '
                            'SDR 90% MOEs exact under linear county-group sums. Race/edu '
                            'cells are 1-YEAR (no 5-yr splice) — wider margins than the '
                            'metro race views.',
                    'geometry': '2020 PUMAs nested in counties via the tract->PUMA rel file'},
           'cores': cores}
    json.dump(out, open(B.DATA / 'market_cores.json', 'w'))
    print(f'wrote market_cores.json ({len(cores)} cores)')

    # sanity: NYC core single 20-64 should sit near the published NYC-city inset total
    by = json.load(open(B.DATA / 'byage_min.json'))
    nyc = next(m for m in by['metros'] if m.get('city') and m['n'] == 'New York')
    pub = sum(nyc['m']) + sum(nyc['w'])
    got = sum(cores['C_NYC']['m']) + sum(cores['C_NYC']['w'])
    print(f'  NYC check: core {got:,.0f} vs published city inset {pub:,.0f} '
          f'({100*(got-pub)/pub:+.2f}%)')

if __name__ == '__main__':
    main()
