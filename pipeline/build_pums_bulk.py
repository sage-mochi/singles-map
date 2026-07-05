"""Milestone 3 ingest: bulk-CSV PUMS -> metro cross-tabs + per-metro MOE.

Supersedes the per-state API pull in build_pums_metro.py. The API can't
realistically carry the 80 replicate weights for every state, so M3 switches to
the bulk person-CSV files (one zip per state, ~93 of 286 columns used here).

One pass per state produces four PUMA-level marginal aggregations, each carrying
all 81 weight columns (PWGTP + PWGTP1..80):
    base : (band, sex)              -> reproduces the M2 single counts
    race : (band, sex, race5)       -> mutually-exclusive race (PUMS advantage)
    edu  : (band, sex, ba_plus)     -> Date-onomics college split
    econ : (band, sex, employed) + income buckets   -> the differentiator

Each is allocated to CBSA via the existing proportional crosswalk (allocation is
linear, so the replicate weights carry through), then a 90% MOE is computed per
cell by successive-difference replication:
    SE = sqrt(0.05 * sum_r (X_r - X)^2),  MOE90 = 1.645 * SE.

Run from anywhere; paths resolve relative to the repo root. Resumable: raw zips
and per-state aggregates are cached under pipeline/pums_cache/ (git-ignored).
"""
import io, json, sys, time, zipfile, pickle
from pathlib import Path
import numpy as np, pandas as pd, requests
import config

ROOT  = Path(__file__).resolve().parent.parent
DATA  = ROOT / 'data'
CACHE = ROOT / 'pipeline' / 'pums_cache'
CACHE.mkdir(parents=True, exist_ok=True)

BASE = config.PUMS_BULK_BASE
# FIPS -> lowercase postal, for the csv_p{ab}.zip filenames.
ST_AB = {'01':'al','02':'ak','04':'az','05':'ar','06':'ca','08':'co','09':'ct',
 '10':'de','11':'dc','12':'fl','13':'ga','15':'hi','16':'id','17':'il','18':'in',
 '19':'ia','20':'ks','21':'ky','22':'la','23':'me','24':'md','25':'ma','26':'mi',
 '27':'mn','28':'ms','29':'mo','30':'mt','31':'ne','32':'nv','33':'nh','34':'nj',
 '35':'nm','36':'ny','37':'nc','38':'nd','39':'oh','40':'ok','41':'or','42':'pa',
 '44':'ri','45':'sc','46':'sd','47':'tn','48':'tx','49':'ut','50':'vt','51':'va',
 '53':'wa','54':'wv','55':'wi','56':'wy'}
STATES = list(ST_AB)

WCOLS = ['PWGTP'] + [f'PWGTP{i}' for i in range(1, 81)]   # 81: point + 80 reps
VARS  = ['STATE','PUMA','SEX','AGEP','MAR','RAC1P','HISP','SCHL','ESR',
         'PINCP','ADJINC']
USECOLS = VARS + WCOLS

RACE_CATS = ['white','black','asian','hisp','two','other']
INC_BUCKETS = ['<25k','25-50k','50-75k','75-100k','100k+']
INC_BINS = [-np.inf, 25_000, 50_000, 75_000, 100_000, np.inf]

# ---- download + read one state -------------------------------------------------
def fetch_zip(st):
    fn = CACHE / f'csv_p{ST_AB[st]}.zip'
    if fn.exists() and fn.stat().st_size > 0:
        return fn
    url = f'{BASE}/csv_p{ST_AB[st]}.zip'
    for k in range(5):
        try:
            r = requests.get(url, timeout=600)
            if r.status_code == 200:
                fn.write_bytes(r.content); return fn
        except Exception:
            pass
        time.sleep(3 * (k + 1))
    raise RuntimeError('download failed: ' + url)

def read_state(st):
    z = zipfile.ZipFile(fetch_zip(st))
    member = next(n for n in z.namelist() if n.lower().endswith('.csv'))
    dt = {'STATE':str, 'PUMA':str, 'SCHL':'float', 'ESR':'float',
          'PINCP':'float', 'ADJINC':'float'}
    df = pd.read_csv(io.BytesIO(z.read(member)), usecols=USECOLS, dtype=dt,
                     low_memory=False)
    return df

