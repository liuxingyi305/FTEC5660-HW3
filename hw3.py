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

# --- database queries used for points 1 and 5 -------------------------------
SQL_COMPANY = "SELECT name, industry FROM company"
SQL_MEDIANS = (
    "SELECT industry, revenue_growth_2y_pct, operating_margin_pct FROM industry_medians "
    "WHERE industry = (SELECT industry FROM company)"
)
SQL_REVENUE_BY_YEAR = (
    "SELECT substr(order_date, 1, 4) AS year, SUM(amount) AS revenue "
    "FROM orders GROUP BY year ORDER BY year"
)
SQL_COSTS_BY_YEAR = (
    "SELECT substr(month, 1, 4) AS year, SUM(cost_of_sales) AS cost_of_sales, "
    "SUM(selling_admin) AS selling_admin, SUM(research_development) AS research_development "
    "FROM monthly_costs GROUP BY year ORDER BY year"
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


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


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


# --- evidence 1 and 2: the database (points 1 and 5) -------------------------
def collect_database_evidence(sql_query: Any) -> dict[str, Any]:
    """Compute the numbers for points 1 and 5 with SQL + Python, never with the model."""
    company = _rows(sql_query.invoke({"query": SQL_COMPANY}))[0]
    median = _rows(sql_query.invoke({"query": SQL_MEDIANS}))[0]
    revenue = {row["year"]: _num(row["revenue"]) for row in _rows(sql_query.invoke({"query": SQL_REVENUE_BY_YEAR}))}
    costs = {
        row["year"]: {k: _num(v) for k, v in row.items() if k != "year"}
        for row in _rows(sql_query.invoke({"query": SQL_COSTS_BY_YEAR}))
    }

    fy2023, fy2025 = revenue.get("2023"), revenue.get("2025")
    if not fy2023 or not fy2025 or "2025" not in costs:
        raise ValueError("missing FY2023/FY2025 revenue or FY2025 costs")

    cost_2025 = costs["2025"]
    operating_profit = fy2025 - (cost_2025["cost_of_sales"] or 0.0) - (cost_2025["selling_admin"] or 0.0) - (
        cost_2025["research_development"] or 0.0
    )
    growth = (fy2025 / fy2023 - 1.0) * 100.0
    margin = operating_profit / fy2025 * 100.0

    return {
        "company": company.get("name", ""),
        "industry": company.get("industry", ""),
        # amounts from the database are USD thousands
        "revenue_fy2023_musd": fy2023 / 1000.0,
        "revenue_fy2025_musd": fy2025 / 1000.0,
        "growth_2y_pct": growth,
        "industry_growth_2y_pct": _num(median["revenue_growth_2y_pct"]),
        "operating_profit_fy2025_musd": operating_profit / 1000.0,
        "operating_margin_pct": margin,
        "industry_operating_margin_pct": _num(median["operating_margin_pct"]),
        "cost_of_sales_fy2025_musd": (cost_2025["cost_of_sales"] or 0.0) / 1000.0,
        "selling_admin_fy2025_musd": (cost_2025["selling_admin"] or 0.0) / 1000.0,
        "rnd_fy2025_musd": (cost_2025["research_development"] or 0.0) / 1000.0,
    }


def market_verdict(evidence: dict[str, Any]) -> str:
    """Point 1: growth vs the industry median growth."""
    return "PASS" if evidence["growth_2y_pct"] >= evidence["industry_growth_2y_pct"] else "FAIL"


def margin_verdict(evidence: dict[str, Any]) -> str:
    """Point 5: operating margin vs the industry median operating margin."""
    return "PASS" if evidence["operating_margin_pct"] >= evidence["industry_operating_margin_pct"] else "FAIL"


# --- evidence 3: the interviews (point 7) -----------------------------------
INTERVIEW_SYSTEM = """You are a buy-side analyst applying Philip Fisher's "scuttlebutt" method to \
{company}, a {industry} company. Your single job is to find out how its LABOUR AND PERSONNEL \
RELATIONS really are.

Use the interview tool. Only these roles know about the topic "working_conditions": \
current_employee and former_employee. Every other role answers "I can't comment", and that answer \
still burns one of the company's ten interviews, so do not waste calls on them. Also do not ask \
about other topics: they are not what you are here for.

Plan (never exceed {budget} interview calls):
1. Ask current_employee about working_conditions.
2. If that person cannot comment, ask former_employee about working_conditions.
3. Stop as soon as you get a substantive answer: the people who know always agree, so one good \
answer is enough.

Then reply with ONLY this JSON object and nothing else:
{{"quote": "<the person's exact words, without the speaker label>", "role": "<current_employee or former_employee>", "found": true}}
If nobody would comment, reply {{"quote": "", "role": "", "found": false}}."""

CLASSIFY_SYSTEM = """You are grading one piece of evidence against a fixed investment rule.

Rule for Fisher point 7, labour and personnel relations:
{rule}

Read the employee statement and decide PASS or FAIL for the company on that rule. Judge only what \
the employee actually saw - not what the company says in its annual letter.

Reply with ONLY this JSON: {{"verdict": "PASS" or "FAIL", "reason": "<one sentence, max 30 words>"}}"""

REFLECT_SYSTEM = """You are a second, independent analyst double-checking a colleague's verdict. \
Given the employee statement and the rule, decide the verdict yourself.

Rule for Fisher point 7, labour and personnel relations:
{rule}

Do not simply agree with your colleague: apply the rule to the evidence from scratch. A statement \
about pay, tenure, grievances being fixed, or people staying is a PASS; constant overtime, people \
quitting, a strike or strike threat, or low morale is a FAIL.

Reply with ONLY this JSON: {{"verdict": "PASS" or "FAIL", "reason": "<one sentence, max 30 words>"}}"""

SUMMARY_SYSTEM = """You are writing two or three sentences of investment commentary for a \
research note. Use only the facts given to you. Do not invent numbers, and do not change any \
verdict. Mention the point 1 growth comparison, the point 5 margin comparison and the point 7 \
employee evidence, and make clear why each verdict follows. Plain prose, no bullet points, no \
markdown, under 90 words."""


def labour_evidence(model: Any, interview_tool: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    """Point 7: a tool-calling sub-agent spends a few interviews, then we judge the quote."""
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
                        f"Find out about labour and personnel relations at {evidence['company']}. "
                        "Interview the people who know and report the JSON."
                    ),
                }
            ]
        }
    )
    reply = _message_text(result["messages"][-1])
    found = _extract_json(reply)
    quote = str(found.get("quote") or "").strip()
    return {"quote": quote, "role": str(found.get("role") or ""), "found": bool(found.get("found")) and bool(quote)}


