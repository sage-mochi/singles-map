"""Step 1B of the matchmaking metric: the empirical partner-age-gap kernel K.

Derived from PUMS couples: within each household, the reference person (RELSHIPP
20) is paired with their opposite-sex spouse/partner (RELSHIPP 21/22), giving a
(man age, woman age) couple weighted by the reference person's weight. From the
realized age gaps we report, per seeker sex and age, the partner-age distribution
(p25/p50/p75/mean) -- which sets the DEFAULT preferred-age-range slider -- plus a
marginal gap distribution (the default K shape the user can then re-center).

Caveat surfaced to the user: realized couples reflect preference AND past
availability, not pure preference -- which is exactly why the tool exposes the
range as an adjustable slider rather than baking this in.

Reuses build_pums_bulk for the cached zips. Output: data/age_kernel.json.
"""
import sys, io, json, pickle, zipfile
import numpy as np, pandas as pd
import build_pums_bulk as B

COLS = ['SERIALNO', 'RELSHIPP', 'AGEP', 'SEX', 'PWGTP', 'RAC1P', 'HISP']
SEEKER_AGES = list(range(18, 76))
GAPS = list(range(-20, 21))          # partner_age - seeker_age
RACES = ['white', 'black', 'asian', 'hisp', 'two', 'other']

def recode_race(df):
    """Mutually-exclusive race, identical to build_pums_bulk: Hispanic first."""
    return np.select(
        [df.HISP != 1, df.RAC1P == 1, df.RAC1P == 2, df.RAC1P == 6, df.RAC1P == 9],
        ['hisp', 'white', 'black', 'asian', 'two'], default='other')

def couples_state(st):
    pkl = B.CACHE / f'couples2_{st}.pkl'          # v2 cache: adds race
    if pkl.exists():
        return pickle.load(open(pkl, 'rb'))
    z = zipfile.ZipFile(B.fetch_zip(st))
    member = next(n for n in z.namelist() if n.lower().endswith('.csv'))
    df = pd.read_csv(io.BytesIO(z.read(member)), usecols=COLS, low_memory=False)
    df = df[df.RELSHIPP.isin([20, 21, 22])].copy()
    df['rc'] = recode_race(df)
    ref = df[df.RELSHIPP == 20][['SERIALNO','AGEP','SEX','PWGTP','rc']].rename(
            columns={'AGEP':'a0','SEX':'s0','PWGTP':'wt','rc':'rc0'})
    par = df[df.RELSHIPP.isin([21, 22])][['SERIALNO','AGEP','SEX','rc']].rename(
            columns={'AGEP':'a1','SEX':'s1','rc':'rc1'})
    cp = ref.merge(par, on='SERIALNO')
    cp = cp[cp.s0 != cp.s1]                       # opposite-sex pairs only
    man  = np.where(cp.s0 == 1, cp.a0, cp.a1)
    wom  = np.where(cp.s0 == 2, cp.a0, cp.a1)
    manr = np.where(cp.s0 == 1, cp.rc0, cp.rc1)
    womr = np.where(cp.s0 == 2, cp.rc0, cp.rc1)
    out = pd.DataFrame({'man': man, 'wom': wom, 'manr': manr, 'womr': womr,
                        'wt': cp.wt.values})
    pickle.dump(out, open(pkl, 'wb'))
    return out

def race_tables(cp):
    """Race-pairing tables from the couples. Returns:
    - pair: P(partner race | own race, own sex) — realized shares (already mutual);
      used for RIVAL aim, exactly like the realized age kernel.
    - aff: affinity = P(q,r) / (P(q)·P(r)) — observed over expected under random
      mixing, so group SIZE is factored out (raw shares would double-count local
      composition when applied to metro pools). Symmetric in the pair by
      construction — the two-directional reciprocity is one number.
      Keyed [womanRace][manRace]."""
    tbl = cp.groupby(['womr', 'manr']).wt.sum().unstack(fill_value=0.0)
    tbl = tbl.reindex(index=RACES, columns=RACES, fill_value=0.0)
    joint = tbl / tbl.values.sum()
    pw, pm = joint.sum(axis=1), joint.sum(axis=0)
    aff = joint.div(pw, axis=0).div(pm, axis=1)
    pair_m = {r: {q: round(float(joint.loc[q, r] / pm[r]), 5) for q in RACES}
              for r in RACES if pm[r] > 0}          # men of race r -> partner q
    pair_w = {q: {r: round(float(joint.loc[q, r] / pw[q]), 5) for r in RACES}
              for q in RACES if pw[q] > 0}          # women of race q -> partner r
    return ({'m': pair_m, 'w': pair_w},
            {q: {r: round(float(aff.loc[q, r]), 4) for r in RACES} for q in RACES},
            {'w': {q: round(float(pw[q]), 5) for q in RACES},
             'm': {r: round(float(pm[r]), 5) for r in RACES}})

def wquantile(v, w, q):
    o = np.argsort(v); v, w = v[o], w[o]
    cw = (np.cumsum(w) - 0.5*w) / w.sum()
    return float(np.interp(q, cw, v))

