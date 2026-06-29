"""
PUMS Rebuild — Milestone 1 prototype
------------------------------------
Goal: prove the recode + weighting reproduce the published B12002 numbers,
and demonstrate the unlock (race x age, education x age) that published
tables cannot provide. State-level only -- the PUMA->CBSA crosswalk is M2.

Validation states: California (06) and New York (36).
MOE demo (successive-difference replication) shown for one state.
"""
import requests, io, sys, os
import pandas as pd
import numpy as np

KEY = os.environ.get("CENSUS_API_KEY")
if not KEY: raise SystemExit("Set CENSUS_API_KEY (free: https://api.census.gov/data/key_signup.html)")
YEAR = "2024"
PUMS = f"https://api.census.gov/data/{YEAR}/acs/acs1/pums"
TBL  = f"https://api.census.gov/data/{YEAR}/acs/acs1"

BANDS = ["20-24","25-29","30-34","35-39","40-44","45-49","50-54","55-59","60-64"]
BAND_EDGES = [20,25,30,35,40,45,50,55,60,65]
STATES = {"06":"California", "36":"New York"}

# ---------------------------------------------------------------- PUMS fetch
def fetch_pums(state, cols):
    """One state, given columns + PWGTP. Returns DataFrame."""
    params = {"get": ",".join(cols), "for": "public use microdata area:*",
              "in": f"state:{state}", "key": KEY}
    r = requests.get(PUMS, params=params, timeout=300); r.raise_for_status()
    rows = r.json()
    return pd.DataFrame(rows[1:], columns=rows[0])

# ---------------------------------------------------------------- recode
def recode(df):
    df = df.copy()
    df["AGEP"] = df["AGEP"].astype(int)
    df["PWGTP"] = df["PWGTP"].astype(int)
    df["single"] = df["MAR"].astype(int).ne(1)              # not currently married
    df["sex"]    = np.where(df["SEX"].astype(int).eq(1), "m", "w")
    df["band"]   = pd.cut(df["AGEP"], BAND_EDGES, right=False, labels=BANDS)
    hisp = df["HISP"].ne("01")
    rac  = df["RAC1P"].astype(int)
    df["race"] = np.select(
        [hisp, rac.eq(1), rac.eq(2), rac.eq(6), rac.eq(9)],
        ["Hispanic","White","Black","Asian","Two or more"], default="Other")
    df["ba_plus"] = df["SCHL"].astype(int).ge(21)           # bachelor's+
    return df

# ------------------------------------------------ weighted single by band/sex
def single_by_band(df, extra=None):
    keys = ["band","sex"] + (extra or [])
    g = (df[df["single"] & df["band"].notna()]
         .groupby(keys, observed=True)["PWGTP"].sum().astype(int))
    return g

# ----------------------------------------- published B12002 (state, by band)
def published_single(state):
    r = requests.get(TBL, params={"get":"group(B12002)","for":f"state:{state}","key":KEY},
                     timeout=120); r.raise_for_status()
    row = dict(zip(r.json()[0], r.json()[1]))
    def val(n):
        v = row.get(f"B12002_{n:03d}E")
        try: x=int(v); return max(x,0)
        except: return 0
    # male single per band i: NM 6+i, SEP 38+i, WID 68+i, DIV 83+i ; female +block
    out = {}
    for i,b in enumerate(BANDS):
        out[(b,"m")] = sum(val(s+i) for s in (6,38,68,83))
        out[(b,"w")] = sum(val(s+i) for s in (99,131,161,176))
    return pd.Series(out)

