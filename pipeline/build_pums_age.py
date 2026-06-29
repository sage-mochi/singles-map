"""Step 1A of the matchmaking metric: single-year-of-age single counts per metro.

The map/tables use 5-year bands, which can't express the ~2-year partner age gap
that the availability-ratio (v2) metric needs. PUMS AGEP is continuous, so here we
re-tabulate single men/women by SINGLE YEAR OF AGE (20-64) per metro, with a per-
cell MOE from the replicate weights. The v2 seeker metric then weights the
opposite-sex pool (and same-sex rivals) across these single-year cells by a user-
set partner-age kernel.

Reuses build_pums_bulk's download/read/allocate machinery; raw zips are cached, so
this is a re-tabulation pass (no re-download). Per-state age aggregates are cached
under pipeline/pums_cache/. Output: data/pums_metro_age.json.
"""
import sys, json, pickle, time
import numpy as np, pandas as pd
import build_pums_bulk as B          # fetch_zip / read_state / load_xwalk_long / allocate / WCOLS / STATES

AGES = list(range(20, 65))           # 45 single-year cells, matching the single 20-64 universe

def aggregate_age_state(st):
    pkl = B.CACHE / f'aggage_{st}.pkl'
    if pkl.exists():
        return pickle.load(open(pkl, 'rb'))
    df = B.read_state(st)
    df = df[(df.MAR != 1) & (df.AGEP >= 20) & (df.AGEP < 65)].copy()   # single 20-64
    df['pkey'] = df.STATE.str.zfill(2) + df.PUMA.str.zfill(5)
    df['sex']  = np.where(df.SEX == 1, 'm', 'w')
    agg = df.groupby(['pkey', 'AGEP', 'sex'], observed=True)[B.WCOLS].sum()
    pickle.dump(agg, open(pkl, 'wb'))
    return agg

def main():
    states = B.STATES
    if len(sys.argv) > 2 and sys.argv[1] == '--states':
        states = sys.argv[2].split(',')
        print(f'(subset run: {states})')

    xwl = B.load_xwalk_long()
    parts = []
    for i, st in enumerate(states):
        parts.append(aggregate_age_state(st))
        sys.stdout.write(st + ' '); sys.stdout.flush()
        if (i + 1) % 13 == 0: print()
    print('\nstates aggregated.')
    nat = pd.concat(parts).groupby(['pkey', 'AGEP', 'sex'], observed=True).sum()

    alloc = B.allocate(nat, xwl, ['AGEP', 'sex'])      # -> cbsa, AGEP, sex, est, moe
    alloc = alloc[alloc.cbsa != 'NONMETRO']

    metros = {}
    for _, r in alloc.iterrows():
        a = int(r.AGEP)
        if a < 20 or a > 64: continue
        node = metros.setdefault(r.cbsa, {'m':[0]*45,'w':[0]*45,'m_moe':[0]*45,'w_moe':[0]*45})
        node[r.sex][a-20]       = round(r.est)
        node[f'{r.sex}_moe'][a-20] = round(r.moe)

    out = {'meta': {'vintage':'ACS 2024 1-year PUMS',
                    'ages': AGES,
                    'note':'single (MAR!=1) men/women by single year of age, per CBSA; '
                           'MOE 90% from 80 replicate weights (SDR).',
                    'states': states},
           'metros': metros}
    full = len(states) == len(B.STATES)
    name = 'pums_metro_age.json' if full else 'pums_metro_age_subset.json'
    json.dump(out, open(B.DATA / name, 'w'))
    print(f'\nwrote {name}  ({len(metros)} metros, ages 20-64)')

    # quick sanity: national single men/women by age should be smooth & ~equal young, female-heavy old
    nm = [sum(metros[c]['m'][i] for c in metros) for i in range(45)]
    nw = [sum(metros[c]['w'][i] for c in metros) for i in range(45)]
    if full:
        print('=== national single counts by age (metro sum) ===')
        for a in (20, 25, 30, 35, 45, 60):
            i = a-20
            print(f'  age {a}: men {nm[i]:>10,}  women {nw[i]:>10,}  m/100w {100*nm[i]/nw[i]:.0f}')

if __name__ == '__main__':
    main()
