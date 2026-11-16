"""Degradation test: odd but legal data must not crash the pipeline.

Uses a fake sql_query tool (no real company data) to check three situations the
private set could contain: a two-year history, an industry missing from
industry_medians, and a final year whose cost ledger is incomplete.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hw3  # noqa: E402


def table(header, rows):
    return "\n".join([" | ".join(header)] + [" | ".join(str(c) for c in r) for r in rows])


class FakeSQL:
    """Mimics the sql_query tool for a made-up company with the given tables."""

    def __init__(self, revenue, cost_months, medians, industry="Testing", all_medians=None):
        self.revenue = revenue
        self.cost_months = cost_months  # year -> (months covered, cos, sa, rd per month totals)
        self.medians = medians
        self.industry = industry
        self.all_medians = all_medians or []

    def invoke(self, payload):
        q = " ".join(str(payload.get("query", "")).lower().split())
        if "industry_medians" in q:  # before "from company": the subquery mentions both
            if "where industry" in q:
                return table(["revenue_growth_2y_pct", "operating_margin_pct"],
                             [[m[1], m[2]] for m in self.medians]) if self.medians else "(no rows)"
            return table(["revenue_growth_2y_pct", "operating_margin_pct"], self.all_medians)
        if "from company" in q:
            return table(["name", "industry"], [["Fake Co", self.industry]])
        if "from orders" in q:
            return table(["year", "revenue"], sorted(self.revenue.items()))
        if "from monthly_costs" in q:
            if "count(*)" in q:
                return table(["year", "months"], [[y, m[0]] for y, m in sorted(self.cost_months.items())])
            return table(
                ["year", "cost_of_sales", "selling_admin", "research_development"],
                [[y, m[1], m[2], m[3]] for y, m in sorted(self.cost_months.items())],
            )
        if "from quarterly_cash" in q:
            return table(["year", "ocf", "capex"], [[y, 30000, 5000] for y in sorted(self.revenue)])
        raise AssertionError(f"unexpected query: {q}")


def show(label, fake):
    print(f"\n{label}")
    ev = hw3.collect_database_evidence(fake)
    print(f"   years used {ev['years_used']}, growth {ev['growth_2y_pct']:.1f}% "
          f"vs median {ev['industry_growth_2y_pct']:.1f}%, margin {ev['operating_margin_pct']:.1f}% "
          f"vs {ev['industry_operating_margin_pct']:.1f}%")
    print(f"   benchmark: {ev['median_source']}")
    print(f"   notes: {ev['notes'] or 'none'}")
    print(f"   point 1 -> {hw3.market_judgement(ev)['verdict']} ({hw3.market_judgement(ev)['confidence']})")
    print(f"   point 5 -> {hw3.margin_judgement(ev)['verdict']} ({hw3.margin_judgement(ev)['confidence']})")


show(
    "A. Only two full years of history (FY2024, FY2025), costs complete",
    FakeSQL({"2024": 100000, "2025": 110000},
            {"2024": (12, 60000, 18000, 4000), "2025": (12, 60000, 20000, 5000)},
            [["Testing", 10.0, 12.0]]),
)

show(
    "B. The company's industry is missing from industry_medians",
    FakeSQL({"2024": 100000, "2025": 110000},
            {"2024": (12, 60000, 18000, 4000), "2025": (12, 60000, 20000, 5000)},
            [],
            all_medians=[[10.0, 12.0], [15.0, 9.0]]),
)

show(
    "C. The last year's cost ledger stops after eight months",
    FakeSQL({"2023": 100000, "2024": 105000, "2025": 110000},
            {"2023": (12, 60000, 18000, 4000), "2024": (12, 63000, 19000, 4200),
             "2025": (8, 42000, 13000, 2800)},
            [["Testing", 10.0, 12.0]]),
)

print("\nAll three degraded cases produced verdicts instead of raising.")
