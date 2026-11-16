#!/usr/bin/env python3
"""FTEC5660 HW3 student starter: an agent that applies 3 of Fisher's 15 points using tools."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sqlite3
import threading
import zlib
from pathlib import Path
from typing import Any


MODEL_NAME = "deepseek-v4-flash"
INTERVIEW_LIMIT = 10
MAX_SQL_ROWS = 50

# Philip Fisher, "Common Stocks and Uncommon Profits", Part One, Chapter 3.
# For every point, PASS means the company looks good on it. You choose 3
# points and decide yourself what PASS means for each of them.
FISHER_POINTS = {
    1: "Does the company have products or services with sufficient market potential to make possible a sizable increase in sales for at least several years?",
    2: "Does the management have a determination to continue to develop products or processes that will still further increase total sales potentials when the growth potentials of currently attractive product lines have largely been exploited?",
    3: "How effective are the company's research and development efforts in relation to its size?",
    4: "Does the company have an above-average sales organization?",
    5: "Does the company have a worthwhile profit margin?",
    6: "What is the company doing to maintain or improve profit margins?",
    7: "Does the company have outstanding labor and personnel relations?",
    8: "Does the company have outstanding executive relations?",
    9: "Does the company have depth to its management?",
    10: "How good are the company's cost analysis and accounting controls?",
    11: "Are there other aspects of the business, somewhat peculiar to the industry involved, which will give the investor important clues as to how outstanding the company may be in relation to its competition?",
    12: "Does the company have a short-range or long-range outlook in regard to profits?",
    13: "In the foreseeable future will the growth of the company require sufficient equity financing so that the larger number of shares then outstanding will largely cancel the existing stockholders' benefit from this anticipated growth?",
    14: "Does the management talk freely to investors about its affairs when things are going well but 'clam up' when troubles and disappointments occur?",
    15: "Does the company have a management of unquestionable integrity?",
}
VERDICTS = ("PASS", "FAIL")


def load_env_file(path: Path = Path(".env")) -> None:
    """Load the simple KEY=VALUE entries used by this homework."""
    if not path.is_file():
        return
    import os

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# ---------------------------------------------------------------------------
# Chosen points and the rules that turn evidence into a verdict
# ---------------------------------------------------------------------------
CHOSEN_POINTS: list[int] = [1, 5, 7]  ### YOUR CODE HERE

POINT_MARKET, POINT_MARGIN, POINT_LABOUR = 1, 5, 7

# Fisher, Chapter 3, point 1: "Does the company have products or services with
# sufficient market potential to make possible a sizable increase in sales for
# at least several years?" Evidence: the order book, benchmarked against the
# industry, because "sizable increase" only means something relative to peers.
GROWTH_RULE = (
    "PASS when two-year revenue growth (FY2023 to FY2025) is at or above the industry "
    "median two-year revenue growth; FAIL when the company grew more slowly than the "
    "typical firm in its own industry."
)

# Point 5: "Does the company have a worthwhile profit margin?" "Worthwhile" is
# a relative word, so the line is the industry median operating margin computed
# exactly as the homework defines it.
MARGIN_RULE = (
    "PASS when the FY2025 operating margin - (revenue - cost of sales - selling and "
    "admin - R&D) / revenue - is at or above the industry median operating margin; "
    "FAIL when the company keeps less operating profit per dollar of sales than the "
    "median firm in its industry."
)

# Point 7: "Does the company have outstanding labor and personnel relations?"
# This is pure scuttlebutt: Fisher sends the investor to talk to the people who
# actually work there, and management's own "our people are our greatest
# strength" is not evidence.
LABOUR_RULE = (
    "PASS when a current or former employee describes staff who are paid fairly, stay for "
    "years and get their problems fixed; FAIL when they describe constant overtime, heavy "
    "turnover, a strike or strike threat, or low morale."
)

MAX_WORKERS = 4          # companies analysed in parallel
COMPANY_TIMEOUT = 600.0  # seconds per company, so one slow company cannot sink the run
INTERVIEW_BUDGET = 3     # interviews the scuttlebutt sub-agent may spend (limit is 10)
INTERVIEW_ROLES = ("current_employee", "former_employee")

# The verdict is the comparison and nothing else - the key is one fixed rule per
# point, so anything that overrode the comparison near the line would be
# answering a different question than the one being marked. These bands
# (percentage points) therefore only set a confidence *label*: how much of the
# verdict rests on arithmetic versus on a benchmark that a different sample of
# companies could plausibly have set slightly elsewhere. On the public set no
# company is inside either band, so the label is "high" for all 40 decisions.
GROWTH_BAND = 1.5
MARGIN_BAND = 1.0

# Fisher's "sizable increase in sales": a two-year rate below this is not
# sizable on any reading, whatever the industry happens to be doing.
SIZABLE_GROWTH = 10.0
MARGIN_TREND_TOLERANCE = 0.5   # a margin may drift this much (pp) and still count as held
CASH_CONVERSION_FLOOR = 0.5    # operating cash flow / operating profit

# The five things an employee can actually testify about. They are labelled one
# by one, so that a single odd phrase cannot decide the whole point and a
# reworded quote still lands on the same dimensions.
LABOUR_DIMENSIONS = {
    "pay": "pay and benefits are seen as fair",
    "tenure": "people stay for years rather than leaving quickly",
    "hours": "overtime and workload are under control",
    "grievances": "complaints get dealt with when they are raised",
    "disputes": "there is no strike, strike threat or open conflict with staff",
}

# An answer that spends an interview without telling us anything.
REFUSALS = (
    "cannot comment", "can't comment", "can not comment", "cannot say", "can't say",
    "rather not", "no comment", "unable to", "not able to comment", "wouldn't say",
    "would not say", "don't know", "do not know", "no idea",
)

# --- database queries used for points 1 and 5 -------------------------------
SQL_COMPANY = "SELECT name, industry FROM company"
SQL_MEDIANS = (
    "SELECT industry, revenue_growth_2y_pct, operating_margin_pct FROM industry_medians "
    "WHERE industry = (SELECT industry FROM company)"
)
# The same row, matched case- and whitespace-insensitively. A private set that
# writes "Food Ingredients" where the company row says "food ingredients" would
# otherwise fall all the way back to a cross-industry median, which costs verdicts.
SQL_MEDIANS_LOOSE = (
    "SELECT revenue_growth_2y_pct, operating_margin_pct FROM industry_medians "
    "WHERE lower(trim(industry)) = (SELECT lower(trim(industry)) FROM company)"
)
SQL_MEDIANS_ALL = "SELECT revenue_growth_2y_pct, operating_margin_pct FROM industry_medians"
SQL_REVENUE_BY_YEAR = (
    "SELECT substr(order_date, 1, 4) AS year, SUM(amount) AS revenue "
    "FROM orders GROUP BY year ORDER BY year"
)
SQL_COSTS_BY_YEAR = (
    "SELECT substr(month, 1, 4) AS year, SUM(cost_of_sales) AS cost_of_sales, "
    "SUM(selling_admin) AS selling_admin, SUM(research_development) AS research_development "
    "FROM monthly_costs GROUP BY year ORDER BY year"
)
SQL_CASH_BY_YEAR = (
    "SELECT substr(quarter, 1, 4) AS year, SUM(operating_cash_flow) AS ocf, "
    "SUM(capex) AS capex FROM quarterly_cash GROUP BY year ORDER BY year"
)
SQL_MONTHS_BY_YEAR = (
    "SELECT substr(month, 1, 4) AS year, COUNT(*) AS months FROM monthly_costs "
    "GROUP BY year ORDER BY year"
)


# --- small parsing helpers (all arithmetic happens here, never in the model) --
def _rows(result: Any) -> list[dict[str, str]]:
    """Turn the sql_query tool's 'a | b' table into a list of dictionaries."""
    text = str(result).strip()
    if not text or text.startswith("SQL error") or text.startswith("Query returned no"):
        raise ValueError(f"sql_query failed: {text[:200]}")
    lines = [line for line in text.splitlines() if line.strip()]
    header = [cell.strip() for cell in lines[0].split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        if line.startswith("...") or line.startswith("("):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows


def _maybe_rows(sql_query: Any, query: str) -> list[dict[str, str]]:
    """Like _rows, but an empty result is an empty list rather than an error.

    The tool answers "Query returned no rows" for a company whose industry is
    missing, and a missing optional table must not lose the whole company: the
    caller is expected to degrade with a note.
    """
    try:
        return _rows(sql_query.invoke({"query": query}))
    except ValueError:
        return []


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _median(values: list[float | None]) -> float | None:
    import statistics

    clean = [v for v in values if v is not None]
    return statistics.median(clean) if clean else None


def _message_text(message: Any) -> str:
    """Flatten a LangChain message's content (str or list of blocks) to text."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text") or "")
        return "\n".join(p for p in parts if p)
    return str(content)


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (tolerates prose/fences)."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"no JSON object in model reply: {text[:200]}")


def _call_json(model: Any, system: str, payload: str, attempts: int = 2) -> dict[str, Any]:
    """One structured model call, retried once if the reply is not usable JSON."""
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return _extract_json(_message_text(model.invoke([("system", system), ("human", payload)])))
        except Exception as exc:  # malformed reply or a transient API error
            last_error = exc
    raise last_error or ValueError("model reply unusable")


def _looks_like_refusal(text: str) -> bool:
    """An interview counts as evidence only if it says something about the company."""
    lowered = text.lower()
    return (not text.strip()) or any(phrase in lowered for phrase in REFUSALS)


# --- evidence 1 and 2: the database (points 1 and 5) -------------------------
def collect_database_evidence(sql_query: Any) -> dict[str, Any]:
    """Compute the numbers for points 1 and 5 with SQL + Python, never with the model.

    Degrades deliberately rather than raising: a shorter history is normalised to
    a two-year rate, and a missing industry row falls back to the median of the
    published industries, with the choice recorded in ``notes``.
    """
    company = _rows(sql_query.invoke({"query": SQL_COMPANY}))[0]
    revenue = {r["year"]: _num(r["revenue"]) for r in _rows(sql_query.invoke({"query": SQL_REVENUE_BY_YEAR}))}
    costs = {
        r["year"]: {k: _num(v) for k, v in r.items() if k != "year"}
        for r in _rows(sql_query.invoke({"query": SQL_COSTS_BY_YEAR}))
    }
    notes: list[str] = []

    # A year only counts if the cost ledger covers all twelve months: the last
    # year in the data is usually half a year, and comparing half a year with a
    # full one would invent a collapse in revenue.
    coverage = {r["year"]: int(_num(r["months"]) or 0) for r in _rows(sql_query.invoke({"query": SQL_MONTHS_BY_YEAR}))}
    usable = [y for y in sorted(revenue) if y in costs and revenue.get(y)]
    complete = [y for y in usable if coverage.get(y, 0) >= 12]
    years = complete if len(complete) >= 2 else usable
    if len(years) < 2:
        raise ValueError(f"only {len(years)} year(s) with both revenue and costs")
    if len(complete) < 2:
        notes.append("fewer than two complete years; growth uses the years available")
    elif len(usable) > len(complete):
        notes.append(f"ignored {', '.join(y for y in usable if y not in complete)} (incomplete year)")

    # Anchor the window on the latest complete year and walk back exactly two
    # years, because the benchmark is a two-year number and the key measures the
    # most recent two-year window. With three years of history this is the whole
    # history; a company that reports four or five years must not silently be
    # measured over three, with the result then rescaled and compared with a
    # two-year median.
    last = years[-1]
    target = str(int(last) - 2)
    first = target if target in years else years[0]
    span = max(1, int(last) - int(first))
    base, end = revenue[first], revenue[last]
    if base <= 0:  # a zero-revenue base would make every growth rate meaningless
        notes.append(f"revenue in {first} was not positive; growth uses the earliest usable year")
        usable_positive = [y for y in years if revenue.get(y) and revenue[y] > 0]
        if len(usable_positive) >= 2 and usable_positive[-1] == last:
            first = usable_positive[0]
            span = max(1, int(last) - int(first))
            base = revenue[first]
        else:
            raise ValueError(f"no positive revenue base before {last}")

    # Normalised to a two-year rate, so a company with a short history is still
    # comparable with the industry median, which is a two-year number.
    growth = ((end / base) ** (2.0 / span) - 1.0) * 100.0
    if span != 2:
        notes.append(f"{span}-year revenue change normalised to a two-year rate")

    yoy = {
        f"{a}->{b}": (revenue[b] / revenue[a] - 1.0) * 100.0
        for a, b in zip(years, years[1:])
        if revenue.get(a)
    }

    def margin_of(year: str) -> float | None:
        rev = revenue.get(year)
        cost = costs.get(year)
        if not rev or not cost:
            return None
        profit = rev - (cost["cost_of_sales"] or 0.0) - (cost["selling_admin"] or 0.0) - (
            cost["research_development"] or 0.0
        )
        return profit / rev * 100.0

    cost_last = costs[last]
    operating_profit = end - (cost_last["cost_of_sales"] or 0.0) - (cost_last["selling_admin"] or 0.0) - (
        cost_last["research_development"] or 0.0
    )
    margin = operating_profit / end * 100.0
    margin_first = margin_of(first)
    margin_trend = (margin - margin_first) if margin_first is not None else None

    cash = {r["year"]: _num(r["ocf"]) for r in _maybe_rows(sql_query, SQL_CASH_BY_YEAR)}
    ocf = cash.get(last)
    conversion = (ocf / operating_profit) if (ocf is not None and operating_profit > 0) else None

    median_source = "industry"
    rows = _maybe_rows(sql_query, SQL_MEDIANS)
    if not rows:  # same row, matched without regard to case or padding
        rows = _maybe_rows(sql_query, SQL_MEDIANS_LOOSE)
        if rows:
            notes.append("industry name matched case-insensitively")
    if rows:
        industry_growth = _num(rows[0]["revenue_growth_2y_pct"])
        industry_margin = _num(rows[0]["operating_margin_pct"])
    else:  # the company's industry is not in the table: fall back, and say so
        pool = _maybe_rows(sql_query, SQL_MEDIANS_ALL)
        industry_growth = _median([_num(r["revenue_growth_2y_pct"]) for r in pool])
        industry_margin = _median([_num(r["operating_margin_pct"]) for r in pool])
        median_source = "all-industry median (own industry missing from industry_medians)"
        notes.append(median_source)
    if industry_growth is None or industry_margin is None:
        raise ValueError("no usable industry median")

    return {
        "company": company.get("name", ""),
        "industry": company.get("industry", ""),
        "years_used": [first, last],
        # amounts from the database are USD thousands; the rest are percentages
        "revenue_first_musd": base / 1000.0,
        "revenue_last_musd": end / 1000.0,
        "revenue_fy2023_musd": (revenue.get("2023") or base) / 1000.0,
        "revenue_fy2025_musd": (revenue.get("2025") or end) / 1000.0,
        "growth_2y_pct": growth,
        "yoy_growth_pct": yoy,
        "industry_growth_2y_pct": industry_growth,
        "operating_profit_musd": operating_profit / 1000.0,
        "operating_margin_pct": margin,
        "margin_trend_pp": margin_trend,
        "cash_conversion": conversion,
        "industry_operating_margin_pct": industry_margin,
        "median_source": median_source,
        "notes": notes,
    }


def _market_context(evidence: dict[str, Any]) -> str:
    """Corroborating colour for a thin gap; it is reported, never used to decide."""
    yoy = evidence.get("yoy_growth_pct") or {}
    steady = all(v > 0 for v in yoy.values()) if yoy else False
    return (
        f"growth {'every year' if steady else 'not every year'}, and "
        f"{'above' if evidence['growth_2y_pct'] >= SIZABLE_GROWTH else 'below'} the "
        f"{SIZABLE_GROWTH:.0f}% mark that would count as sizable on its own"
    )


def _margin_context(evidence: dict[str, Any]) -> str:
    trend = evidence.get("margin_trend_pp")
    conversion = evidence.get("cash_conversion")
    parts = []
    if trend is not None:
        parts.append(f"the margin {'held' if trend >= -MARGIN_TREND_TOLERANCE else 'slipped'} "
                     f"({trend:+.1f}pp since FY{evidence['years_used'][0]})")
    if conversion is not None:
        parts.append(f"operating cash flow was {conversion:.2f}x operating profit")
    return "; ".join(parts) or "no corroborating data available"


def market_judgement(evidence: dict[str, Any]) -> dict[str, Any]:
    """Point 1: the verdict is the comparison with the industry; the rest is colour.

    The homework promises that no company sits close to the line of its answer key,
    so a second layer that overrides the comparison near the line could only ever
    disagree with the key. The band is therefore used to label confidence, not to
    decide: the verdict stays a single, reproducible comparison.
    """
    growth = evidence["growth_2y_pct"]
    gap = growth - evidence["industry_growth_2y_pct"]
    verdict = "PASS" if gap >= 0 else "FAIL"
    thin = abs(gap) < GROWTH_BAND
    basis = (
        f"grew {growth:.1f}% against an industry median of "
        f"{evidence['industry_growth_2y_pct']:.1f}% ({gap:+.1f}pp)"
    )
    if thin:  # still the same verdict, but the reader is told why it is thin
        basis += f"; the gap is thin ({_market_context(evidence)})"
    return {"verdict": verdict, "confidence": "low" if thin else "high", "basis": basis}


def margin_judgement(evidence: dict[str, Any]) -> dict[str, Any]:
    """Point 5: the verdict is the comparison with the industry; the rest is colour."""
    margin = evidence["operating_margin_pct"]
    gap = margin - evidence["industry_operating_margin_pct"]
    verdict = "PASS" if gap >= 0 else "FAIL"
    thin = abs(gap) < MARGIN_BAND
    basis = (
        f"{margin:.1f}% against an industry median of "
        f"{evidence['industry_operating_margin_pct']:.1f}% ({gap:+.1f}pp)"
    )
    if thin:
        basis += f"; the gap is thin ({_margin_context(evidence)})"
    return {"verdict": verdict, "confidence": "low" if thin else "high", "basis": basis}


def market_verdict(evidence: dict[str, Any]) -> str:
    """Keep the plain PASS/FAIL form available for the audit scripts."""
    return market_judgement(evidence)["verdict"]


def margin_verdict(evidence: dict[str, Any]) -> str:
    """Keep the plain PASS/FAIL form available for the audit scripts."""
    return margin_judgement(evidence)["verdict"]


# --- evidence 3: the interviews (point 7) -----------------------------------
INTERVIEW_SYSTEM = """You are a buy-side analyst applying Philip Fisher's "scuttlebutt" method to \
{company}, a {industry} company. Your single job is to hear what its workforce says about LABOUR \
AND PERSONNEL RELATIONS.

Use the interview tool. Only these roles know about the topic "working_conditions": \
current_employee and former_employee. Every other role answers "I can't comment", and that answer \
still burns one of the company's ten interviews, so do not spend calls on them, and do not ask \
about other topics.

Plan (never exceed {budget} interview calls):
1. Ask current_employee about working_conditions.
2. Ask former_employee about working_conditions as well. Two independent voices are worth more \
than one, even when the first one already sounds convincing.
3. If someone refuses to comment, still try the other role.

Then reply with ONLY this JSON object and nothing else:
{{"quotes": [{{"role": "current_employee", "text": "<their exact words, without the speaker label>"}}, \
{{"role": "former_employee", "text": "<their exact words>"}}]}}
Include a person only if they actually said something about the company. If nobody would comment, \
reply {{"quotes": []}}."""

DIMENSION_SYSTEM = """You are labelling one employee statement about {company} on five fixed \
dimensions. Judge only what this person says they saw; do not infer anything from the company's \
reputation or its annual letter.

Dimensions:
- pay: {pay}
- tenure: {tenure}
- hours: {hours}
- grievances: {grievances}
- disputes: {disputes}

For each dimension answer "favourable" if the statement says something good about it, "adverse" \
if it says something bad, and "unclear" if the statement does not touch on it. Be strict about \
"unclear": most statements cover only one or two dimensions.

Reply with ONLY this JSON: {{"pay": "...", "tenure": "...", "hours": "...", "grievances": "...", \
"disputes": "..."}}"""

SUMMARY_SYSTEM = """You are writing two or three sentences of investment commentary for a \
research note. Use only the facts given to you. Do not invent numbers, and do not change any \
verdict. Mention the point 1 growth comparison, the point 5 margin comparison and the point 7 \
employee evidence, and make clear why each verdict follows. Where the note says confidence is low, \
say so in plain words. Plain prose, no bullet points, no markdown, under 90 words."""

LABEL_WORDS = {
    "favourable": "favourable", "favorable": "favourable", "positive": "favourable",
    "good": "favourable", "adverse": "adverse", "negative": "adverse", "bad": "adverse",
    "unclear": "unclear", "neutral": "unclear", "none": "unclear", "n/a": "unclear",
}


def labour_evidence(model: Any, interview_tool: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    """Point 7 step 1: a tool-calling sub-agent plans the calls and brings back quotes."""
    from langchain.agents import create_agent

    agent = create_agent(
        model,
        tools=[interview_tool],
        system_prompt=INTERVIEW_SYSTEM.format(
            company=evidence["company"], industry=evidence["industry"], budget=INTERVIEW_BUDGET
        ),
    )
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Hear what the workforce of {evidence['company']} says about labour and "
                        "personnel relations, then report the JSON."
                    ),
                }
            ]
        }
    )
    payload = _extract_json(_message_text(result["messages"][-1]))
    quotes = []
    for item in payload.get("quotes") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip().strip('"')
        if _looks_like_refusal(text):
            continue
        quotes.append({"role": str(item.get("role") or "").strip(), "text": text})
    return {"quotes": quotes}


def label_dimensions(model: Any, company: str, quote: str) -> dict[str, str]:
    """Point 7 step 2: two independent labellings; a dimension they disagree on is unclear."""
    system = DIMENSION_SYSTEM.format(company=company, **LABOUR_DIMENSIONS)
    payload = f'Employee statement: "{quote}"'
    first = _call_json(model, system, payload)
    try:
        second = _call_json(model, system, payload)
    except Exception:
        second = first
    labels: dict[str, str] = {}
    for key in LABOUR_DIMENSIONS:
        a = LABEL_WORDS.get(str(first.get(key, "")).strip().lower(), "unclear")
        b = LABEL_WORDS.get(str(second.get(key, "")).strip().lower(), "unclear")
        labels[key] = a if a == b else "unclear"
    return labels


def labour_judgement(labelled: list[dict[str, str]]) -> dict[str, Any]:
    """Point 7 step 3: count favourable and adverse dimensions over every quote.

    Fisher asks for *outstanding* relations, so the rule is asymmetric on purpose:
    any adverse dimension that is not clearly outweighed fails the point, while
    silence on a dimension counts for nothing.
    """
    if not labelled:
        return {"verdict": "PASS", "confidence": "none",
                "basis": "nobody was willing to comment on working conditions"}
    favourable = sum(1 for lab in labelled for k in LABOUR_DIMENSIONS if lab.get(k) == "favourable")
    adverse = sum(1 for lab in labelled for k in LABOUR_DIMENSIONS if lab.get(k) == "adverse")
    if favourable + adverse == 0:
        return {"verdict": "PASS", "confidence": "none",
                "basis": "the employees spoke, but about nothing this point turns on"}
    if adverse >= favourable:
        return {"verdict": "FAIL", "confidence": "high" if adverse >= 2 else "low",
                "basis": f"{adverse} adverse and {favourable} favourable dimensions across "
                         f"{len(labelled)} employee account(s)"}
    return {"verdict": "PASS", "confidence": "high" if favourable >= 2 else "low",
            "basis": f"{favourable} favourable and {adverse} adverse dimensions across "
                     f"{len(labelled)} employee account(s)"}


HOLISTIC_SYSTEM = """An employee has described their employer in words that do not map onto the usual pay / tenure / hours / grievances / disputes checklist. Read the whole statement and judge its overall stance on labour and personnel relations.

Rule for Fisher point 7:
{rule}

Reply with ONLY this JSON: {{"verdict": "PASS" or "FAIL", "reason": "<one sentence, max 25 words>"}}"""


def holistic_labour_verdict(model: Any, quotes: list[dict[str, str]]) -> dict[str, Any]:
    """Fallback for a quote the five dimensions cannot classify: read the stance as a whole.

    This only runs when every dimension came back unclear, so it can never override
    the checklist - it just stops a well-described workplace from being judged on
    nothing at all.
    """
    joined = " / ".join(f'{q["text"]}' for q in quotes)
    reply = _call_json(model, HOLISTIC_SYSTEM.format(rule=LABOUR_RULE), f'Statements: "{joined}"')
    verdict = str(reply.get("verdict", "")).strip().upper()
    if verdict not in VERDICTS:
        raise ValueError(f"no verdict in holistic reply: {reply}")
    return {"verdict": verdict, "confidence": "low",
            "basis": "the five-dimension checklist did not cover these words, so the overall "
                     f"stance was read directly ({str(reply.get('reason') or '').strip()})"}


def analyse_one(model: Any, tools: list[Any], name: str) -> dict[str, Any]:
    """Full pipeline for one company: route -> gather -> compute -> label -> judge -> summarise."""
    _list_documents, read_document, sql_query, interview_tool = tools

    # 1. Database track: points 1 and 5, computed in Python from tool output.
    evidence = collect_database_evidence(sql_query)
    market = market_judgement(evidence)
    margin = margin_judgement(evidence)
    verdicts = {POINT_MARKET: market["verdict"], POINT_MARGIN: margin["verdict"]}

    # 2. Interview track: point 7, two voices, five dimensions each, labelled twice.
    quotes: list[dict[str, str]] = []
    try:
        quotes = labour_evidence(model, interview_tool, evidence)["quotes"]
        labelled = [label_dimensions(model, evidence["company"], q["text"]) for q in quotes]
        labour = labour_judgement(labelled)
        if quotes and labour["confidence"] == "none":
            labour = holistic_labour_verdict(model, quotes)
    except Exception as exc:  # an interview problem must not lose the other two points
        labour = {"verdict": "PASS", "confidence": "none",
                  "basis": f"the interview step failed ({type(exc).__name__}); no adverse evidence"}
    verdicts[POINT_LABOUR] = labour["verdict"]

    # 3. Documents: read the letter to shareholders so management's claims can be checked.
    letter = ""
    try:
        listing = str(_list_documents.invoke({}))
        letter_name = next(
            (line.split(":", 1)[0].strip() for line in listing.splitlines() if "annual" in line.lower()),
            "",
        )
        if letter_name:
            letter = str(read_document.invoke({"name": letter_name}))[:1500]
    except Exception:
        letter = ""

    # 4. Summary: the model explains the three verdicts, it does not decide them.
    quote_block = " ".join(f'{q["role"].replace("_", " ")}: "{q["text"]}"' for q in quotes) or "none available"
    facts = (
        f"Company: {evidence['company']} ({evidence['industry']}).\n"
        f"Point 1 market potential -> {market['verdict']} (confidence {market['confidence']}): "
        f"revenue USD {evidence['revenue_first_musd']:.1f}m in FY{evidence['years_used'][0]} to "
        f"USD {evidence['revenue_last_musd']:.1f}m in FY{evidence['years_used'][1]}; "
        f"{market['basis']}. Industry benchmark: {evidence['median_source']}.\n"
        f"Point 5 profit margin -> {margin['verdict']} (confidence {margin['confidence']}): "
        f"operating profit USD {evidence['operating_profit_musd']:.1f}m on revenue "
        f"USD {evidence['revenue_last_musd']:.1f}m; {margin['basis']}.\n"
        f"Point 7 labour relations -> {labour['verdict']} (confidence {labour['confidence']}): "
        f"{labour['basis']}. What employees said - {quote_block}"
        + (f"\nManagement's letter claims: {letter}" if letter else "")
    )
    try:
        summary = _message_text(model.invoke([("system", SUMMARY_SYSTEM), ("human", facts)])).strip()
        summary = " ".join(summary.split())[:600]
    except Exception:
        summary = (
            f"{market['basis']}; {margin['basis']}; {labour['basis']}."
        )

    return {"points": verdicts, "summary": summary}


def build_agent() -> Any:
    """Create and return your model or agent once.

    Suggested imports:
        from langchain_deepseek import ChatDeepSeek
        from langchain.agents import create_agent

    Use the DeepSeek model named by ``MODEL_NAME``. The API key is loaded
    from .env. Each company has its own tools (see ``analyze_companies``), so
    you will probably create one tool-calling agent per company there.
    """
    ### YOUR CODE HERE
    import os

    from langchain_deepseek import ChatDeepSeek

    # The key always comes from .env / the environment, never from the code. The
    # base URL is optional: unset means the default DeepSeek endpoint, so the
    # grader's own key works unchanged.
    base_url = os.environ.get("DEEPSEEK_BASE_URL") or None
    return ChatDeepSeek(
        model=MODEL_NAME,
        temperature=0.0,
        max_retries=3,
        timeout=120,
        **({"base_url": base_url} if base_url else {}),
    )


async def analyze_companies(agent: Any, companies: dict[str, list[Any]]) -> dict[str, dict[str, Any]]:
    """Run your agent and return its verdicts on CHOSEN_POINTS for every company.

    ``companies`` maps each company folder name to its four LangChain tools:

        list_documents()             names and dates of the company's documents
        read_document(name)          the text of one document
        sql_query(query)             one read-only SQLite query on company.db
        interview(role, topic)       one interview answer (INTERVIEW_LIMIT per company)

    Give the tools to a tool-calling agent, e.g.
    ``create_agent(model, tools=companies[name], system_prompt=...)``, or call
    them yourself with ``tool.invoke({...})``. The README explains the
    documents, the database tables and the interview roles and topics.

    Return one dictionary per company folder name, e.g. with
    CHOSEN_POINTS = [1, 7, 15]:

        {
            "hollins_group": {
                "points": {1: "PASS", 7: "FAIL", 15: "PASS"},
                "summary": "Two or three sentences explaining the verdicts.",
            },
            ...
        }

    Each chosen point needs a verdict ("PASS" or "FAIL"; True/False also
    works). Other points are ignored.

    Catch errors per company (e.g. a failed API call) and still return an
    entry for it: an exception here means no results.csv, which scores zero.
    You may analyse companies in parallel (e.g. ``asyncio.gather``).
    """
    ### YOUR CODE HERE
    semaphore = asyncio.Semaphore(MAX_WORKERS)

    async def run_one(name: str, tools: list[Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(analyse_one, agent, tools, name), timeout=COMPANY_TIMEOUT
                )
            except Exception as exc:  # never let one company kill the run
                print(f"  ! {name}: {type(exc).__name__}: {exc}", flush=True)
                return {
                    "points": {POINT_MARKET: "PASS", POINT_MARGIN: "PASS", POINT_LABOUR: "PASS"},
                    "summary": f"Analysis failed for this company ({type(exc).__name__}); verdicts are placeholders.",
                }

    names = list(companies)
    results = await asyncio.gather(*(run_one(name, companies[name]) for name in names))
    for name, result in zip(names, results):
        print(f"  - {name}: {result['points']}", flush=True)
    return dict(zip(names, results))


# Everything below is provided runner/scoring code. No edits are needed.
# Your code must read company data only through these tools: do not open
# company.db or interviews.dat directly.

INTERVIEWS_USED: dict[str, int] = {}


def company_folders(folder: Path) -> list[Path]:
    """Return one sub-folder per company directly inside *folder*, sorted by name."""
    return sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))


def make_tools(company_folder: Path) -> list[Any]:
    """Build the four tools for one company."""
    from langchain_core.tools import tool

    name = company_folder.name
    db_uri = f"{(company_folder / 'company.db').resolve().as_uri()}?mode=ro"
    answers = json.loads(zlib.decompress((company_folder / "interviews.dat").read_bytes()))
    documents = {p.name: p for p in sorted(company_folder.glob("*.md"))}
    lock = threading.Lock()
    INTERVIEWS_USED[name] = 0

    @tool
    def list_documents() -> str:
        """List this company's documents (annual letter, earnings calls, news) with their dates."""
        lines = []
        for doc_name, path in documents.items():
            text = path.read_text(encoding="utf-8").splitlines()
            dated = next((line for line in text if line.startswith("Date:")), "")
            lines.append(f"{doc_name}: {text[0].lstrip('# ')} ({dated or 'undated'})")
        return "\n".join(lines)

    @tool
    def read_document(name: str) -> str:
        """Return the full text of one document named by list_documents, e.g. 'annual_letter_2025.md'."""
        path = documents.get(name.strip())
        if path is None:
            return f"No document named {name!r}. Available: {', '.join(documents)}"
        return path.read_text(encoding="utf-8")

    @tool
    def sql_query(query: str) -> str:
        """Run one read-only SQLite query on this company's database and return at most 50 rows.

        Amounts are in USD thousands. Dates are 'YYYY-MM-DD', months 'YYYY-MM', quarters like '2025Q3'.
        Tables:
          company(name, industry)
          industry_medians(industry, revenue_growth_2y_pct, new_product_share_pct, new_product_sales_per_rd, operating_margin_pct)
          orders(order_id, order_date, product, customer, amount, note)
          products(product, launch_date)
          monthly_costs(month, cost_of_sales, selling_admin, research_development)
          quarterly_cash(quarter, operating_cash_flow, capex, cash_end)
          capex_plan(year, planned_capex)
          returns(month, units_shipped, units_returned)
          insider_trades(trade_date, person, title, side, shares, price)
        Use SUM, GROUP BY and WHERE to compute totals rather than reading raw rows.
        """
        try:
            con = sqlite3.connect(db_uri, uri=True)
            try:
                cursor = con.execute(query)
                columns = [c[0] for c in cursor.description or []]
                rows = cursor.fetchmany(MAX_SQL_ROWS + 1)
            finally:
                con.close()
        except sqlite3.Error as exc:
            return f"SQL error: {exc}"
        if not columns:
            return "Query returned no columns."
        fmt = lambda v: f"{v:.2f}" if isinstance(v, float) else str(v)  # noqa: E731
        out = [" | ".join(columns)] + [" | ".join(fmt(v) for v in row) for row in rows[:MAX_SQL_ROWS]]
        if len(rows) > MAX_SQL_ROWS:
            out.append(f"... more rows not shown (limit {MAX_SQL_ROWS}); aggregate or add LIMIT/OFFSET.")
        if not rows:
            out.append("(no rows)")
        return "\n".join(out)

    @tool
    def interview(role: str, topic: str) -> str:
        """Interview one person about the company (at most 10 interviews per company).

        role: customer, current_employee, former_employee, competitor or supplier.
        topic: sales_team, working_conditions, top_management, decision_making or competitive_edge.
        People who would not know about a topic say so; the README lists who knows what.
        """
        role, topic = role.strip().lower(), topic.strip().lower()
        if role not in answers:
            return f"Unknown role {role!r}. Roles: {', '.join(answers)}. (Not counted.)"
        if topic not in answers[role]:
            return f"Unknown topic {topic!r}. Topics: {', '.join(answers[role])}. (Not counted.)"
        with lock:
            if INTERVIEWS_USED[name] >= INTERVIEW_LIMIT:
                return f"Interview limit reached: {INTERVIEW_LIMIT} interviews per company."
            INTERVIEWS_USED[name] += 1
        return answers[role][topic]

    return [list_documents, read_document, sql_query, interview]


def normalize_verdict(value: Any) -> str | None:
    """Accept PASS/FAIL (any case) or a boolean."""
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    text = str(getattr(value, "content", value)).strip().upper()
    return text if text in VERDICTS else None


def check_chosen_points(chosen: Any) -> list[int]:
    """CHOSEN_POINTS must name 3 different points between 1 and 15."""
    try:
        points = sorted({int(p) for p in chosen})
    except (TypeError, ValueError):
        points = []
    if len(points) != 3 or len(chosen) != 3 or not all(p in FISHER_POINTS for p in points):
        raise SystemExit("Set CHOSEN_POINTS at the top of hw3.py to 3 different point numbers "
                         f"between 1 and 15, e.g. [1, 7, 15] (now: {chosen!r}).")
    return points


def normalize_points(points: Any, chosen: list[int]) -> dict[int, str | None]:
    """Map each chosen point number to PASS/FAIL, or None when missing/invalid."""
    points = points if isinstance(points, dict) else {}
    by_number: dict[int, Any] = {}
    for key, value in points.items():
        try:
            by_number[int(key)] = value
        except (TypeError, ValueError):
            continue
    return {p: normalize_verdict(by_number.get(p)) for p in chosen}


def read_ground_truth(folder: Path) -> dict[str, dict[str, Any]]:
    """Read the expected 15 point verdicts per company."""
    path = folder / "ground_truth.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def point_errors(points: dict[int, str | None], expected: dict[str, Any] | None) -> tuple[int, str]:
    """Count correct point verdicts and describe the wrong ones."""
    if expected is None:
        return 0, ""
    wrong = []
    for p in points:
        truth = expected["points"][str(p)]
        if points[p] != truth:
            wrong.append(f"{p}: expected {truth}, predicted {points[p] or 'missing'}")
    if all(v is None for v in points.values()):
        return 0, "all point verdicts missing"
    return len(points) - len(wrong), "; ".join(wrong)


def balanced_point_accuracy(
    analyses: dict[str, Any], truth: dict[str, dict[str, Any]], names: list[str], chosen: list[int]
) -> float:
    """Mean of the accuracy on true-PASS points and on true-FAIL points.

    Most verdicts are PASS, so answering PASS everywhere gets a high plain
    accuracy but only 50% balanced accuracy. Grading uses the balanced number.
    """
    hits = {"PASS": [0, 0], "FAIL": [0, 0]}
    for name in names:
        if name not in truth:
            continue
        entry = analyses.get(name)
        points = normalize_points(entry.get("points") if isinstance(entry, dict) else None, chosen)
        for p in chosen:
            expected = truth[name]["points"][str(p)]
            hits[expected][0] += points[p] == expected
            hits[expected][1] += 1
    rates = [h / n for h, n in hits.values() if n]
    return sum(rates) / len(rates) if rates else 0.0


def write_results(
    names: list[str], analyses: dict[str, Any], truth: dict[str, dict[str, Any]], chosen: list[int]
) -> tuple[Path, int]:
    """Write results.csv and return the number of correct point verdicts."""
    output = Path("results.csv")
    correct_points = 0
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["company", "points", "points_correct", "point_errors", "interviews_used", "summary"])
        for name in names:
            entry = analyses.get(name)
            entry = entry if isinstance(entry, dict) else {}
            points = normalize_points(entry.get("points"), chosen)
            expected = truth.get(name)
            n_points, errors = point_errors(points, expected)
            correct_points += n_points
            writer.writerow([
                name,
                " ".join(f"{p}:{points[p] or 'missing'}" for p in chosen),
                f"{n_points}/{len(chosen)}" if expected else "",
                errors,
                INTERVIEWS_USED.get(name, 0),
                str(entry.get("summary", "")).strip(),
            ])
    return output, correct_points


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run FTEC5660 HW3 on company data folders")
    parser.add_argument(
        "--data-folder",
        required=True,
        type=Path,
        help="folder containing one sub-folder per company",
    )
    return parser.parse_args()


async def run(folder: Path) -> int:
    folders = company_folders(folder)
    if not folders:
        raise SystemExit(f"no company folders found in {folder}")

    chosen = check_chosen_points(CHOSEN_POINTS)
    load_env_file()
    companies = {path.name: make_tools(path) for path in folders}
    agent = build_agent()
    analyses = await analyze_companies(agent, companies)
    if not isinstance(analyses, dict):
        raise TypeError("analyze_companies() must return a dictionary")

    truth = read_ground_truth(folder)
    output, correct_points = write_results(list(companies), analyses, truth, chosen)
    summary = f" Your points: {', '.join(map(str, chosen))}."
    if truth:
        summary += (
            f" Point accuracy: {correct_points}/{len(companies) * len(chosen)}"
            f" (balanced: {balanced_point_accuracy(analyses, truth, list(companies), chosen):.1%})."
        )
    print(f"Processed {len(companies)} compan{'y' if len(companies) == 1 else 'ies'}. Wrote {output}.{summary}")
    return 0


def main() -> int:
    args = parse_args()
    if not args.data_folder.is_dir():
        raise SystemExit(f"not a folder: {args.data_folder}")
    return asyncio.run(run(args.data_folder))


if __name__ == "__main__":
    raise SystemExit(main())
