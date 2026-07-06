"""Post-build validation for the 5-year race switch (open decision #3).

Run after build_pums_bulk.py --5yr + build_pums_age.py --5yr. Reports:
  1. Coverage: how many metros gained a 5-year race node.
  2. MOE shrinkage: median relative margin on race 25-34 cells, 1-year vs 5-year
     (expect ~halving — the whole point of the switch).
  3. §3 leverage bands recomputed on the 5-year sample (count-weighted 10/median/90
     of the men-per-100-women ratio), so the exhibit numbers can be re-baked.
  4. Raw (unweighted) vs weighted spread — the "small-sample noise stripped out" claim.
Purely diagnostic; writes nothing.
"""
import json, math
from pathlib import Path
D = Path(__file__).resolve().parent.parent / 'data'

m1  = json.load(open(D/'pums_metro_m3.json'))['metros']
m5  = json.load(open(D/'pums_metro_m3_5yr.json'))['metros']

def relmoe_cell(n):
    """median relative MOE over the 25-34 race cells (bands 1,2, both sexes)."""
    out=[]
    for sx in ('m','w'):
        for b in (1,2):
            e=n[sx][b]; mo=n[f'{sx}_moe'][b]
            if e>50: out.append(mo/e)
    return out

def race_relmoe(metros):
    per={r:[] for r in ('white','black','hisp','asian')}
    for x in metros.values():
        for r in per:
            n=x.get('race',{}).get(r)
            if n: per[r]+=relmoe_cell(n)
    return {r:(sorted(v)[len(v)//2] if v else None) for r,v in per.items()}

print('=== 1. coverage ===')
cov1=sum('race' in x for x in m1.values()); cov5=sum('race' in x for x in m5.values())
print(f'  metros with a race node: 1-year {cov1}, 5-year {cov5}')

print('=== 2. MOE shrinkage: median relative margin on race 25-34 cells ===')
r1=race_relmoe(m1); r5=race_relmoe(m5)
print(f'  {"race":8s} {"1-yr":>7s} {"5-yr":>7s} {"ratio":>6s}')
for r in ('white','black','hisp','asian'):
    a,b=r1[r],r5[r]
    if a and b: print(f'  {r:8s} {a*100:6.1f}% {b*100:6.1f}% {b/a:6.2f}')

def wpct(pairs,q):
    pairs=sorted(pairs); tot=sum(w for _,w in pairs); acc=0
    for v,w in pairs:
        acc+=w
        if acc>=q*tot: return v
    return pairs[-1][0]
def upct(vals,q):
    vals=sorted(vals); return vals[int(q*(len(vals)-1))]
def band(metros, node_of):
    wp=[]; uv=[]
    for x in metros.values():
        n=node_of(x)
        if not n: continue
        m=n['m'][1]+n['m'][2]; w=n['w'][1]+n['w'][2]
        if w<=0 or m<=0: continue
        wp.append((100*m/w, m+w)); uv.append(100*m/w)
    return (round(wpct(wp,.1)),round(wpct(wp,.5)),round(wpct(wp,.9)),
            round(upct(uv,.1)),round(upct(uv,.9)))

print('=== 3. §3 leverage bands (count-weighted p10/median/p90 | unweighted p10/p90) ===')
print('  Any single (1-yr base):', band(m1, lambda x:x['base']))
for r in ('white','hisp','black','asian'):
    print(f'  {r:8s} (5-yr):', band(m5, lambda x:x.get("race",{}).get(r)))
print('  (for comparison, 1-yr race:)')
for r in ('white','hisp','black','asian'):
    print(f'  {r:8s} (1-yr):', band(m1, lambda x:x.get("race",{}).get(r)))
