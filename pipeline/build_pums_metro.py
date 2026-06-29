import requests, json, os, sys, time
from collections import defaultdict

KEY=os.environ.get('CENSUS_API_KEY')
if not KEY: raise SystemExit('Set CENSUS_API_KEY (free: https://api.census.gov/data/key_signup.html)')
PUMS='https://api.census.gov/data/2024/acs/acs1/pums'
STATES=['01','02','04','05','06','08','09','10','11','12','13','15','16','17','18','19',
 '20','21','22','23','24','25','26','27','28','29','30','31','32','33','34','35','36','37',
 '38','39','40','41','42','44','45','46','47','48','49','50','51','53','54','55','56']

def get(params, tries=5):
    for k in range(tries):
        try:
            r=requests.get(PUMS,params=params,timeout=300)
            if r.status_code==200: return r.json()
            time.sleep(3*(k+1))
        except Exception: time.sleep(3*(k+1))
    raise RuntimeError('fail '+str(params.get('in')))

# Reconciliation targets (published B12002 single 20-64 by band): national + sample metros.
# Self-generates if refs_published.json is absent, so a clean clone is fully reproducible.
TBL='https://api.census.gov/data/2024/acs/acs1'
REF_METROS={'35620':'New York','31080':'Los Angeles','41860':'San Francisco','12420':'Austin',
 '16980':'Chicago','19820':'Detroit','41940':'San Jose','24340':'Grand Rapids','38900':'Portland OR'}

def _single_bands(geo_for, geo_in=None):
    p={'get':'group(B12002)','for':geo_for,'key':KEY}
    if geo_in: p['in']=geo_in
    r=None
    for k in range(5):
        r=requests.get(TBL,params=p,timeout=180)
        if r.status_code==200: break
        time.sleep(3*(k+1))
    rows=r.json(); idx={h:i for i,h in enumerate(rows[0])}
    gi=idx.get('metropolitan statistical area/micropolitan statistical area', idx.get('us'))
    out={}
    for row in rows[1:]:
        def val(n):
            try: x=int(row[idx[f'B12002_{n:03d}E']]); return max(x,0)
            except: return 0
        m=[sum(val(s+i) for s in (6,38,68,83)) for i in range(9)]
        w=[sum(val(s+i) for s in (99,131,161,176)) for i in range(9)]
        out[row[gi]]={'m':m,'w':w}
    return out

def build_refs():
    refs={'US':_single_bands('us:*')['1']}
    mm=_single_bands('metropolitan statistical area/micropolitan statistical area:'+','.join(REF_METROS))
    for cb,nm in REF_METROS.items():
        refs[cb]={'name':nm, **mm[cb]}
    json.dump(refs, open('refs_published.json','w'))
    return refs

os.makedirs('pums_cache', exist_ok=True)
# 1) per-state: single by (puma,band,sex)
for st in STATES:
    fn=f'pums_cache/agg_{st}.json'
    if os.path.exists(fn): continue
    rows=get({'get':'SEX,AGEP,MAR,PWGTP','for':'public use microdata area:*',
              'in':f'state:{st}','key':KEY})
    idx={h:i for i,h in enumerate(rows[0])}
    iS,iA,iM,iWT,iPU=idx['SEX'],idx['AGEP'],idx['MAR'],idx['PWGTP'],idx['public use microdata area']
    agg=defaultdict(int)
    for row in rows[1:]:
        if row[iM]=='1': continue            # married -> skip
        a=int(row[iA])
        if a<20 or a>=65: continue
        band=(a-20)//5
        sex='m' if row[iS]=='1' else 'w'
        puma=st+row[iPU]
        agg[f'{puma}|{band}|{sex}']+=int(row[iWT])
    json.dump(agg, open(fn,'w'))
    sys.stdout.write(st+' '); sys.stdout.flush()
print('\nPUMS pulled.')

# 2) combine to puma-level
puma_agg=defaultdict(lambda: defaultdict(int))   # puma -> {(band,sex):wt}
for st in STATES:
    for k,v in json.load(open(f'pums_cache/agg_{st}.json')).items():
        puma,band,sex=k.split('|'); puma_agg[puma][(int(band),sex)]+=v

