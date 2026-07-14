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

COLS = ['SERIALNO', 'RELSHIPP', 'AGEP', 'SEX', 'PWGTP', 'RAC1P', 'HISP', 'MARHYP', 'SCHL']
SEEKER_AGES = list(range(18, 76))
GAPS = list(range(-20, 21))          # partner_age - seeker_age
RACES = ['white', 'black', 'asian', 'hisp', 'two', 'other']
EDUS  = ['ba', 'noba']               # BA+ split, same recode as build_pums_bulk
FORM_WINDOW = 7                      # a marriage is "recent formation" if within this many years

def recode_race(df):
    """Mutually-exclusive race, identical to build_pums_bulk: Hispanic first."""
    return np.select(
        [df.HISP != 1, df.RAC1P == 1, df.RAC1P == 2, df.RAC1P == 6, df.RAC1P == 9],
        ['hisp', 'white', 'black', 'asian', 'two'], default='other')

def couples_state(st):
    pkl = B.CACHE / f'couples4_{st}.pkl'          # v4 cache: adds SCHL (BA+ split)
    if pkl.exists():
        return pickle.load(open(pkl, 'rb'))
    z = zipfile.ZipFile(B.fetch_zip(st))
    member = next(n for n in z.namelist() if n.lower().endswith('.csv'))
    df = pd.read_csv(io.BytesIO(z.read(member)), usecols=COLS, low_memory=False)
    df = df[df.RELSHIPP.isin([20, 21, 22])].copy()
    df['rc'] = recode_race(df)
    df['ed'] = np.where(df.SCHL >= 21, 'ba', 'noba')
    ref = df[df.RELSHIPP == 20][['SERIALNO','AGEP','SEX','PWGTP','rc','ed','MARHYP']].rename(
            columns={'AGEP':'a0','SEX':'s0','PWGTP':'wt','rc':'rc0','ed':'ed0','MARHYP':'my0'})
    par = df[df.RELSHIPP.isin([21, 22])][['SERIALNO','AGEP','SEX','rc','ed','RELSHIPP']].rename(
            columns={'AGEP':'a1','SEX':'s1','rc':'rc1','ed':'ed1','RELSHIPP':'rel1'})
    cp = ref.merge(par, on='SERIALNO')
    cp = cp[cp.s0 != cp.s1]                       # opposite-sex pairs only
    man  = np.where(cp.s0 == 1, cp.a0, cp.a1)
    wom  = np.where(cp.s0 == 2, cp.a0, cp.a1)
    manr = np.where(cp.s0 == 1, cp.rc0, cp.rc1)
    womr = np.where(cp.s0 == 2, cp.rc0, cp.rc1)
    mane = np.where(cp.s0 == 1, cp.ed0, cp.ed1)
    wome = np.where(cp.s0 == 2, cp.ed0, cp.ed1)
    # Marriage year applies only when the partner is a spouse (RELSHIPP 21) — then
    # both partners' MARHYP is the year they married each other, so the ref's is it.
    # For an unmarried partner (22) MARHYP is the ref's OLD marriage (or blank), not
    # this union's date, so it's dropped and the union is treated as current.
    spouse = (cp.rel1 == 21).to_numpy()
    mary = np.where(spouse, cp.my0.to_numpy(dtype='float'), np.nan)
    out = pd.DataFrame({'man': man, 'wom': wom, 'manr': manr, 'womr': womr,
                        'mane': mane, 'wome': wome,
                        'wt': cp.wt.values, 'spouse': spouse, 'mary': mary})
    pickle.dump(out, open(pkl, 'wb'))
    return out

def pair_tables(cp, wcol, mcol, cats):
    """Pairing tables from the couples over any categorical trait (race, edu).
    Returns:
    - pair: P(partner cat | own cat, own sex) — realized shares (already mutual);
      used for RIVAL aim, exactly like the realized age kernel.
    - aff: affinity = P(q,r) / (P(q)·P(r)) — observed over expected under random
      mixing, so group SIZE is factored out (raw shares would double-count local
      composition when applied to metro pools). Symmetric in the pair by
      construction — the two-directional reciprocity is one number.
      Keyed [womanCat][manCat]."""
    tbl = cp.groupby([wcol, mcol]).wt.sum().unstack(fill_value=0.0)
    tbl = tbl.reindex(index=cats, columns=cats, fill_value=0.0)
    joint = tbl / tbl.values.sum()
    pw, pm = joint.sum(axis=1), joint.sum(axis=0)
    aff = joint.div(pw, axis=0).div(pm, axis=1)
    pair_m = {r: {q: round(float(joint.loc[q, r] / pm[r]), 5) for q in cats}
              for r in cats if pm[r] > 0}          # men of cat r -> partner q
    pair_w = {q: {r: round(float(joint.loc[q, r] / pw[q]), 5) for r in cats}
              for q in cats if pw[q] > 0}          # women of cat q -> partner r
    return ({'m': pair_m, 'w': pair_w},
            {q: {r: round(float(aff.loc[q, r]), 4) for r in cats} for q in cats},
            {'w': {q: round(float(pw[q]), 5) for q in cats},
             'm': {r: round(float(pm[r]), 5) for r in cats}})

