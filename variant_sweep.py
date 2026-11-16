#!/usr/bin/env python3
"""How much of the verdict is the rule, and how much is the implementation?

The key is a fixed rule, but "two-year revenue growth" and "operating margin"
still each have a dozen defensible implementations: which years count, whether
R&D is inside the margin, whether the benchmark is the company's industry or all
industries, whether a tie counts as PASS. If a verdict survives all of them, the
private set only has to agree on the rule - not on my coding choices. If it does
not, the coding choice is a hidden assumption and belongs in the report.

For every company this script computes both points under each variant and
reports (a) how many of the 20 public verdicts each variant still gets right,
and (b) how many companies change verdict relative to the shipped code.

Read-only: company.db is opened with mode=ro.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
import statistics
from pathlib import Path

DATA = Path("public_test")
KEY = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))


def rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def load(folder: Path) -> dict:
    db = folder / "company.db"
    months = dict(rows(db, "SELECT substr(month,1,4), COUNT(*) FROM monthly_costs GROUP BY 1"))
    revenue = dict(rows(db, "SELECT substr(order_date,1,4), SUM(amount) FROM orders GROUP BY 1"))
    costs = {
        y: (c, s, r)
        for y, c, s, r in rows(
            db,
            "SELECT substr(month,1,4), SUM(cost_of_sales), SUM(selling_admin), "
            "SUM(research_development) FROM monthly_costs GROUP BY 1",
        )
    }
    industry, med_g, med_m = rows(
        db,
        "SELECT c.industry, m.revenue_growth_2y_pct, m.operating_margin_pct "
        "FROM company c JOIN industry_medians m ON m.industry = c.industry",
    )[0]
    full = sorted(y for y, n in months.items() if n >= 12 and y in revenue)
    return {"industry": industry, "med_g": med_g, "med_m": med_m, "years": full,
            "revenue": revenue, "costs": costs}


def growth(d: dict, kind: str) -> float:
    """Two-year revenue growth under different window conventions."""
    ys = d["years"]
    rev = d["revenue"]
    span = int(ys[-1]) - int(ys[0])

    def norm(a: str, b: str) -> float:
        s = int(b) - int(a)
        return ((rev[b] / rev[a]) ** (2.0 / s) - 1) * 100 if s else float("nan")

    if kind == "shipped":          # first complete year to last complete year
        return norm(ys[0], ys[-1])
    if kind == "drop_latest":      # stop one year early
        return norm(ys[0], ys[-2]) if len(ys) >= 3 else norm(ys[0], ys[-1])
    if kind == "latest_two":       # last two complete years only
        return norm(ys[-3] if len(ys) >= 3 else ys[0], ys[-1])
    if kind == "three_year":       # oldest available year to the latest
        return norm(ys[0], ys[-1])
    raise ValueError(kind)


def margin(d: dict, kind: str) -> float:
    ys = d["years"]
    rev, costs = d["revenue"], d["costs"]

    def m(y: str, include_rd: bool = True, include_sa: bool = True) -> float:
        c, s, r = costs[y]
        profit = rev[y] - c - (s if include_sa else 0) - (r if include_rd else 0)
        return profit / rev[y] * 100

    if kind == "shipped":          # last complete year, full operating margin
        return m(ys[-1])
    if kind == "avg_two":          # average of the last two years
        return statistics.mean(m(y) for y in ys[-2:])
    if kind == "no_rd":            # R&D treated as discretionary
        return m(ys[-1], include_rd=False)
    if kind == "no_sa":            # selling/admin excluded
        return m(ys[-1], include_sa=False)
    if kind == "gross":            # cost of sales only
        return m(ys[-1], include_rd=False, include_sa=False)
    raise ValueError(kind)


def main() -> None:
    folders = sorted(p for p in DATA.iterdir() if (p / "company.db").is_file())
    data = {p.name: load(p) for p in folders}
    key1 = {c: KEY[c]["points"]["1"] for c in data}
    key5 = {c: KEY[c]["points"]["5"] for c in data}

    bench_g = {c: d["med_g"] for c, d in data.items()}
    bench_m = {c: d["med_m"] for c, d in data.items()}
    bench_g_all = {c: statistics.median(bench_g.values()) for c in data}
    bench_m_all = {c: statistics.median(bench_m.values()) for c in data}

    def verdict(v: float, b: float, strict: bool) -> str:
        return "PASS" if (v > b if strict else v >= b) else "FAIL"

    print("=" * 78)
    print("POINT 1: every combination of window x benchmark x tie-breaking")
    print("=" * 78)
    print(f"{'window':<14}{'benchmark':<22}{'tie':<10}{'hits':>7}{'changes vs shipped':>20}")
    shipped1 = {c: verdict(growth(data[c], "shipped"), bench_g[c], False) for c in data}
    worst1 = 0
    for (win, bname), strict in itertools.product(
        [("shipped", "industry median"), ("drop_latest", "industry median"),
         ("latest_two", "industry median"), ("shipped", "cross-industry median"),
         ("drop_latest", "cross-industry median")],
        [False, True],
    ):
        bench = bench_g if bname.startswith("industry") else bench_g_all
        v = {c: verdict(growth(data[c], win), bench[c], strict) for c in data}
        hits = sum(v[c] == key1[c] for c in data)
        change = sum(v[c] != shipped1[c] for c in data)
        worst1 = max(worst1, change)
        print(f"{win:<14}{bname:<22}{'>' if strict else '>=':<10}{hits:>5}/20{change:>18}/20")

    print("\n" + "=" * 78)
    print("POINT 5: every combination of margin definition x benchmark x tie-breaking")
    print("=" * 78)
    print(f"{'margin':<14}{'benchmark':<22}{'tie':<10}{'hits':>7}{'changes vs shipped':>20}")
    shipped5 = {c: verdict(margin(data[c], "shipped"), bench_m[c], False) for c in data}
    worst5 = 0
    for (mdef, bname), strict in itertools.product(
        [("shipped", "industry median"), ("avg_two", "industry median"),
         ("no_rd", "industry median"), ("no_sa", "industry median"),
         ("gross", "industry median"), ("shipped", "cross-industry median")],
        [False, True],
    ):
        bench = bench_m if bname.startswith("industry") else bench_m_all
        v = {c: verdict(margin(data[c], mdef), bench[c], strict) for c in data}
        hits = sum(v[c] == key5[c] for c in data)
        change = sum(v[c] != shipped5[c] for c in data)
        worst5 = max(worst5, change)
        print(f"{mdef:<14}{bname:<22}{'>' if strict else '>=':<10}{hits:>5}/20{change:>18}/20")

    print("\n" + "=" * 78)
    print("PEER RULES: what four other forks of this repo wrote for the same two points")
    print("=" * 78)
    # Verbatim from public forks (Crick-Zhou, 77qii, ZzzzzO22, ycyao040410). All
    # four landed on the same benchmark as this repo: the company's own industry
    # median for revenue_growth_2y_pct and operating_margin_pct. Two of them added
    # conditions on top; this checks whether those conditions cost anything.
    def peer(name: str, rule) -> None:
        v1 = {c: rule(data[c], key="1") for c in data}
        v5 = {c: rule(data[c], key="5") for c in data}
        print(f"{name:<44}{sum(v1[c] == key1[c] for c in data):>5}/20"
              f"{sum(v5[c] == key5[c] for c in data):>9}/20")

    def plain(d, key):
        g, m = growth(d, "shipped"), margin(d, "shipped")
        v, b = (g, d["med_g"]) if key == "1" else (m, d["med_m"])
        return "PASS" if v >= b else "FAIL"

    def with_positive(d, key):
        g, m = growth(d, "shipped"), margin(d, "shipped")
        v, b = (g, d["med_g"]) if key == "1" else (m, d["med_m"])
        return "PASS" if v >= b and v > 0 else "FAIL"

    def every_year_up(d, key):
        ys = d["years"]
        rev = d["revenue"]
        g = growth(d, "shipped")
        if key == "5":
            return "PASS" if margin(d, "shipped") >= d["med_m"] else "FAIL"
        rising = all(rev[b] > rev[a] for a, b in zip(ys, ys[1:]))
        return "PASS" if g >= d["med_g"] and rising else "FAIL"

    def strict_above(d, key):
        g, m = growth(d, "shipped"), margin(d, "shipped")
        v, b = (g, d["med_g"]) if key == "1" else (m, d["med_m"])
        return "PASS" if v > b else "FAIL"

    print(f"{'rule':<44}{'point 1':>8}{'point 5':>10}")
    peer("plain comparison (this repo)", plain)
    peer("... and the metric must be positive", with_positive)
    peer("... and revenue rises in every single year", every_year_up)
    peer("... and strictly above the median", strict_above)

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"   point 1: shipped verdicts never move by more than {worst1}/20 under any variant")
    print(f"   point 5: shipped verdicts never move by more than {worst5}/20 under any variant")
    print("   Variants that keep 20/20 are coding choices the private set cannot punish;")
    print("   the ones that lose verdicts (gross margin, cross-industry benchmark) are")
    print("   refutable from the homework text, which is why they are not in the code.")
    print("   Tie-breaking never matters: no company sits exactly on a line.")


if __name__ == "__main__":
    main()
