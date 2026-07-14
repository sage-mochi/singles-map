"""Radius-market layer: PUMA-level singles for the "Radius" boundary type.

Dating apps match by user-set radius, not by administrative boundary — so the
third boundary type treats the market as a FIELD: every PUMA gets a dot whose
market is everything within R miles of it (R is a user slider; the client sums
these PUMA nodes over great-circle neighbourhoods). This file ships, per PUMA:

  - base single m/w by single year of age (20-64) + SDR 90% MOE
  - the BA+ / no-BA split of the same (education seeker filter at radius)
  - a population-weighted centroid (2020 tract pops x tract internal points)
  - a display name (modal county + state)

Race cells are deliberately NOT shipped: single-race x single-year x PUMA 1-year
cells are noise; use metro/market boundaries for the race lens. Client-side
radius sums combine MOEs by root-sum-square across PUMAs (independence
approximation — the same one moeOf uses across bands; exact SDR would require
shipping 80 replicate weights per cell). The layer is LAZY-LOADED by the map
(fetched on first use), not baked into the HTML: ~8 MB raw / ~2 MB gzipped.

Reuses build_pums_age's per-state aggregate caches. Needs CENSUS_API_KEY for
the 2020 tract populations (one decennial PL call per state, cached).
Output: data/puma_radius.json. build_map.py copies it into site/.
"""
import csv, io, json, os, time, zipfile
import numpy as np, pandas as pd, requests
import build_pums_bulk as B
import build_pums_age as A

KEY = os.environ.get('CENSUS_API_KEY')
REPS = [f'PWGTP{i}' for i in range(1, 81)]

def get(url, params, tries=5):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=180)
            if r.status_code == 200: return r.json()
        except Exception: pass
        time.sleep(2 * (k + 1))
    raise RuntimeError(f'failed: {url} {str(params)[:80]}')

def tract_pops():
    """2020 tract population per 11-digit tract fips (cached)."""
    fn = B.CACHE / 'tract_pop2020.json'
    if fn.exists(): return json.load(open(fn))
    if not KEY: raise SystemExit('CENSUS_API_KEY required for tract populations')
    pop = {}
    for st in B.STATES:
        rows = get(B.config.DECENNIAL_PL,
                   {'get': 'P1_001N', 'for': 'tract:*', 'in': f'state:{st}', 'key': KEY})
        idx = {h: i for i, h in enumerate(rows[0])}
        for row in rows[1:]:
            pop[row[idx['state']] + row[idx['county']] + row[idx['tract']]] = int(row[idx['P1_001N']])
        print(st, end=' ', flush=True)
    print()
    json.dump(pop, open(fn, 'w'))
    return pop