def race_tables(cp):
    return pair_tables(cp, 'womr', 'manr', RACES)

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

    # Formation subset: current unions, not surviving-couple stock. A couple counts
    # if it is a cohabiting partnership (always current) OR a marriage formed within
    # FORM_WINDOW years. This removes the survivorship/vintage bias — an older
    # seeker's realized-partner distribution then reflects who pairs at that age NOW,
    # not who married them decades ago (re-partnering gaps run wider than first).
    CUR = B.config.ACS_YEAR
    formation = (~cp.spouse) | (cp.spouse & ((CUR - cp.mary) <= FORM_WINDOW))
    cpF = cp[formation]
    print(f'formation subset ({CUR-FORM_WINDOW}-{CUR} marriages + cohabiting): '
          f'{len(cpF):,} records, weighted {cpF.wt.sum():,.0f} '
          f'({100*cpF.wt.sum()/cp.wt.sum():.0f}% of couples)')

    manF, womF, wtF = cpF.man.to_numpy(), cpF.wom.to_numpy(), cpF.wt.to_numpy(float)
    m_by, _ = kernel_for(manF, womF, wtF)   # male seeker -> female partner (formation)
    w_by, _ = kernel_for(womF, manF, wtF)   # female seeker -> male partner
    m_cg = cond_gap(manF, womF, wtF)            # per-age conditional kernels (v3 reciprocity)
    w_cg = cond_gap(womF, manF, wtF)
    race_pair, race_aff, _ = race_tables(cp)   # race affinity: all couples (stable)
    edu_pair, edu_aff, _ = pair_tables(cp, 'wome', 'mane', EDUS)   # edu: same logic

    out = {'meta': {'vintage':f'{B.config.VINTAGE} PUMS',
                    'definition':f'opposite-sex unions. Age kernels use the FORMATION subset '
                                 f'(cohabiting partners + marriages formed {CUR-FORM_WINDOW}-{CUR} '
                                 f'via MARHYP) to reflect current pairing, not surviving-couple '
                                 f'stock. Race tables use all couples.',
                    'caveat':'realized unions reflect preference AND availability, not pure '
                             'preference; the tool exposes the range as an adjustable slider. '
                             'Formation subset is married-recent + all cohabiting (MARHYP dates '
                             'marriages only).',
                    'condGap':'P(gap | seeker age), triangular +-2 age smoothing; used for '
                              'rival aim and reciprocity discounting (v3). Formation subset.',
                    'race':'racePair = P(partner race | own race, sex), realized shares '
                           '(rival aim); raceAff = joint/(marginal*marginal) affinity, '
                           'group size factored out, symmetric (reciprocity). National, all couples; '
                           'age-gap and race treated as independent factors.',
                    'edu':'eduPair/eduAff = the same tables over the BA+ split (SCHL>=21), '
                          'keyed [womanEdu][manEdu]. National, all couples; independent factor.',
                    'states': states},
           'bySex': {'m': m_by, 'w': w_by},          # default ranges + asymmetry chart
           'condGap': {'m': m_cg, 'w': w_cg},         # reciprocity + rival aim
           'racePair': race_pair, 'raceAff': race_aff,   # race rival aim + reciprocity
           'eduPair': edu_pair, 'eduAff': edu_aff}       # edu rival aim + reciprocity
    full = len(states) == len(B.STATES)
    name = 'age_kernel.json' if full else 'age_kernel_subset.json'
    json.dump(out, open(B.DATA / name, 'w'))
    print(f'wrote {name}')

    # Formation vs all-couples comparison (the bias this fixes): older seekers'
    # ranges should widen / skew younger for men when using formation.
    a_m_by, _ = kernel_for(cp.man.to_numpy(), cp.wom.to_numpy(), cp.wt.to_numpy(float))
    a_w_by, _ = kernel_for(cp.wom.to_numpy(), cp.man.to_numpy(), cp.wt.to_numpy(float))
    print('=== default range: all-couples  ->  formation (p25-p75) ===')
    for a in (25, 30, 40, 50, 55):
        if a in m_by and a in a_m_by:
            print(f'  man {a}:   women {a_m_by[a]["p25"]}-{a_m_by[a]["p75"]}  ->  {m_by[a]["p25"]}-{m_by[a]["p75"]}'
                  f'   (n {m_by[a]["n"]:,})')
    for a in (25, 30, 40, 50, 55):
        if a in w_by and a in a_w_by:
            print(f'  woman {a}: men   {a_w_by[a]["p25"]}-{a_w_by[a]["p75"]}  ->  {w_by[a]["p25"]}-{w_by[a]["p75"]}'
                  f'   (n {w_by[a]["n"]:,})')
    print('=== race pairing (realized shares, men -> partner) ===')
    for r in RACES:
        row = race_pair['m'].get(r, {})
        top = sorted(row.items(), key=lambda x: -x[1])[:3]
        print(f'  {r:6s} men: ' + '  '.join(f'{q} {100*v:.1f}%' for q, v in top))
    print('=== own-race affinity (obs/expected under random mixing) ===')
    print('  ' + '  '.join(f'{q}:{race_aff[q][q]:.1f}' for q in RACES))
    print('=== edu pairing (realized shares) + affinity ===')
    for e in EDUS:
        row = edu_pair['m'].get(e, {})
        print(f'  {e:5s} men: ' + '  '.join(f'{q} {100*v:.1f}%' for q, v in row.items()))
    print('  affinity same-edu: ' + '  '.join(f'{q}:{edu_aff[q][q]:.2f}' for q in EDUS))

if __name__ == '__main__':
    main()