# ---- recode + the four marginal aggregations -----------------------------------
def aggregate_state(st):
    pkl = CACHE / f'aggm3_{st}.pkl'
    if pkl.exists():
        return pickle.load(open(pkl, 'rb'))
    df = read_state(st)
    df = df[(df.MAR != 1) & (df.AGEP >= 20) & (df.AGEP < 65)].copy()  # single 20-64
    df['pkey'] = df.STATE.str.zfill(2) + df.PUMA.str.zfill(5)
    df['band'] = ((df.AGEP - 20) // 5).astype(int)
    df['sex']  = np.where(df.SEX == 1, 'm', 'w')
    df['race'] = np.select(
        [df.HISP != 1, df.RAC1P == 1, df.RAC1P == 2, df.RAC1P == 6, df.RAC1P == 9],
        ['hisp', 'white', 'black', 'asian', 'two'], default='other')
    df['ba']  = np.where(df.SCHL >= 21, 'ba', 'noba')
    df['emp'] = df.ESR.isin([1, 2, 4, 5])
    inc = df.PINCP * df.ADJINC / 1e6                      # adjust to survey-year dollars
    df['incb'] = pd.cut(inc, bins=INC_BINS, labels=INC_BUCKETS)

    def g(keys):
        return df.groupby(keys, observed=True)[WCOLS].sum()
    out = {
        'base': g(['pkey','band','sex']),
        'race': g(['pkey','band','sex','race']),
        'edu' : g(['pkey','band','sex','ba']),
        'emp' : df[df.emp].groupby(['pkey','band','sex'], observed=True)[WCOLS].sum(),
        'inc' : df.dropna(subset=['incb']).groupby(
                    ['pkey','band','sex','incb'], observed=True)[WCOLS].sum(),
    }
    pickle.dump(out, open(pkl, 'wb'))
    return out

# ---- allocate PUMA aggregate -> CBSA via the crosswalk -------------------------
def load_xwalk_long():
    xw = json.load(open(DATA / 'puma_cbsa_xwalk.json'))
    rows = [(p, cb, af) for p, alloc in xw.items() for cb, af in alloc.items()]
    return pd.DataFrame(rows, columns=['pkey','cbsa','af'])

def allocate(agg, xwalk_long, dims):
    """agg: PUMA-level frame indexed by (pkey, *dims). -> CBSA frame, MOE added."""
    m = agg.reset_index().merge(xwalk_long, on='pkey')
    m[WCOLS] = m[WCOLS].mul(m.af, axis=0)
    out = m.groupby(['cbsa'] + dims, observed=True)[WCOLS].sum()
    X = out['PWGTP'].to_numpy()
    reps = out[[f'PWGTP{i}' for i in range(1, 81)]].to_numpy()
    se = np.sqrt(0.05 * ((reps - X[:, None]) ** 2).sum(axis=1))
    res = out[['PWGTP']].rename(columns={'PWGTP': 'est'})
    res['moe'] = 1.645 * se
    return res.reset_index()

# ---- assemble the national PUMA aggregates -------------------------------------
def combine(states):
    parts = {k: [] for k in ('base','race','edu','emp','inc')}
    for i, st in enumerate(states):
        a = aggregate_state(st)
        for k in parts:
            parts[k].append(a[k])
        sys.stdout.write(st + ' '); sys.stdout.flush()
        if (i + 1) % 13 == 0: print()
    print('\nstates aggregated.')
    keys = {'base':['pkey','band','sex'], 'race':['pkey','band','sex','race'],
            'edu':['pkey','band','sex','ba'], 'emp':['pkey','band','sex'],
            'inc':['pkey','band','sex','incb']}
    return {k: pd.concat(parts[k]).groupby(keys[k], observed=True).sum()
            for k in parts}

# ---- shape allocated frames into the nested-JSON deliverable -------------------
def bands_mw(frame, extra=None):
    """frame has cols cbsa,band,sex,[extra],est,moe -> {cbsa: {...m/w/moe arrays}}."""
    out = {}
    grp = frame.groupby('cbsa')
    for cb, sub in grp:
        node = {}
        cats = sub[extra].unique() if extra else [None]
        for cat in cats:
            s = sub if extra is None else sub[sub[extra] == cat]
            rec = {'m':[0]*9,'w':[0]*9,'m_moe':[0]*9,'w_moe':[0]*9}
            for _, r in s.iterrows():
                b = int(r.band)
                rec[r.sex][b] = round(r.est)
                rec[f'{r.sex}_moe'][b] = round(r.moe)
            if extra is None: node = rec
            else: node[str(cat)] = rec
        out[cb] = node
    return out

def main():
    states = STATES
    if len(sys.argv) > 1 and sys.argv[1] == '--states':
        states = sys.argv[2].split(',')
        print(f'(subset run: {states})')
    xwl = load_xwalk_long()
    nat = combine(states)

    # National total from PUMA-level weights (all PUMAs, pre-allocation) — the
    # apples-to-apples comparison with M2's national reconciliation. Allocation
    # only redistributes within this total (and an inner merge would silently
    # drop any PUMA absent from the crosswalk), so check it here.
    nat_total = nat['base']['PWGTP'].sum()
    matched = set(xwl.pkey)
    unmatched = sorted({p for p, *_ in nat['base'].index} - matched)

    base = allocate(nat['base'], xwl, ['band','sex'])
    race = allocate(nat['race'], xwl, ['band','sex','race'])
    edu  = allocate(nat['edu'],  xwl, ['band','sex','ba'])
    emp  = allocate(nat['emp'],  xwl, ['band','sex'])
    inc  = allocate(nat['inc'],  xwl, ['band','sex','incb'])
    for f in (base, race, edu, emp, inc):
        f.drop(f[f.cbsa == 'NONMETRO'].index, inplace=True)

    b = bands_mw(base)
    metros = {}
    for cb in b:
        metros[cb] = {'base': b[cb]}
    for cb, v in bands_mw(race, 'race').items(): metros.setdefault(cb,{})['race'] = v
    for cb, v in bands_mw(edu,  'ba').items():   metros.setdefault(cb,{})['edu']  = v
    for cb, v in bands_mw(emp).items():          metros.setdefault(cb,{})['emp']  = v
    for cb, v in bands_mw(inc,  'incb').items():  metros.setdefault(cb,{})['inc']  = v

    out = {'meta': {'vintage':f'{config.VINTAGE} PUMS',
                    'race_def':'mutually-exclusive (Hispanic, then NH White/Black/Asian/Two+/Other)',
                    'employed_def':'ESR in {1,2,4,5}',
                    'income':f'PINCP * ADJINC/1e6 ({config.ACS_YEAR} dollars)',
                    'moe':'SDR over 80 replicate weights, 90% (1.645*SE)',
                    'states': states},
           'metros': metros}
    full = len(states) == len(STATES)
    name = 'pums_metro_m3.json' if full else 'pums_metro_m3_subset.json'
    json.dump(out, open(DATA / name, 'w'))
    # backward-compatible base file (the M2 deliverable shape)
    if full:
        compat = {cb: {'m': metros[cb]['base']['m'], 'w': metros[cb]['base']['w']}
                  for cb in metros}
        json.dump(compat, open(DATA / 'pums_metro_singles.json', 'w'))
    print(f'\nwrote {name}  ({len(metros)} metros)')
    reconcile(base, nat_total, unmatched, full)

# ---- validation: base estimate vs M2 / published -------------------------------
def reconcile(base, nat_total, unmatched, full):
    # Vintage-specific anchor — RE-DERIVE when bumping config.ACS_YEAR (docs/REFRESH.md):
    #   group(B12002) for us:* summed over the single vars = national single 20-64.
    PUB_US = 97_210_546   # published B12002 single 20-64, ACS 2024 1-yr
    print('\n=== NATIONAL single 20-64 (bulk-CSV base) ===')
    print(f'  PUMA total {nat_total:,.0f}', end='')
    if full:
        print(f'   published {PUB_US:,}   diff {100*(nat_total-PUB_US)/PUB_US:+.2f}%')
    else:
        print('   (subset run — national check needs all states)')
    if unmatched:
        print(f'  PUMAs w/o crosswalk match: {len(unmatched)}  e.g. {unmatched[:5]}')
    # name, M2-allocated single 20-64 (ACS 2024, from pums-milestone2-results.md).
    # Vintage-specific — a new year won't match these; they anchor the 2024 rebuild only.
    REF = {'35620':('New York',6125553),'31080':('Los Angeles',4287760),
           '41860':('San Francisco',1390513),'16980':('Chicago',2843645),
           '12420':('Austin',759165),'19820':('Detroit',1299963)}
    print('=== large-metro single 20-64 (bulk-CSV vs M2 API ingest) ===')
    by = base.groupby('cbsa').est.sum()
    for cb, (nm, m2) in REF.items():
        if cb in by.index:
            e = by[cb]
            print(f'  {nm:14s} {e:>12,.0f}  M2 {m2:>12,}  diff {100*(e-m2)/m2:+.2f}%')
    # MOE sanity: NY single men 25-29 (M1 state-level example was 549,602 +-7,350)
    ny = base[(base.cbsa=='35620') & (base.band==1) & (base.sex=='m')]
    if len(ny):
        r = ny.iloc[0]
        print(f"\n  MOE check: NY metro single men 25-29 = {r.est:,.0f} +-{r.moe:,.0f}")

if __name__ == '__main__':
    main()