def gaz(kind):
    """National gazetteer rows for tracts/counties (cached download)."""
    url = B.config.GAZ_URL.replace('_cbsa_', f'_{kind}_')
    txt = B.CACHE / B.config.GAZ_TXT.replace('_cbsa_', f'_{kind}_')
    if not txt.exists():
        r = requests.get(url, timeout=300, headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        txt.write_bytes(z.read(next(n for n in z.namelist() if n.lower().endswith('.txt'))))
    out = {}
    for row in csv.DictReader(open(txt, encoding='latin-1'), delimiter='\t'):
        row = {k.strip(): v for k, v in row.items()}
        out[row['GEOID']] = row
    return out

def centroids_and_names():
    """pkey -> (lon, lat, name): 2020-tract-pop-weighted centroid + modal county."""
    rel = pd.read_csv(B.CACHE / 'tract2puma.txt', dtype=str, encoding='utf-8-sig')
    rel['tract'] = rel.STATEFP + rel.COUNTYFP + rel.TRACTCE
    rel['pkey']  = rel.STATEFP + rel.PUMA5CE
    rel['cty']   = rel.STATEFP + rel.COUNTYFP
    pops   = tract_pops()
    tgaz   = gaz('tracts')
    cgaz   = gaz('counties')
    ST_UP  = {k: v.upper() for k, v in B.ST_AB.items()}
    rel['pop'] = rel.tract.map(pops).fillna(0)
    rel['lat'] = rel.tract.map(lambda t: float(tgaz[t]['INTPTLAT']) if t in tgaz else np.nan)
    rel['lon'] = rel.tract.map(lambda t: float(tgaz[t]['INTPTLONG']) if t in tgaz else np.nan)
    rel = rel.dropna(subset=['lat', 'lon'])
    out = {}
    for pk, g in rel.groupby('pkey'):
        w = g['pop'].to_numpy(float)
        if w.sum() <= 0: w = np.ones(len(g))
        lon = float(np.average(g.lon, weights=w)); lat = float(np.average(g.lat, weights=w))
        cty = g.groupby('cty')['pop'].sum().idxmax()
        cname = cgaz.get(cty, {}).get('NAME', 'Unknown County')
        out[pk] = (round(lon, 4), round(lat, 4), f'{cname}, {ST_UP.get(pk[:2], "")}')
    return out

def est_moe(frame, dims):
    out = frame.groupby(dims, observed=True)[B.WCOLS].sum()
    X = out['PWGTP'].to_numpy()
    reps = out[REPS].to_numpy()
    se = np.sqrt(0.05 * ((reps - X[:, None]) ** 2).sum(axis=1))
    res = out[['PWGTP']].rename(columns={'PWGTP': 'est'})
    res['moe'] = 1.645 * se
    return res.reset_index()

def fill(node, rows, mkey, wkey):
    """rows (pkey,AGEP,sex,est,moe) into dense per-puma arrays under mkey/wkey."""
    for _, r in rows.iterrows():
        i = int(r.AGEP) - 20
        if not (0 <= i < 45): continue
        p = node.setdefault(r.pkey, {})
        k = mkey if r.sex == 'm' else wkey
        arr = p.setdefault(k, [0]*45); arr[i] = round(r.est)
        arrm = p.setdefault(k + 'm', [0]*45); arrm[i] = round(r.moe)

def main():
    cen = centroids_and_names()
    print(f'centroids: {len(cen)} PUMAs')
    parts = []
    for i, st in enumerate(B.STATES):
        parts.append(A.aggregate_age_state(st))
        print(st, end=' ', flush=True)
        if (i + 1) % 13 == 0: print()
    print('\nstates aggregated.')
    full = pd.concat(parts)
    base = est_moe(full, ['pkey', 'AGEP', 'sex'])
    edu  = est_moe(full, ['pkey', 'AGEP', 'sex', 'ba'])

    node = {}
    fill(node, base, 'm_', 'w_')                     # m_ / m_m(oe), w_ / w_m
    fill(node, edu[edu.ba == 'ba'],   'eb_m', 'eb_w')
    fill(node, edu[edu.ba == 'noba'], 'en_m', 'en_w')

    pumas, miss = {}, 0
    for pk, d in node.items():
        if pk not in cen: miss += 1; continue
        lon, lat, name = cen[pk]
        pumas[pk] = {'name': name, 'lon': lon, 'lat': lat, **d}
    if miss: print(f'warning: {miss} PUMAs in PUMS without centroid (skipped)')

    out = {'meta': {'vintage': f'{B.config.VINTAGE} PUMS',
                    'note': 'single (MAR!=1) m/w by single year of age 20-64 per 2020 PUMA, '
                            'dense arrays est+MOE(SDR 90%); eb_*/en_* = BA+ / no-BA split. '
                            'Radius sums combine MOEs by RSS across PUMAs (independence '
                            'approximation). No race cells: PUMA-level race x single-year '
                            '1-yr cells are noise-dominated. Centroids: 2020-tract-pop-'
                            'weighted internal points; name = modal county.',
                    'keys': 'm_/w_ base est, m_m/w_m base MOE; eb_m/eb_w/eb_mm/eb_wm BA+; '
                            'en_* no-BA'},
           'pumas': pumas}
    json.dump(out, open(B.DATA / 'puma_radius.json', 'w'))
    kb = (B.DATA / 'puma_radius.json').stat().st_size // 1024
    print(f'wrote puma_radius.json  ({len(pumas)} PUMAs, {kb:,} KB)')

    # sanity: national totals must match the metro file's national sum path
    nm = base[(base.sex == 'm')].est.sum(); nw = base[(base.sex == 'w')].est.sum()
    print(f'national single 20-64: men {nm:,.0f}  women {nw:,.0f} '
          f'(compare pums_metro_age metro-sum + nonmetro)')

if __name__ == '__main__':
    main()