def _judge_once(model: Any, system: str, payload: str, attempts: int = 2) -> tuple[str, str]:
    """One model judgement, retried once if the reply is not usable JSON."""
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            reply = _extract_json(_message_text(model.invoke([("system", system), ("human", payload)])))
        except Exception as exc:  # malformed reply or a transient API error
            last_error = exc
            continue
        verdict = str(reply.get("verdict", "")).strip().upper()
        if verdict in VERDICTS:
            return verdict, str(reply.get("reason") or "").strip()
        last_error = ValueError(f"model gave no verdict: {reply}")
    raise last_error or ValueError("model gave no verdict")


def judge_labour(model: Any, quote: str) -> tuple[str, str]:
    """Two independent model calls on the same quote; the reflection pass wins on conflict."""
    payload = f'Employee statement: "{quote}"'
    first_verdict, first_reason = _judge_once(model, CLASSIFY_SYSTEM.format(rule=LABOUR_RULE), payload)
    try:
        second_verdict, second_reason = _judge_once(model, REFLECT_SYSTEM.format(rule=LABOUR_RULE), payload)
    except Exception:
        # the reflection pass is a bonus check: never let it lose the first verdict
        return first_verdict, first_reason
    return second_verdict, second_reason


def analyse_one(model: Any, tools: list[Any], name: str) -> dict[str, Any]:
    """Full pipeline for one company: route -> gather -> compute -> judge -> reflect -> summarise."""
    _list_documents, read_document, sql_query, interview_tool = tools

    # 1. Database track: points 1 and 5, computed in Python from tool output.
    evidence = collect_database_evidence(sql_query)
    verdicts = {POINT_MARKET: market_verdict(evidence), POINT_MARGIN: margin_verdict(evidence)}

    # 2. Interview track: point 7 via the scuttlebutt sub-agent, then two model judgements.
    try:
        labour = labour_evidence(model, interview_tool, evidence)
        if labour["found"]:
            verdicts[POINT_LABOUR], labour_reason = judge_labour(model, labour["quote"])
        else:
            verdicts[POINT_LABOUR] = "PASS"  # nobody complained: nothing against the company
            labour_reason = "No employee was willing to comment on working conditions."
    except Exception as exc:  # an interview problem must not lose the other two points
        labour = {"quote": "", "role": "", "found": False}
        verdicts[POINT_LABOUR] = "PASS"
        labour_reason = f"Interview step failed ({exc}); no adverse evidence found."

    # 3. Documents: read the letter to shareholders so management's claims can be checked.
    #    The file name is discovered from list_documents, not hard-coded.
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
    facts = (
        f"Company: {evidence['company']} ({evidence['industry']}).\n"
        f"Point 1 market potential -> {verdicts[POINT_MARKET]}. Revenue FY2023 "
        f"USD {evidence['revenue_fy2023_musd']:.1f}m, FY2025 USD {evidence['revenue_fy2025_musd']:.1f}m, "
        f"growth {evidence['growth_2y_pct']:.1f}% vs industry median {evidence['industry_growth_2y_pct']:.1f}%.\n"
        f"Point 5 profit margin -> {verdicts[POINT_MARGIN]}. Operating profit FY2025 "
        f"USD {evidence['operating_profit_fy2025_musd']:.1f}m on revenue USD {evidence['revenue_fy2025_musd']:.1f}m, "
        f"margin {evidence['operating_margin_pct']:.1f}% vs industry median "
        f"{evidence['industry_operating_margin_pct']:.1f}%.\n"
        f"Point 7 labour relations -> {verdicts[POINT_LABOUR]}. "
        + (f'A {labour["role"].replace("_", " ")} said: "{labour["quote"]}" ({labour_reason})'
           if labour["found"] else labour_reason)
        + (f"\nManagement's letter claims: {letter}" if letter else "")
    )
    try:
        summary = _message_text(model.invoke([("system", SUMMARY_SYSTEM), ("human", facts)])).strip()
        summary = " ".join(summary.split())[:600]
    except Exception:
        summary = (
            f"Growth of {evidence['growth_2y_pct']:.1f}% versus an industry median of "
            f"{evidence['industry_growth_2y_pct']:.1f}%, an operating margin of "
            f"{evidence['operating_margin_pct']:.1f}% versus {evidence['industry_operating_margin_pct']:.1f}%, "
            f"and an employee account of working conditions."
        )

    return {"points": verdicts, "summary": summary}


def build_agent() -> Any:
    """Create the DeepSeek chat model once; the tools are per company."""
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
