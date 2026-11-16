#!/usr/bin/env python3
"""Compare candidate metric definitions for points 1 and 5 on the public set.

The point of this script is to answer "is the metric I shipped the best bet?"
without pretending that public-set accuracy alone settles it. Every candidate
is scored on three things:

  hit    - verdicts matching public_test/ground_truth.json (out of 20)
  edge   - the smallest distance from a company to the rule's line. A rule that
           is right by 0.05pp is a rule that will be wrong on the next company.
  flip   - how many of the 20 verdicts change when the benchmark is nudged by
           1pp, and again when the measurement window is moved by a year. A rule
           that only works at one exact benchmark value is not a rule.

Company data is read through the same ``sql_query`` tool the agent uses, not by
opening company.db directly (Section 2 of the homework requires that of the
agent, and there is no reason for the audit to hold itself to a lower standard).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hw3  # noqa: E402

DATA = Path("public_test")
KEY = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))


def load(folder: Path) -> dict:
    """Revenue/cost series by calendar year, keeping only complete years."""
    sql = hw3.make_tools(folder)[2]  # list_documents, read_document, sql_query, interview

    def q(query: str) -> list[dict[str, str]]:
        return hw3._maybe_rows(sql, query)

    months = {r["y"]: int(float(r["n"])) for r in q(
        "SELECT substr(month,1,4) AS y, COUNT(*) AS n FROM monthly_costs GROUP BY y")}
    revenue = {r["y"]: float(r["v"]) for r in q(
        "SELECT substr(order_date,1,4) AS y, SUM(amount) AS v FROM orders GROUP BY y")}
    costs = {r["y"]: (float(r["c"]), float(r["s"]), float(r["r"])) for r in q(
        "SELECT substr(month,1,4) AS y, SUM(cost_of_sales) AS c, SUM(selling_admin) AS s, "
        "SUM(research_development) AS r FROM monthly_costs GROUP BY y")}
    cash = {r["y"]: float(r["o"]) for r in q(
        "SELECT substr(quarter,1,4) AS y, SUM(operating_cash_flow) AS o "
        "FROM quarterly_cash GROUP BY y")}
    units = {r["y"]: float(r["n"]) for r in q(
        "SELECT substr(month,1,4) AS y, SUM(units_shipped) AS n FROM returns GROUP BY y")}
    med = q("SELECT c.industry AS i, m.revenue_growth_2y_pct AS g, "
            "m.operating_margin_pct AS m FROM company c "
            "JOIN industry_medians m ON m.industry = c.industry")[0]
    full = sorted(y for y, n in months.items() if n >= 12 and y in revenue)
    return {
        "industry": med["i"], "med_g": float(med["g"]), "med_m": float(med["m"]),
        "years": full, "revenue": revenue, "costs": costs, "cash": cash, "units": units,
    }


def margin(d: dict, y: str) -> float:
    c, s, r = d["costs"][y]
    return (d["revenue"][y] - c - s - r) / d["revenue"][y] * 100


def gross(d: dict, y: str) -> float:
    c, _, _ = d["costs"][y]
    return (d["revenue"][y] - c) / d["revenue"][y] * 100


def norm(base: float, end: float, span: int) -> float:
    """Growth over `span` years restated as a two-year rate."""
    if base <= 0:
        return float("nan")
    return ((end / base) ** (2.0 / span) - 1) * 100


def metrics(d: dict) -> dict[str, float]:
    ys = d["years"]
    first, last = ys[0], ys[-1]
    span = int(last) - int(first)
    rev, uni = d["revenue"], d["units"]
    yoy = [
        (rev[b] / rev[a] - 1) * 100 for a, b in zip(ys, ys[1:]) if rev[a] > 0
    ]
    ocf = d["cash"].get(last, 0.0)
    return {
        "g_as_reported": (rev[last] / rev[first] - 1) * 100 if span == 2 else float("nan"),
        "g_2y_annualised": norm(rev[first], rev[last], span),
        "g_mean_yoy": statistics.mean(yoy) if yoy else float("nan"),
        "g_last_year_only": yoy[-1] if yoy else float("nan"),
        "g_units_2y": norm(uni.get(first, 0), uni.get(last, 0), span),
        "m_last_year": margin(d, last),
        "m_two_year_avg": statistics.mean(margin(d, y) for y in ys[-2:]),
        "m_weakest_year": min(margin(d, y) for y in ys),
        "gross_last_year": gross(d, last),
        "m_change_pp": margin(d, last) - margin(d, first),
        "ocf_margin": ocf / rev[last] * 100,
    }


POINT1_METRICS = ["g_as_reported", "g_2y_annualised", "g_mean_yoy",
                  "g_last_year_only", "g_units_2y"]
POINT5_METRICS = ["m_last_year", "m_two_year_avg", "m_weakest_year",
                  "gross_last_year", "m_change_pp", "ocf_margin"]


def score(values: dict[str, float], bench: dict[str, float], key: dict[str, str],
          nudge: float = 0.0) -> dict:
    hits, edges, flips = 0, [], 0
    for cid, v in values.items():
        gap = v - (bench[cid] + nudge)
        edges.append(abs(gap))
        verdict = "PASS" if gap >= 0 else "FAIL"
        hits += verdict == key[cid]
        flips += (verdict != ("PASS" if v - bench[cid] >= 0 else "FAIL"))
    return {"hit": hits, "edge": min(edges), "flip": flips,
            "med_edge": statistics.median(edges)}


def main() -> None:
    folders = sorted(p for p in DATA.iterdir() if (p / "company.db").is_file())
    data = {p.name: load(p) for p in folders}
    key = {
        cid: KEY[cid if cid in KEY else cid]["points"]["1"] for cid in data
    }  # replaced below with per-point keys
    key1 = {cid: KEY[cid]["points"]["1"] for cid in data}
    key5 = {cid: KEY[cid]["points"]["5"] for cid in data}
    del key

    m = {cid: metrics(d) for cid, d in data.items()}

    # Benchmarks. The industry median that ships with each company is the only
    # peer number the homework actually gives us; the cross-industry median is a
    # plausible but weaker stand-in, kept here to show what it would cost.
    bench_g_industry = {cid: d["med_g"] for cid, d in data.items()}
    bench_m_industry = {cid: d["med_m"] for cid, d in data.items()}
    bench_g_all = {cid: statistics.median(bench_g_industry.values()) for cid in data}
    bench_m_all = {cid: statistics.median(bench_m_industry.values()) for cid in data}

    def report(title: str, names: list[str], keypt: dict, benches: list[tuple[str, dict]]):
        print(f"\n{title}")
        print(f"{'metric':<20}{'benchmark':<26}{'hit':>6}{'edge':>8}{'flip@1pp':>10}{'flip@2pp':>10}")
        rows = []
        for name in names:
            vals = {cid: m[cid][name] for cid in data}
            for bname, bench in benches:
                s1 = score(vals, bench, keypt, nudge=1.0)
                s2 = score(vals, bench, keypt, nudge=2.0)
                base = score(vals, bench, keypt)
                print(f"{name:<20}{bname:<26}{base['hit']:>4}/20{base['edge']:>8.2f}"
                      f"{s1['flip']:>10}{s2['flip']:>10}")
                rows.append((base["hit"], base["edge"], s1["flip"], name, bname))
        print("  best by (hit desc, edge desc, flip asc): "
              f"{max(rows, key=lambda r: (r[0], r[1], -r[2]))}")

    report("Point 1 - market potential (sizable increase in sales)", POINT1_METRICS, key1,
           [("industry median (given)", bench_g_industry),
            ("cross-industry median", bench_g_all)])
    report("Point 5 - worthwhile profit margin", POINT5_METRICS, key5,
           [("industry median (given)", bench_m_industry),
            ("cross-industry median", bench_m_all)])

    # What it costs to move the measurement window: recompute growth from the
    # first complete year to the second-to-last, i.e. ignore the latest year.
    print("\nWindow sensitivity, point 1 (last year dropped from the growth calc)")
    moved = {}
    for cid, d in data.items():
        ys = d["years"]
        if len(ys) >= 3:
            moved[cid] = norm(d["revenue"][ys[0]], d["revenue"][ys[-2]], int(ys[-2]) - int(ys[0]))
        else:
            moved[cid] = m[cid]["g_2y_annualised"]
    base = score({cid: m[cid]["g_2y_annualised"] for cid in data}, bench_g_industry, key1)
    alt = score(moved, bench_g_industry, key1)
    changed = sum(
        (moved[c] - bench_g_industry[c] >= 0) != (m[c]["g_2y_annualised"] - bench_g_industry[c] >= 0)
        for c in data
    )
    print(f"   shipped window: {base['hit']}/20   shorter window: {alt['hit']}/20   "
          f"verdicts changed: {changed}/20")


if __name__ == "__main__":
    main()
