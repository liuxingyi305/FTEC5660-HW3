"""Independent audit of results.csv.

1. Rebuilds points 1 and 5 from the tools with different SQL and checks results.csv.
2. Tests whether an absolute cut-off could explain the answer key.
3. Measures tolerance to error in the industry benchmark (old rule vs new rule).
4. Checks that the growth convention (simple vs compounded) does not matter.
5. Reports how far each verdict sits from the line, and its confidence.

Read-only: prints a report and writes nothing.
"""
import csv
import json
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
    r23, r25 = f(rev["r23"]), f(rev["r25"])
    c, s, rd = f(cost["c25"]), f(cost["s25"]), f(cost["rd25"])
    ev = hw3.collect_database_evidence(sql)
    data[folder.name] = dict(
        industry=industry,
        growth=(r25 / r23 - 1) * 100,
        margin=(r25 - c - s - rd) / r25 * 100,
        med_g=f(med["g"]),
        med_m=f(med["m"]),
        gt=GT[folder.name]["points"],
        months=int(cost["months25"]),
        j1=hw3.market_judgement(ev),
        j5=hw3.margin_judgement(ev),
    )

print("=" * 78)
print("1. VERDICT AUDIT: does results.csv agree with an independent recomputation?")
print("=" * 78)
mismatch = 0
for name, d in data.items():
    v1 = "PASS" if d["growth"] >= d["med_g"] else "FAIL"
    v5 = "PASS" if d["margin"] >= d["med_m"] else "FAIL"
    got = RESULTS[name]["points"].split()
    mismatch += not (got[0] == f"1:{v1}" and got[1] == f"5:{v5}")
print(f"   points 1 and 5 in results.csv reproduced independently: {20 - mismatch}/20")
print(f"   every 2025 cost ledger covers 12 months: {all(d['months'] == 12 for d in data.values())}")

print()
print("=" * 78)
print("2. NO-ABSOLUTE-THRESHOLD TEST: could a fixed cut-off explain the answer key?")
print("=" * 78)
for point, key, label in [(1, "growth", "growth %"), (5, "margin", "margin %")]:
    vals = [(d[key], d["gt"][str(point)]) for d in data.values()]
    workable = [t for t in sorted(v for v, _ in vals)
                if all((v >= t) == (lab == "PASS") for v, lab in vals)]
    print(f"   point {point} ({label}): {len(workable)} cut-off(s) separate the 20 answers "
          f"-> {'a fixed bar is possible' if workable else 'NONE: the key must be relative to the industry'}")

print()
print("=" * 78)
print("3. TOLERANCE TO BENCHMARK ERROR: shift the industry median, count changed verdicts")
print("=" * 78)


def old_market(d, delta):
    """The one-comparison rule: verdict = growth above the median or not."""
    return "PASS" if d["growth"] >= d["med_g"] + delta else "FAIL"


def new_market(d, delta):
    """The shipped rule: outside the band the ratio decides, inside it corroboration does."""
    gap = d["growth"] - (d["med_g"] + delta)
    if abs(gap) >= hw3.GROWTH_BAND:
        return "PASS" if gap > 0 else "FAIL"
    return d["j1"]["verdict"]


def old_margin(d, delta):
    return "PASS" if d["margin"] >= d["med_m"] + delta else "FAIL"


def new_margin(d, delta):
    gap = d["margin"] - (d["med_m"] + delta)
    if abs(gap) >= hw3.MARGIN_BAND:
        return "PASS" if gap > 0 else "FAIL"
    return d["j5"]["verdict"]


print(f"   {'median shifted by':20}{'p1 old':>10}{'p1 new':>10}{'p5 old':>10}{'p5 new':>10}")
totals = {"1 old": 0, "1 new": 0, "5 old": 0, "5 new": 0}
for delta in (-3.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 3.0):
    o1 = sum(old_market(d, delta) != old_market(d, 0) for d in data.values())
    n1 = sum(new_market(d, delta) != new_market(d, 0) for d in data.values())
    o5 = sum(old_margin(d, delta) != old_margin(d, 0) for d in data.values())
    n5 = sum(new_margin(d, delta) != new_margin(d, 0) for d in data.values())
    totals["1 old"] += o1
    totals["1 new"] += n1
    totals["5 old"] += o5
    totals["5 new"] += n5
    print(f"   {delta:+.1f} points{'':9}{o1:>10}{n1:>10}{o5:>10}{n5:>10}")
print(f"   {'total':20}{totals['1 old']:>10}{totals['1 new']:>10}{totals['5 old']:>10}{totals['5 new']:>10}")

print()
print("=" * 78)
print("4. CONVENTION INVARIANCE: simple two-year growth vs compound annual growth")
print("=" * 78)
flips = 0
for d in data.values():
    simple = d["growth"] >= d["med_g"]
    cagr = (1 + d["growth"] / 100) ** 0.5 >= (1 + d["med_g"] / 100) ** 0.5
    flips += simple != cagr
print(f"   verdicts that change when both sides are written as a CAGR: {flips}/20")

print()
print("=" * 78)
print("5. DISTANCE TO THE LINE AND CONFIDENCE")
print("=" * 78)
gaps = []
for name, d in data.items():
    gaps.append((abs(d["growth"] - d["med_g"]), "point 1", name, d["growth"], d["med_g"]))
    gaps.append((abs(d["margin"] - d["med_m"]), "point 5", name, d["margin"], d["med_m"]))
gaps.sort()
for gap, point, name, val, med in gaps[:4]:
    print(f"   closest: {point} {name:26} {val:6.1f} vs median {med:5.1f}  (gap {gap:.2f} pts)")
high = sum(d["j1"]["confidence"] == "high" for d in data.values()) + sum(
    d["j5"]["confidence"] == "high" for d in data.values()
)
print(f"   decided on the ratio alone (high confidence): {high}/40; "
      f"inside the band, needing corroboration: {40 - high}/40")
