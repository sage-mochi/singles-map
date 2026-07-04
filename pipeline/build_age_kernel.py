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

COLS = ['SERIALNO', 'RELSHIPP', 'AGEP', 'SEX', 'PWGTP']
SEEKER_AGES = list(range(18, 76))
GAPS = list(range(-20, 21))          # partner_age - seeker_age

def couples_state(st):
    pkl = B.CACHE / f'couples_{st}.pkl'
    if pkl.exists():
        return pickle.load(open(pkl, 'rb'))
    z = zipfile.ZipFile(B.fetch_zip(st))
    member = next(n for n in z.namelist() if n.lower().endswith('.csv'))
    df = pd.read_csv(io.BytesIO(z.read(member)), usecols=COLS, low_memory=False)
    df = df[df.RELSHIPP.isin([20, 21, 22])]
    ref = df[df.RELSHIPP == 20][['SERIALNO','AGEP','SEX','PWGTP']].rename(
            columns={'AGEP':'a0','SEX':'s0','PWGTP':'wt'})
    par = df[df.RELSHIPP.isin([21, 22])][['SERIALNO','AGEP','SEX']].rename(
            columns={'AGEP':'a1','SEX':'s1'})
    cp = ref.merge(par, on='SERIALNO')
    cp = cp[cp.s0 != cp.s1]                       # opposite-sex pairs only
    man = np.where(cp.s0 == 1, cp.a0, cp.a1)
    wom = np.where(cp.s0 == 2, cp.a0, cp.a1)
    out = pd.DataFrame({'man': man, 'wom': wom, 'wt': cp.wt.values})
    pickle.dump(out, open(pkl, 'wb'))
    return out

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

    out = {'meta': {'vintage':'ACS 2024 1-year PUMS',
                    'definition':'opposite-sex married/partnered couples (RELSHIPP 20 + 21/22)',
                    'caveat':'realized couples reflect preference AND past availability, not pure '
                             'preference; the tool exposes the range as an adjustable slider.',
                    'condGap':'P(gap | seeker age), triangular +-2 age smoothing; used for '
                              'rival aim and reciprocity discounting (v3).',
                    'states': states},
           'bySex': {'m': m_by, 'w': w_by},
           'gapDist': {'m': m_gap, 'w': w_gap},
           'condGap': {'m': m_cg, 'w': w_cg}}
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

if __name__ == '__main__':
    main()