# ---------------------------------------------------------------- MOE (SDR)
def moe_demo(state, band, sex):
    """Successive-difference replication MOE for single <sex> in <band>, one state.
    Pulls PWGTP + PWGTP1..80 in chunks, joined on SERIALNO+SPORDER."""
    base = ["SERIALNO","SPORDER","SEX","AGEP","MAR","RAC1P","HISP","SCHL","PWGTP"]
    df = fetch_pums(state, base)
    reps = [f"PWGTP{i}" for i in range(1,81)]
    for chunk in (reps[:40], reps[40:]):
        d = fetch_pums(state, ["SERIALNO","SPORDER"]+chunk)
        df = df.merge(d, on=["SERIALNO","SPORDER"], how="left")
    df = recode(df)
    mask = df["single"] & df["band"].eq(band) & df["sex"].eq("w" if sex=="w" else "m")
    X  = df.loc[mask,"PWGTP"].sum()
    Xr = np.array([df.loc[mask,f"PWGTP{i}"].astype(int).sum() for i in range(1,81)])
    se = np.sqrt(0.05 * ((Xr - X)**2).sum())
    return int(X), int(round(1.645*se))

# ================================================================== run
if __name__ == "__main__":
    PCOLS = ["SEX","AGEP","MAR","RAC1P","HISP","SCHL","PWGTP"]
    print(f"ACS {YEAR} 1-year PUMS — Milestone 1\n" + "="*64)

    pums = {}
    for st, name in STATES.items():
        print(f"\nFetching PUMS {name} ({st}) ...", flush=True)
        df = recode(fetch_pums(st, PCOLS))
        pums[st] = df
        print(f"  {len(df):,} person records")

    # ---- VALIDATION: PUMS vs published, single men/women 20-64 by band ----
    print("\n" + "="*64 + "\nVALIDATION  (PUMS weighted  vs  published B12002)")
    for st, name in STATES.items():
        p = single_by_band(pums[st])
        pub = published_single(st)
        print(f"\n  {name}")
        print(f"  {'band':6s} {'sex':3s} {'PUMS':>10s} {'published':>10s} {'diff%':>7s}")
        worst = 0
        for b in BANDS:
            for sx in ("m","w"):
                pv = int(p.get((b,sx),0)); pb = int(pub.get((b,sx),0))
                d = 100*(pv-pb)/pb if pb else 0; worst=max(worst,abs(d))
                print(f"  {b:6s} {sx:3s} {pv:>10,} {pb:>10,} {d:>+6.1f}%")
        tot_p = int(p.sum()); tot_b = int(pub.sum())
        print(f"  TOTAL 20-64 single: PUMS {tot_p:,}  published {tot_b:,}  "
              f"diff {100*(tot_p-tot_b)/tot_b:+.2f}%  (worst band {worst:.1f}%)")

    # ---- UNLOCK: race x age (published tables CANNOT do this) ----
    print("\n" + "="*64 + "\nUNLOCK  —  single men per 100 women, by RACE x AGE (California)")
    df = pums["06"]
    print(f"  {'race':12s}" + "".join(f"{b:>8s}" for b in BANDS[:6]))
    for race in ["White","Black","Asian","Hispanic","Two or more"]:
        sub = df[df["single"] & df["race"].eq(race) & df["band"].notna()]
        g = sub.groupby(["band","sex"], observed=True)["PWGTP"].sum()
        cells=[]
        for b in BANDS[:6]:
            m=g.get((b,"m"),0); w=g.get((b,"w"),0)
            cells.append(f"{100*m/w:>7.0f}" if w else "     -- ")
        print(f"  {race:12s}" + "".join(cells))

    # ---- UNLOCK: education x age ----
    print("\nUNLOCK  —  single men per 100 women, by EDUCATION x AGE (California)")
    for lab,flag in [("Bachelor's+",True),("No bachelor's",False)]:
        sub = df[df["single"] & df["ba_plus"].eq(flag) & df["band"].notna()]
        g = sub.groupby(["band","sex"], observed=True)["PWGTP"].sum()
        cells=[f"{100*g.get((b,'m'),0)/g.get((b,'w'),1):>7.0f}" for b in BANDS[:6]]
        print(f"  {lab:14s}" + "".join(cells))

    # ---- MOE demo (one cell, one state) ----
    print("\n" + "="*64 + "\nMOE DEMO  (successive-difference replication)")
    x, m = moe_demo("36", "25-29", "m")
    print(f"  New York, single men 25-29:  {x:,}  ±{m:,}  (90% MOE)")
    print(f"  => estimate is {x:,} with margin ±{100*m/x:.1f}%")
    print("\nDone.")
