#!/usr/bin/env python3
"""
build_map.py — assemble the deployable Singles Map HTML.

Injects the four data JSON files into the template's placeholders, verifies
no placeholder remains, optionally runs `node --check` on the embedded script,
and writes the self-contained HTML that GitHub Pages serves.

Run from anywhere:  python pipeline/build_map.py
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root (script lives in pipeline/)

# placeholder -> data filename
INJECT = {
    "__DATA__":     "byage_min.json",
    "__STATES__":   "states_v2.json",
    "__ANALYSIS__": "analysis_data.json",
    "__M3__":       "pums_metro_m3.json",   # PUMS cross-tabs (economic/race/edu) + MOE
    "__AGE__":      "pums_metro_age.json",  # single-year-of-age single counts + MOE (seeker mode)
    "__KERNEL__":   "age_kernel.json",      # empirical partner-age-gap kernel
}
TEMPLATE_NAME = "singles_age2_template.html"
OUTPUT        = ROOT / "site" / "index.html"

# Search a few likely locations so this works in the repo layout AND a flat folder.
SEARCH_DIRS = [ROOT, ROOT/"data", ROOT/"pipeline", ROOT/"pipeline"/"template", Path.cwd()]

def find(name):
    for d in SEARCH_DIRS:
        p = d / name
        if p.exists():
            return p
    sys.exit(f"ERROR: could not find {name} in {[str(d) for d in SEARCH_DIRS]}")

def main():
    tpl = find(TEMPLATE_NAME).read_text(encoding="utf-8")
    out = tpl
    for ph, fname in INJECT.items():
        if ph not in out:
            sys.exit(f"ERROR: placeholder {ph} missing from template")
        out = out.replace(ph, find(fname).read_text(encoding="utf-8").strip())

    leftover = [ph for ph in INJECT if ph in out]
    if leftover:
        sys.exit(f"ERROR: unfilled placeholders remain: {leftover}")

    # Optional JS syntax check on the last <script> block (skipped if node absent).
    if shutil.which("node"):
        scripts = re.findall(r"<script>(.*?)</script>", out, re.S)
        if scripts:
            chk = ROOT / ".build_check.js"
            chk.write_text(scripts[-1], encoding="utf-8")
            r = subprocess.run(["node", "--check", str(chk)], capture_output=True, text=True)
            chk.unlink(missing_ok=True)
            if r.returncode != 0:
                sys.exit(f"ERROR: embedded JS failed node --check:\n{r.stderr}")
            print("JS syntax OK")
    else:
        print("note: node not found — skipping JS syntax check")

    # Re-verify embedded data (sanity).
    try:
        d = json.loads(find("byage_min.json").read_text(encoding="utf-8"))
        n = len(d.get("metros", []))
        cities = sum(1 for m in d["metros"] if m.get("city"))
        print(f"embedded metro markers: {n} (incl. {cities} city insets)")
    except Exception as e:
        print(f"warn: could not re-verify byage_min.json ({e})")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(out, encoding="utf-8")
    print(f"wrote {OUTPUT}  ({len(out)//1024} KB)")
    print("Deploy: commit site/index.html — that is the only file Pages serves.")

if __name__ == "__main__":
    main()
