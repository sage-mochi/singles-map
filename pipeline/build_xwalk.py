import pandas as pd, requests, json, sys, time, os
import config

KEY=os.environ.get('CENSUS_API_KEY')
if not KEY: raise SystemExit('Set CENSUS_API_KEY (free: https://api.census.gov/data/key_signup.html)')
VALID={'01','02','04','05','06','08','09','10','11','12','13','15','16','17','18','19',
 '20','21','22','23','24','25','26','27','28','29','30','31','32','33','34','35','36','37',
 '38','39','40','41','42','44','45','46','47','48','49','50','51','53','54','55','56'}

def get(url, params, tries=5):
    for k in range(tries):
        try:
            r=requests.get(url,params=params,timeout=180)
            if r.status_code==200: return r.json()
            time.sleep(2*(k+1))
        except Exception:
            time.sleep(2*(k+1))
    raise RuntimeError('failed: '+url+' '+str(params.get('in')))

# county -> CBSA (MSAs only)
d=pd.read_excel('deli.xlsx', header=2, dtype=str); d.columns=[c.strip() for c in d.columns]
msa=d[d['Metropolitan/Micropolitan Statistical Area']=='Metropolitan Statistical Area']
county2cbsa={str(r['FIPS State Code']).zfill(2)+str(r['FIPS County Code']).zfill(3):r['CBSA Code']
             for _,r in msa.iterrows()}

# tract -> PUMA
rel=pd.read_csv('tract_puma.csv',dtype=str)
rel.columns=[c.strip().lstrip('\ufeff') for c in rel.columns]
rel=rel[rel['STATEFP'].isin(VALID)].copy()
rel['tract']=rel['STATEFP']+rel['COUNTYFP']+rel['TRACTCE']
rel['county']=rel['STATEFP']+rel['COUNTYFP']
rel['puma']=rel['STATEFP']+rel['PUMA5CE']
states=sorted(rel['STATEFP'].unique())

# tract pops (cached)
if os.path.exists('tract_pop.json'):
    pop=json.load(open('tract_pop.json'))
else:
    pop={}
    for st in states:
        rows=get(config.DECENNIAL_PL,
                 {'get':'P1_001N','for':'tract:*','in':f'state:{st}','key':KEY})
        idx={h:i for i,h in enumerate(rows[0])}
        for row in rows[1:]:
            pop[row[idx['state']]+row[idx['county']]+row[idx['tract']]]=int(row[idx['P1_001N']])
        sys.stdout.write('.'); sys.stdout.flush()
    json.dump(pop, open('tract_pop.json','w'))
    print(f'\ntract pops: {len(pop):,}')

rel['pop']=rel['tract'].map(pop).fillna(0).astype(int)
rel['cbsa']=rel['county'].map(county2cbsa).fillna('NONMETRO')

g=rel.groupby(['puma','cbsa'])['pop'].sum().reset_index()
tot=rel.groupby('puma')['pop'].sum().rename('ptot')
g=g.merge(tot,on='puma'); g['afact']=g['pop']/g['ptot']
xwalk={}
for _,r in g.iterrows():
    xwalk.setdefault(r['puma'],{})[r['cbsa']]=round(float(r['afact']),6)
json.dump(xwalk, open('puma_cbsa_xwalk.json','w'))

sums=[sum(v.values()) for v in xwalk.values()]
split=sum(1 for v in xwalk.values() if len([c for c in v if v[c]>0.005])>1)
metro_split=sum(1 for v in xwalk.values()
                if len([c for c in v if v[c]>0.005 and c!='NONMETRO'])>=1
                and len([c for c in v if v[c]>0.005])>1)
print(f'states:{len(states)} PUMAs:{len(xwalk):,} CBSAs(MSA):{len(set(county2cbsa.values()))}')
print(f'afact sum range [{min(sums):.4f},{max(sums):.4f}]')
print(f'PUMAs spanning >1 bucket: {split:,}  (of which touch metro+other: {metro_split:,})')