def cond_gap(seeker_age, partner_age, wt):
    """Conditional gap distribution per seeker age (v3 reciprocity needs this):
    P(partner_age - seeker_age = g | seeker age), smoothed along the age axis with a
    triangular +-2 window (1,2,3,2,1) to stabilize thin edge cells. Sparse dict output
    (gaps with p < 1e-4 dropped)."""
    gap = (partner_age - seeker_age).astype(int)
    A, G = len(SEEKER_AGES), len(GAPS)
    H = np.zeros((A, G))
    ai = (seeker_age - SEEKER_AGES[0]).astype(int)
    gi = gap - GAPS[0]
    ok = (ai >= 0) & (ai < A) & (gi >= 0) & (gi < G)
    np.add.at(H, (ai[ok], gi[ok]), wt[ok])
    Hs = np.zeros_like(H)
    for off, w in ((-2, 1), (-1, 2), (0, 3), (1, 2), (2, 1)):
        if off < 0:   Hs[-off:, :] += w * H[:off, :]
        elif off > 0: Hs[:-off, :] += w * H[off:, :]
        else:         Hs += w * H
    out = {}
    for i, a in enumerate(SEEKER_AGES):
        tot = Hs[i].sum()
        if tot <= 0: continue
        row = {int(g): round(float(v / tot), 5)
               for g, v in zip(GAPS, Hs[i]) if v / tot >= 1e-4}
        if row: out[a] = row
    return out

def kernel_for(seeker_age, partner_age, wt):
    """-> {age: {p25,p50,p75,mean,n}} over SEEKER_AGES, and gap histogram."""
    by = {}
    for a in SEEKER_AGES:
        msk = seeker_age == a
        if msk.sum() == 0: continue
        pa, w = partner_age[msk], wt[msk]
        if w.sum() <= 0: continue
        by[a] = {'p25': round(wquantile(pa, w, .25)),
                 'p50': round(wquantile(pa, w, .50)),
                 'p75': round(wquantile(pa, w, .75)),
                 'mean': round(float(np.average(pa, weights=w)), 1),
                 'n': int(round(w.sum()))}
    gap = partner_age - seeker_age
    hist = {}
    for g in GAPS:
        hist[g] = float(wt[gap == g].sum())
    tot = sum(hist.values()) or 1.0
    hist = {g: round(v/tot, 4) for g, v in hist.items()}
    return by, hist

def main():
    states = B.STATES
    if len(sys.argv) > 2 and sys.argv[1] == '--states':
        states = sys.argv[2].split(',')
        print(f'(subset run: {states})')
    parts = []
    for i, st in enumerate(states):
        parts.append(couples_state(st))
        sys.stdout.write(st + ' '); sys.stdout.flush()
        if (i + 1) % 13 == 0: print()
    cp = pd.concat(parts, ignore_index=True)
    print(f'\ncouples: {len(cp):,} records, weighted {cp.wt.sum():,.0f}')

    man, wom, wt = cp.man.to_numpy(), cp.wom.to_numpy(), cp.wt.to_numpy(float)
    m_by, m_gap = kernel_for(man, wom, wt)    # male seeker -> female partner
    w_by, w_gap = kernel_for(wom, man, wt)    # female seeker -> male partner
    m_cg = cond_gap(man, wom, wt)             # per-age conditional kernels (v3 reciprocity)
    w_cg = cond_gap(wom, man, wt)
    race_pair, race_aff, race_marg = race_tables(cp)

    out = {'meta': {'vintage':f'{B.config.VINTAGE} PUMS',
                    'definition':'opposite-sex married/partnered couples (RELSHIPP 20 + 21/22)',
                    'caveat':'realized couples reflect preference AND past availability, not pure '
                             'preference; the tool exposes the range as an adjustable slider.',
                    'condGap':'P(gap | seeker age), triangular +-2 age smoothing; used for '
                              'rival aim and reciprocity discounting (v3).',
                    'race':'racePair = P(partner race | own race, sex), realized shares '
                           '(rival aim); raceAff = joint/(marginal*marginal) affinity, '
                           'group size factored out, symmetric (reciprocity). National; '
                           'age-gap and race treated as independent factors.',
                    'states': states},
           'bySex': {'m': m_by, 'w': w_by},
           'gapDist': {'m': m_gap, 'w': w_gap},
           'condGap': {'m': m_cg, 'w': w_cg},
           'racePair': race_pair, 'raceAff': race_aff, 'raceMarg': race_marg}
    full = len(states) == len(B.STATES)
    name = 'age_kernel.json' if full else 'age_kernel_subset.json'
    json.dump(out, open(B.DATA / name, 'w'))
    print(f'wrote {name}')
    # sanity: typical gap by a few seeker ages
    print('=== male seeker -> partner age (p25/p50/p75) ===')
    for a in (25, 30, 40, 50):
        if a in m_by: r=m_by[a]; print(f'  man {a}: women {r["p25"]}-{r["p75"]} (median {r["p50"]}, mean {r["mean"]})')
    print('=== female seeker -> partner age ===')
    for a in (25, 30, 40, 50):
        if a in w_by: r=w_by[a]; print(f'  woman {a}: men {r["p25"]}-{r["p75"]} (median {r["p50"]}, mean {r["mean"]})')
    print('=== race pairing (realized shares, men -> partner) ===')
    for r in RACES:
        row = race_pair['m'].get(r, {})
        top = sorted(row.items(), key=lambda x: -x[1])[:3]
        print(f'  {r:6s} men: ' + '  '.join(f'{q} {100*v:.1f}%' for q, v in top))
    print('=== own-race affinity (obs/expected under random mixing) ===')
    print('  ' + '  '.join(f'{q}:{race_aff[q][q]:.1f}' for q in RACES))

if __name__ == '__main__':
    main()
