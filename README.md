# FTEC5660 Homework 3: Fisher's 15-Point Investment Agent

Build a LangChain agent that researches a company with tools and applies the
framework from Philip Fisher's *Common Stocks and Uncommon Profits* (Part One,
Chapter 3 "What to buy: the fifteen points to look for in a common stock", and
Chapter 4 "What to buy: applying this to your own needs"). You choose 3 of
Fisher's 15 points and decide yourself what PASS means for each. For every
company your agent gives a PASS/FAIL verdict on your 3 points and a short
summary.

The companies are fictional, so the model cannot answer from memory. Unlike a
pile of reports, the evidence is spread over three sources, and your agent has
to go and get it with tools, the way Fisher's "scuttlebutt" method sends an
investor to talk to customers, competitors, suppliers and staff.

## Data and tools

`public_test/` has one folder per company. Your code receives four tools per
company (built by the provided code in `hw3.py`):

| Tool | What it gives you |
| --- | --- |
| `list_documents()` | the company's six documents and their dates |
| `read_document(name)` | one document: the 2025 letter to shareholders, four earnings calls (Q3 2025 to Q2 2026) and a news file (July 2025 to July 2026) |
| `sql_query(query)` | one read-only SQLite query on the company's database; at most 50 rows come back, so use `SUM`, `GROUP BY` and `WHERE` |
| `interview(role, topic)` | what one person says about the company; at most **10 interviews per company** |

The documents are what management says. Management's own claims (such as "we
have never cut research" or "our people are our greatest strength") are not
evidence on their own: check them against the database and the interviews.

**Database tables** (amounts in USD thousands; dates `YYYY-MM-DD`, months
`YYYY-MM`, quarters like `2025Q3`; the fiscal year is the calendar year):

