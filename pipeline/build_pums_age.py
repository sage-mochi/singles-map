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
RACES = ['white', 'black', 'asian', 'hisp', 'two', 'other']
# Sparse race×age cells below this are noise-dominated (relative MOE > 100%) and sit
# far under the seeker's 1,000-reciprocal-match floor, so dropping them can't change a
# surfaced result — it only trims page weight (notably under the ~5x-denser 5-year sample).
SPARSE_FLOOR = 10

def aggregate_age_state(st):
    """PUMA x single-year-age x sex x RACE x BA aggregate (all 81 weight cols).
    The sex-level totals and the race/edu marginals are recovered by summing
    cells (replicate columns are linear, so every level keeps exact SDR MOEs)."""
    pkl = B._pkl('aggage3_', st)                 # v3 cache: adds ba (vintage-namespaced)
    if pkl.exists():
        return pickle.load(open(pkl, 'rb'))
    df = B.read_state(st)
    df = df[(df.MAR != 1) & (df.AGEP >= 20) & (df.AGEP < 65)].copy()   # single 20-64
    df['pkey'] = df.STATE.str.zfill(2) + df.PUMA.str.zfill(5)
    df['sex']  = np.where(df.SEX == 1, 'm', 'w')
    df['race'] = np.select(
        [df.HISP != 1, df.RAC1P == 1, df.RAC1P == 2, df.RAC1P == 6, df.RAC1P == 9],
        ['hisp', 'white', 'black', 'asian', 'two'], default='other')
    df['ba']  = np.where(df.SCHL >= 21, 'ba', 'noba')      # same recode as build_pums_bulk
    agg = df.groupby(['pkey', 'AGEP', 'sex', 'race', 'ba'], observed=True)[B.WCOLS].sum()
    pickle.dump(agg, open(pkl, 'wb'))
    return agg

def main():
    args = sys.argv[1:]
    if '--5yr' in args:
        B.set_vintage('5yr'); args.remove('--5yr')
        print(f'(5-year vintage: {B.config.VINTAGE5})')
    states = B.STATES
    if len(args) >= 2 and args[0] == '--states':
        states = args[1].split(',')
        print(f'(subset run: {states})')

    xwl = B.load_xwalk_long()
    parts = []
    for i, st in enumerate(states):
        parts.append(aggregate_age_state(st))
        sys.stdout.write(st + ' '); sys.stdout.flush()
        if (i + 1) % 13 == 0: print()
    print('\nstates aggregated.')
    nat_full = pd.concat(parts).groupby(['pkey', 'AGEP', 'sex', 'race', 'ba'], observed=True).sum()
    nat_race = nat_full.groupby(['pkey', 'AGEP', 'sex', 'race'], observed=True).sum()
    nat_edu  = nat_full.groupby(['pkey', 'AGEP', 'sex', 'ba'], observed=True).sum()
    nat = nat_race.groupby(['pkey', 'AGEP', 'sex'], observed=True).sum()   # exact (linear)

    alloc = B.allocate(nat, xwl, ['AGEP', 'sex'])      # -> cbsa, AGEP, sex, est, moe
    alloc = alloc[alloc.cbsa != 'NONMETRO']

    metros = {}
    for _, r in alloc.iterrows():
        a = int(r.AGEP)
        if a < 20 or a > 64: continue
        node = metros.setdefault(r.cbsa, {'m':[0]*45,'w':[0]*45,'m_moe':[0]*45,'w_moe':[0]*45})
        node[r.sex][a-20]       = round(r.est)
        node[f'{r.sex}_moe'][a-20] = round(r.moe)

    # race x single-year cells (seeker-mode race filter), sparse: {race:{sex:{age:[est,moe]}}}
    alloc_r = B.allocate(nat_race, xwl, ['AGEP', 'sex', 'race'])
    alloc_r = alloc_r[alloc_r.cbsa != 'NONMETRO']
    for _, r in alloc_r.iterrows():
        a = int(r.AGEP)
        if a < 20 or a > 64: continue
        est = round(r.est)
        if est < SPARSE_FLOOR: continue            # sparse: drop noise-dominated cells
        node = metros.setdefault(r.cbsa, {'m':[0]*45,'w':[0]*45,'m_moe':[0]*45,'w_moe':[0]*45})
        node.setdefault('race', {}).setdefault(r.race, {}).setdefault(r.sex, {})[a] = \
            [est, round(r.moe)]

    # edu x single-year cells (seeker-mode education filter), same sparse shape:
    # {ba/noba:{sex:{age:[est,moe]}}}. Two thick groups, so 1-year cells hold up.
    alloc_e = B.allocate(nat_edu, xwl, ['AGEP', 'sex', 'ba'])
    alloc_e = alloc_e[alloc_e.cbsa != 'NONMETRO']
    for _, r in alloc_e.iterrows():
        a = int(r.AGEP)
        if a < 20 or a > 64: continue
        est = round(r.est)
        if est < SPARSE_FLOOR: continue
        node = metros.setdefault(r.cbsa, {'m':[0]*45,'w':[0]*45,'m_moe':[0]*45,'w_moe':[0]*45})
        node.setdefault('edu', {}).setdefault(r.ba, {}).setdefault(r.sex, {})[a] = \
            [est, round(r.moe)]

    vintage = B.config.VINTAGE5 if B.VTAG == '5yr' else B.config.VINTAGE
    out = {'meta': {'vintage':f'{vintage} PUMS',
                    'ages': AGES,
                    'note':'single (MAR!=1) men/women by single year of age, per CBSA; '
                           'MOE 90% from 80 replicate weights (SDR). race = sparse '
                           'mutually-exclusive race cells {race:{sex:{age:[est,moe]}}}; '
                           'edu = sparse BA+ split (SCHL>=21) {ba/noba:{sex:{age:[est,moe]}}}.',
                    'states': states},
           'metros': metros}
    full = len(states) == len(B.STATES)
    stem = 'pums_metro_age_5yr' if B.VTAG == '5yr' else 'pums_metro_age'
    name = f'{stem}.json' if full else f'{stem}_subset.json'
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
