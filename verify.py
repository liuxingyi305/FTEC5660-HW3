"""Independent audit of results.csv.

Rebuilds every number from the tools with different SQL, checks the verdicts in
results.csv were not touched, and stress-tests the two rules against alternative
but defensible definitions. Read-only: prints a report, writes nothing.
"""
import csv
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hw3  # noqa: E402

ROOT = Path(__file__).parent / "public_test"
GT = json.loads((ROOT / "ground_truth.json").read_text())
RESULTS = {r["company"]: r for r in csv.DictReader(open(Path(__file__).parent / "results.csv"))}


def rows(text):
    lines = [l for l in str(text).strip().splitlines() if l.strip() and not l.startswith("(")]
    head = [c.strip() for c in lines[0].split("|")]
    out = []
    for line in lines[1:]:
        if line.startswith("..."):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) == len(head):
            out.append(dict(zip(head, cells)))
    return out


def f(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


REV_SQL = """SELECT
  ROUND(SUM(CASE WHEN substr(order_date,1,4)='2023' THEN amount END),2) AS r23,
  ROUND(SUM(CASE WHEN substr(order_date,1,4)='2024' THEN amount END),2) AS r24,
  ROUND(SUM(CASE WHEN substr(order_date,1,4)='2025' THEN amount END),2) AS r25
FROM orders"""
COST_SQL = """SELECT
  ROUND(SUM(CASE WHEN month LIKE '2025%' THEN cost_of_sales END),2) AS c25,
  ROUND(SUM(CASE WHEN month LIKE '2025%' THEN selling_admin END),2) AS s25,
  ROUND(SUM(CASE WHEN month LIKE '2025%' THEN research_development END),2) AS rd25,
  COUNT(CASE WHEN month LIKE '2025%' THEN 1 END) AS months25
FROM monthly_costs"""
MED_SQL = "SELECT revenue_growth_2y_pct AS g, operating_margin_pct AS m FROM industry_medians WHERE industry='%s'"

data = {}
for folder in sorted(p for p in ROOT.iterdir() if p.is_dir()):
    tools = hw3.make_tools(folder)
    sql = tools[2]
    rev = rows(sql.invoke({"query": REV_SQL}))[0]
    cost = rows(sql.invoke({"query": COST_SQL}))[0]
    industry = rows(sql.invoke({"query": "SELECT industry FROM company"}))[0]["industry"]
    med = rows(sql.invoke({"query": MED_SQL % industry.replace("'", "''")}))[0]
    r23, r24, r25 = f(rev["r23"]), f(rev["r24"]), f(rev["r25"])
    c, s, rd = f(cost["c25"]), f(cost["s25"]), f(cost["rd25"])
    data[folder.name] = dict(
        industry=industry, r23=r23, r24=r24, r25=r25, c=c, s=s, rd=rd,
        months=int(cost["months25"]), med_g=f(med["g"]), med_m=f(med["m"]),
        gt=GT[folder.name]["points"],
    )

print("=" * 78)
print("1. VERDICT AUDIT: does results.csv match an independent recomputation?")
print("=" * 78)
mismatch = 0
for name, d in data.items():
    g2 = (d["r25"] / d["r23"] - 1) * 100
    m = (d["r25"] - d["c"] - d["s"] - d["rd"]) / d["r25"] * 100
    v1 = "PASS" if g2 >= d["med_g"] else "FAIL"
    v5 = "PASS" if m >= d["med_m"] else "FAIL"
    got = RESULTS[name]["points"]
    ok = got == f"1:{v1} 5:{v5} 7:{got.split()[-1].split(':')[1]}"
    ok = got.split()[0] == f"1:{v1}" and got.split()[1] == f"5:{v5}"
    mismatch += not ok
    if d["months"] != 12:
        print(f"   !! {name}: monthly_costs has {d['months']} months for 2025")
print(f"   point 1 and 5 verdicts in results.csv reproduced independently: {20 - mismatch}/20")

print()
print("=" * 78)
print("2. NO-ABSOLUTE-THRESHOLD TEST: could a fixed cut-off explain the answer key?")
print("=" * 78)
for point, key, med_key, label in [(1, "g2", "med_g", "growth %"), (5, "m", "med_m", "margin %")]:
    vals = []
    for name, d in data.items():
        v = (d["r25"] / d["r23"] - 1) * 100 if point == 1 else (d["r25"] - d["c"] - d["s"] - d["rd"]) / d["r25"] * 100
        vals.append((v, d["gt"][str(point)]))
    candidates = sorted({v for v, _ in vals})
    workable = [t for t in candidates if all((v >= t) == (lab == "PASS") for v, lab in vals)]
    print(f"   point {point} ({label}): {len(workable)} fixed cut-off(s) separate the 20 answers "
          f"-> {'any' if workable else 'NONE, so the key must be relative (industry benchmark)'}")

print()
print("=" * 78)
print("3. DEFINITION SENSITIVITY: do the verdicts survive other sensible definitions?")
print("=" * 78)
flips = {"g_1y": 0, "g_cagr": 0, "m_no_rd": 0, "m_gross": 0}
for name, d in data.items():
    g2 = (d["r25"] / d["r23"] - 1) * 100
    g1 = (d["r24"] / d["r23"] - 1) * 100
    gc = ((d["r25"] / d["r23"]) ** 0.5 - 1) * 100
    base_m = (d["r25"] - d["c"] - d["s"] - d["rd"]) / d["r25"] * 100
    m_no_rd = (d["r25"] - d["c"] - d["s"]) / d["r25"] * 100
    m_gross = (d["r25"] - d["c"]) / d["r25"] * 100
    flips["g_1y"] += ("PASS" if g1 >= d["med_g"] else "FAIL") != ("PASS" if g2 >= d["med_g"] else "FAIL")
    flips["g_cagr"] += ("PASS" if gc >= d["med_g"] else "FAIL") != ("PASS" if g2 >= d["med_g"] else "FAIL")
    flips["m_no_rd"] += ("PASS" if m_no_rd >= d["med_m"] else "FAIL") != ("PASS" if base_m >= d["med_m"] else "FAIL")
    flips["m_gross"] += ("PASS" if m_gross >= d["med_m"] else "FAIL") != ("PASS" if base_m >= d["med_m"] else "FAIL")
print(f"   growth as 1-year (FY23->FY24) instead of 2-year: {flips['g_1y']}/20 verdicts change")
print(f"   growth as CAGR instead of simple 2-year change:  {flips['g_cagr']}/20 verdicts change")
print(f"   margin before R&D instead of after R&D:          {flips['m_no_rd']}/20 verdicts change")
print(f"   gross margin instead of operating margin:        {flips['m_gross']}/20 verdicts change")

print()
print("=" * 78)
print("4. PEER MEDIAN vs OFFICIAL MEDIAN: is the benchmark itself stable?")
print("=" * 78)
by_ind = {}
for d in data.values():
    by_ind.setdefault(d["industry"], []).append(d)
print(f"   {'industry':26}{'n':>3}{'med growth':>12}{'peer med':>10}{'med margin':>12}{'peer med':>10}")
for ind, ds in sorted(by_ind.items()):
    g = statistics.median([(d["r25"] / d["r23"] - 1) * 100 for d in ds])
    m = statistics.median([(d["r25"] - d["c"] - d["s"] - d["rd"]) / d["r25"] * 100 for d in ds])
    print(f"   {ind[:25]:26}{len(ds):>3}{ds[0]['med_g']:>12.1f}{g:>10.1f}{ds[0]['med_m']:>12.1f}{m:>10.1f}")

print()
print("=" * 78)
print("5. DISTANCE TO THE LINE: how much room does each verdict have?")
print("=" * 78)
gaps = []
for name, d in data.items():
    g2 = (d["r25"] / d["r23"] - 1) * 100
    m = (d["r25"] - d["c"] - d["s"] - d["rd"]) / d["r25"] * 100
    gaps.append((abs(g2 - d["med_g"]), "point 1", name, g2, d["med_g"]))
    gaps.append((abs(m - d["med_m"]), "point 5", name, m, d["med_m"]))
gaps.sort()
for gap, point, name, val, med in gaps[:6]:
    print(f"   closest: {point} {name:26} {val:6.1f} vs median {med:5.1f}  (gap {gap:.2f} pts)")
print(f"   median gap {statistics.median([g[0] for g in gaps]):.1f} pts, smallest {gaps[0][0]:.2f} pts")