| Table | Columns |
| --- | --- |
| `company` | name, industry |
| `industry_medians` | industry, revenue_growth_2y_pct, new_product_share_pct, new_product_sales_per_rd, operating_margin_pct (one row per industry; use the row for the company's own industry) |
| `orders` | order_id, order_date, product, customer, amount, note (about 3,000 to 4,000 orders from January 2023 to June 2026; revenue is the sum of order amounts) |
| `products` | product, launch_date |
| `monthly_costs` | month, cost_of_sales, selling_admin, research_development |
| `quarterly_cash` | quarter, operating_cash_flow, capex, cash_end |
| `capex_plan` | year, planned_capex (2026 to 2028) |
| `returns` | month, units_shipped, units_returned |
| `insider_trades` | trade_date, person, title, side (`buy` or `sell`), shares, price |

**Terms used in the data.**

| Term | Meaning |
| --- | --- |
| FY2025 | Fiscal year 2025. Here the fiscal year is the calendar year, so FY2025 is January to December 2025. |
| Quarter | Three months of the year: Q1 is January to March, Q2 April to June, Q3 July to September and Q4 October to December. The database writes `2025Q3`; the documents write "third quarter 2025". |
| Amounts | The database is in **USD thousands** (676,100 means US$676.1 million). The documents use USD millions. |
| Revenue | Money from sales: the sum of `amount` in `orders`. |
| Cost of sales | The cost of making the products that were sold. |
| Selling and admin | The cost of selling and of running the company (sales staff, offices, management). |
| R&D | Research and development spending, mainly on new products. |
| Operating margin | Operating profit as a share of revenue: (revenue − cost of sales − selling and admin − R&D) ÷ revenue. |
| Operating cash flow | Cash that the day-to-day business brings in during a quarter. |
| Cash at the end of a quarter | The cash the company holds on the last day of that quarter (`cash_end`). |
| Capex | Capital expenditure: spending on factories, machines and other long-term assets. `capex_plan` holds the planned capex for 2026 to 2028. |
| Return rate | Units returned ÷ units shipped, from the `returns` table. |
| Industry median | The middle value for companies in the same industry, from `industry_medians`. |
| Insider | A director or senior manager of the company. `insider_trades` lists the shares they bought (`buy`) or sold (`sell`). |
| Earnings call | A meeting each quarter where management presents the quarter's results to investors. Each call quotes that quarter's revenue. |

**Interviews.** Roles: `customer`, `current_employee`, `former_employee`,
`competitor`, `supplier`. Topics: `sales_team`, `working_conditions`,
`top_management`, `decision_making`, `competitive_edge`. Only some people know
about each topic; the others say they can't comment, and that answer still
uses up one interview.

| Topic | Who knows about it |
| --- | --- |
| `sales_team` | customer, competitor |
| `working_conditions` | current_employee, former_employee |
| `top_management` | former_employee, supplier |
| `decision_making` | supplier, customer |
| `competitive_edge` | competitor, customer |

People who know about a topic always agree with each other, so one good answer
is enough.

## Choose your 3 points and your rules

Pick any 3 of Fisher's 15 points and write them in `CHOSEN_POINTS` near the
top of `hw3.py`, e.g. `CHOSEN_POINTS = [1, 7, 15]`. The full questions are in
`FISHER_POINTS` in `hw3.py` and in the book.

| # | Fisher's point | Where the evidence is |
| --- | --- | --- |
| 1 | Market potential for sales growth | database |
| 2 | New products as current lines mature | database |
| 3 | Effective research and development | database |
| 4 | Above-average sales organization | interviews (`sales_team`) |
| 5 | Worthwhile profit margin | database |
| 6 | Maintaining or improving margins | database |
| 7 | Labour and personnel relations | interviews (`working_conditions`) |
| 8 | Executive relations | interviews (`top_management`) |
| 9 | Depth of management | interviews (`decision_making`) |
| 10 | Cost analysis and accounting controls | earnings calls, checked against the database |
| 11 | Advantage over competitors | interviews (`competitive_edge`) |
| 12 | Long-range outlook on profits | database |
| 13 | Growth without dilutive equity financing | database |
| 14 | Management talks freely about troubles | earnings calls, checked against the database |
| 15 | Management of unquestionable integrity | news file, checked against the database |

For each of your points, **you design the rule**: which evidence to use, what
to compute or whom to ask, and where the line between PASS and FAIL lies. PASS
means the company looks favourable on that point, as Fisher would see it.

We grade your verdicts against our own answer key. It uses one fixed rule per
point, which we do not publish, and the data never puts a company close to
the line of that rule, so a sensible reading of Fisher usually gives the same
answer. **Test your rules on the public answers**:
`public_test/ground_truth.json` gives the expected verdict on all 15 points
for each of the 20 public companies, and every point fails in 6 of them. If
your rule disagrees with the public answers, rethink it.

**Tip: calculate with tools, not in your head.** Do every sum, ratio and
comparison in SQL or Python, including the number of days between two dates
(SQLite's `julianday()` gives a date as a number of days).

## Student task

Fill in the three places in `hw3.py` marked `### YOUR CODE HERE`. You may add
imports, constants and helper functions above them, but do not change the
provided code below them:

- `CHOSEN_POINTS` lists your 3 points.
- `build_agent()` creates your model and anything else you need once.
- `analyze_companies(agent, companies)` receives
  `{company_folder: [list_documents, read_document, sql_query, interview]}`
  and returns, for every company,
  `{"points": {1: "PASS", 7: "FAIL", 15: "PASS"}, "summary": "..."}` (here with
  `CHOSEN_POINTS = [1, 7, 15]`).

Use the course techniques that fit your points: tool use for the database and
the interviews, planning which interviews to spend, routing each point to the
right source, prompt chaining, parallel calls, reflection to check your
answers, or a combination. Your code must read company data **only through
the four tools**; do not open `company.db` or `interviews.dat` yourself. Do
not hard-code folder names, company names or the public answers.

## Setup and public test

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
echo "DEEPSEEK_API_KEY=your_key_here" > .env
python3 hw3.py --data-folder public_test
```

Use your own DeepSeek API key (from https://platform.deepseek.com). The
required model is `deepseek-v4-flash`. We grade with our own key, so never put
a key in your code; `.env` must stay out of git.

The program creates `results.csv` with the columns `company`, `points`,
`points_correct`, `point_errors`, `interviews_used` and `summary`, and prints
the point score on your 3 points (the percentage after "balanced"). The point
score is the average of two shares: the share of truly passing verdicts that
you marked PASS and the share of truly failing verdicts that you marked FAIL.
For example, 90% and 60% give (90% + 60%) / 2 = 75%, and marking every point
PASS gives (100% + 0%) / 2 = 50%. Once `CHOSEN_POINTS` is set, the starter
runs before you add any API code (it returns no verdicts, so it scores 0%).

`public_test/ground_truth.json` gives the expected verdicts on all 15 points
for each public company. Grading uses a private set of about 20 unseen
companies with the same tools and the same answer key rules, but different
names and wording, and scores your 3 points only.

## Homework 3 solution:

### My 3 points and rules

I chose points **1, 5 and 7**. Together they force the agent to use two different
kinds of evidence — an order book and cost ledger on one side, an employee's own
words on the other — which is exactly the split Fisher draws between what a
company reports and what the outside world says about it.

| # | Fisher's question (his words) | Why I chose it | My PASS rule | Evidence |
| --- | --- | --- | --- | --- |
| 1 | "Does the company have products or services with sufficient market potential to make possible a sizable increase in sales for at least several years?" | It is Fisher's first filter: without a long runway of growth nothing else rescues the stock. "Sizable" is meaningless in the abstract, so it has to be made relative to the industry. | PASS when two-year revenue growth (FY2023 → FY2025, the last three complete years in the order book) is at or above the industry median two-year revenue growth. | `orders` (sum of `amount` by year) vs `industry_medians.revenue_growth_2y_pct` |
| 5 | "Does the company have a worthwhile profit margin?" | The word doing the work is "worthwhile". Fisher cares about margins because they pay for growth without new shares, and because they reveal pricing power; both are relative to what competitors earn. | PASS when the FY2025 operating margin, computed exactly as the homework defines it — (revenue − cost of sales − selling and admin − R&D) ÷ revenue — is at or above the industry median operating margin. | `orders` + `monthly_costs` vs `industry_medians.operating_margin_pct` |
| 7 | "Does the company have outstanding labor and personnel relations?" | This is the point that cannot be read off a database. It is Fisher's "scuttlebutt" method: you go outside the company and ask the people who actually work there, because management's "our people are our greatest strength" proves nothing. It also makes the agent plan a scarce resource (10 interviews). | PASS when a current or former employee describes fair pay, long tenure and grievances that get fixed when raised; FAIL when they describe constant overtime, heavy turnover, a strike or strike threat, or low morale. | `interview(role, topic="working_conditions")` — only `current_employee` and `former_employee` know this topic |

Why these three and not points 4, 6 or 15: 1 and 5 are pure arithmetic, so the
verdict cannot drift with wording — the private set changes wording, not SQL.
Point 7 is judgment on a quote, which a model does well, and it is the one point
where a model that only reads documents would be completely blind.

### Design

```mermaid
flowchart TD
    A["company folder<br/>list_documents · read_document · sql_query · interview"] --> B{"Router<br/>point → evidence source<br/>(static table, no model)"}
    B -->|"point 1 + point 5"| C["sql_query × 4<br/>revenue by year · costs by year<br/>industry medians · company industry"]
    C --> D["Python: growth_2y, operating margin<br/>compare with industry median"]
    D --> E["verdict 1, verdict 5<br/>deterministic"]
    B -->|"point 7"| F["Scuttlebutt sub-agent<br/>create_agent + interview tool"]
    F --> G["plan: current_employee first,<br/>former_employee as fallback,<br/>stop after one answer, ≤ 3 of 10 calls"]
    G --> H["quote"]
    H --> I["judge #1"]
    H --> J["judge #2 (independent reflection)"]
    I --> K["verdict 7<br/>reflection wins on conflict"]
    J --> K
    E --> L["summary writer<br/>sees verdicts, numbers, quote<br/>and the 2025 shareholder letter"]
    K --> L
    L --> M["results.csv"]
```

The router is a fixed table, not a model call: points 1 and 5 can only be
answered by `sql_query`, point 7 only by `interview`, so letting a model choose
would add cost and risk without adding information. Everything numeric happens
twice removed from the model — the model asks for the data through `sql_query`,
and Python turns the returned rows into a growth rate and an operating margin,
because the homework is right that model arithmetic is the main source of wrong
verdicts. Point 7 is the one place where a model must judge: a tool-calling
`create_agent` is given only the `interview` tool and a plan (ask the current
employee first, fall back to a former employee, stop as soon as someone gives a
substantive answer, never more than three of the ten calls), and then two
independent model calls read the same quote — the second one is told to decide
from scratch, and it wins if the two disagree, which is a cheap reflection step.
A final chained call writes the two-or-three sentence summary from the verdicts,
the computed ratios and the quote; it is explicitly forbidden to change a
verdict, and it also sees the 2025 letter to shareholders so that management's
claims can be set against the numbers rather than repeated. Companies are
analysed four at a time with `asyncio.gather` over `asyncio.to_thread`, each with
a 600-second timeout and its own `try/except`, so one bad API call or one odd
company still leaves a full `results.csv`. Two small defences matter more than
they look: the shareholder letter's file name is discovered through
`list_documents` instead of being assumed, and every model reply is parsed with
one retry, so a malformed answer costs one call rather than a verdict.

### Results

`python3 hw3.py --data-folder public_test` gives **60/60 correct point verdicts
on the 20 public companies, balanced score 100.0%** (30/30 truly passing points
marked PASS, 30/30 truly failing points marked FAIL), in about 80 seconds, using
**one interview per company out of the ten allowed**. I ran it five times; the
verdicts were identical every time.

There are **no companies where my verdicts disagree with
`public_test/ground_truth.json`**, so there is nothing to explain away. Because a
perfect score on the visible data is exactly the situation in which a student
fools themselves, I audited it instead of trusting it. The scripts are in the
repo and write nothing:

| Check | Script | Result |
| --- | --- | --- |
| `results.csv` is what the code produced, not edited by hand | `verify.py` | recomputes points 1 and 5 from the tools with different SQL (CASE WHEN instead of GROUP BY): **20/20 verdicts reproduced** |
| The answer key is not an absolute cut-off my rule happens to imitate | `verify.py` | **no** fixed growth or margin threshold can separate the 20 answers (0 of 40 candidate cut-offs), so the key must be industry-relative — which is what my rule is |
| The verdicts survive a different benchmark | `verify.py` | industry medians differ from medians computed across the 20 public peers (e.g. packaging 11.5% vs 18.3% growth); I use the table, as the homework says, and only the table reproduces the key |
| The rule is not knife-edge | `verify.py` | median distance to the line is 3.7 points; the tightest is Kestrel's margin, 10.0% against 8.3% (1.71 points) |
| Point 7 survives rewording, which is the one thing the private set changes | `test_robust.py` | each real quote rewritten twice by the model, then re-judged: **40/40 paraphrased verdicts still match the key** |
| Point 7 spends its interviews sensibly | `test_robust.py` | with a mock interview tool where the current employee refuses, the sub-agent falls back to a former employee and returns the right FAIL in 2 of 10 calls |
| One broken company cannot sink the run | `test_errors.py` | a company whose tools all raise still gets an entry and the other company is unaffected |

Two risks I want to be straight about. First, the rules were tuned while looking
at the public answers — the homework tells you to do exactly that ("test your
rules on the public answers... if your rule disagrees, rethink it"), but it does
mean the 100% is in-sample: it shows the rule matches the key, not that it
generalises to unknown companies. What gives me some confidence is that the
matching rule is the industry benchmark rather than a fitted number.

Second, the two database points depend on the definitions more than they look.
Re-running the same comparison with growth measured as one year (FY2023→FY2024)
instead of two, or as a CAGR, changes 14 of 20 verdicts; measuring the margin
before R&D changes 5, and using gross margin changes 6. That is not a flaw in the
rule, it is a warning that "revenue growth 2y" and "operating margin" have to be
the course's definitions, and they are: the FY2025 operating margin is
(revenue − cost of sales − selling and admin − R&D) ÷ revenue exactly as Section
3.2 defines it, and the growth rate is compared with the
`revenue_growth_2y_pct` column of `industry_medians`. A future version would
report both definitions side by side and flag any company where they disagree.


## Task 2: Reflection — AI sycophancy and AI psychosis

**Sycophancy is a data problem wearing a personality.** A model trained on human
approval learns that agreement is rewarded, and the failure is quiet: it does not
say "you are right", it says "here is a balanced view" and then quietly adopts
yours. This homework is a controlled experiment in that temptation. Every
company's shareholder letter is written to be agreeable — "our sales team is
second to none", "we have never cut research spending". An agent that reads those
documents and reports what it read will produce confident, well-written,
completely wrong analysis, because the letters are identical in tone for
companies that are failing. The 20 public companies make the cost visible: an
agent that passes everything scores 50% balanced, not 90%, because the metric
deliberately refuses to reward agreement. Sycophancy is not just flattering, it
is uninformative, and a scoring rule that separates the two shares is a cheap
way to make it expensive.

What I had to do to my own agent is the same operation in miniature. The first
version of the reasoning was going to be "ask the model to compare the margin
with the industry median". That is a request for agreement, not a check: given
two numbers and a rule, a model under pressure to be helpful will find a way to
make them consistent, and the homework warns that model arithmetic was its most
common cause of wrong verdicts. So the numbers never enter the model's head. They
are computed in Python from `sql_query` output, and the model is handed the
answer it must explain. The interesting part is that this made the summaries
*better*, not just safer: an agent that has nothing to decide writes more
precisely about what it saw. The same logic applies to the interview: two
independent judgements, the second told explicitly not to defer to the first,
because a reviewer who knows the answer is not a reviewer.

**AI psychosis is what happens when a system's model of the world stops being
corrected by the world.** It is not a single dramatic hallucination; it is a
loop with no exit condition. The agent invents a fact, builds a plan on it,
interprets every new observation as confirmation, and spends its budget
elaborating the mistake. The failure modes are structural, not emotional: no
termination criterion, no external check, unlimited self-reference. Anyone who
has watched an agent burn ten tool calls on a misinterpreted error message has
seen a small version of it.

The homework's design quietly punishes psychosis. There are ten interviews, so
an agent that keeps asking "to be sure" runs out and starts seeing refusals
everywhere; the grader runs three times, so luck does not survive; and the
private set is unseen, so a rule tuned to the public answers collapses. My
defences are deliberately boring: hard caps (three interview calls, 600 seconds
per company), a deterministic core that the model cannot talk itself out of, a
router that is a table rather than an opinion, and a reflection step whose only
job is to disagree once. The 100% score is not evidence that the agent is
intelligent; it is evidence that the loop has an exit.

Where this becomes uncomfortable is that the two failures are the same failure
seen from opposite ends. Sycophancy is the model bending its beliefs toward the
human; psychosis is the model bending its beliefs toward itself. Both are the
same missing term: contact with something that cannot be argued with. In this
homework that thing is `SUM(amount)` — an unglamorous number that does not care
what anyone thinks. The lesson I am taking away is not "use tools", it is that an
agent needs at least one part of its world that is expensive to lie to. A model
allowed to grade its own homework will always pass, and it will be sincere about
it, which is worse.