# 3) allocate to CBSA via afact + national total
xwalk=json.load(open('puma_cbsa_xwalk.json'))
cbsa=defaultdict(lambda: defaultdict(float))
nat=defaultdict(float)
missing=0
for puma,cells in puma_agg.items():
    alloc=xwalk.get(puma)
    if not alloc: missing+=1
    for (band,sex),wt in cells.items():
        nat[(band,sex)]+=wt
        if not alloc: continue
        for cb,af in alloc.items():
            cbsa[cb][(band,sex)]+=wt*af
print(f'PUMAs w/o xwalk match: {missing}')

# 4) reconcile
refs = json.load(open('refs_published.json')) if os.path.exists('refs_published.json') else build_refs()
def tot(d): return sum(d.values())
def banded(d):  # -> m[9],w[9]
    m=[d.get((i,'m'),0) for i in range(9)]; w=[d.get((i,'w'),0) for i in range(9)]; return m,w

print('\n=== NATIONAL reconciliation (single 20-64) ===')
nm,nw=banded(nat); pubUS=refs['US']
pn=sum(nm)+sum(nw); pp=sum(pubUS['m'])+sum(pubUS['w'])
print(f'  PUMS {pn:,.0f}  published {pp:,}  diff {100*(pn-pp)/pp:+.2f}%')

print('\n=== METRO reconciliation (single 20-64, allocated vs published) ===')
print(f"  {'metro':14s} {'PUMS(alloc)':>12s} {'published':>12s} {'diff%':>7s}")
for cb in [k for k in refs if k!='US']:
    m,w=banded(cbsa.get(cb,{})); pe=sum(m)+sum(w)
    pp=sum(refs[cb]['m'])+sum(refs[cb]['w'])
    print(f"  {refs[cb]['name']:14s} {pe:>12,.0f} {pp:>12,} {100*(pe-pp)/pp:>+6.1f}%")

# 5) save allocated metro singles (the M2 deliverable) + full reconciliation
out={}
for cb,cells in cbsa.items():
    if cb=='NONMETRO': continue
    m,w=banded(cells)
    out[cb]={'m':[round(x) for x in m],'w':[round(x) for x in w]}
json.dump(out, open('pums_metro_singles.json','w'))

# full reconciliation vs all published MSAs
allcb=sorted(out)
pubALL={}
CH=40
for i in range(0,len(allcb),CH):
    chunk=allcb[i:i+CH]
    p={'get':'group(B12002)','for':'metropolitan statistical area/micropolitan statistical area:'+','.join(chunk),'key':KEY}
    rr=requests.get('https://api.census.gov/data/2024/acs/acs1',params=p,timeout=180)
    if rr.status_code!=200: continue
    rows=rr.json(); idx={h:i for i,h in enumerate(rows[0])}
    gi=idx['metropolitan statistical area/micropolitan statistical area']
    for row in rows[1:]:
        def val(n):
            try: x=int(row[idx[f'B12002_{n:03d}E']]); return max(x,0)
            except: return 0
        tt=sum(sum(val(s+i) for s in (6,38,68,83,99,131,161,176)) for i in range(9))
        pubALL[row[gi]]=tt
errs=[]
for cb in allcb:
    pe=sum(out[cb]['m'])+sum(out[cb]['w']); pp=pubALL.get(cb)
    if pp and pp>0: errs.append((abs(100*(pe-pp)/pp), cb, pe, pp))
errs.sort()
import statistics as S
absv=[e[0] for e in errs]
print(f'\n=== FULL METRO reconciliation ({len(errs)} MSAs) ===')
print(f'  median |err| {S.median(absv):.2f}%  | 90th pct {sorted(absv)[int(0.9*len(absv))]:.2f}%  | max {absv[-1]:.2f}%')
print(f'  within 1%: {sum(1 for a in absv if a<=1)}/{len(absv)}  within 2%: {sum(1 for a in absv if a<=2)}/{len(absv)}')
print('  worst 5 (allocation-straddle metros):')
for a,cb,pe,pp in errs[-5:]:
    print(f'    {cb}: PUMS {pe:,.0f} vs pub {pp:,}  {100*(pe-pp)/pp:+.1f}%')
