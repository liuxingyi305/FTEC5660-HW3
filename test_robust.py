"""Stress tests for point 7, the only point that depends on language.

A. Rewording test: paraphrase each real interview (same stance, different words -
   which is what the private set does), then judge the paraphrase with
   (a) the shipped five-dimension pipeline and (b) the single-question version it
   replaced, and compare both with the answer key.
B. Fallback test: a mock interview tool where the current employee refuses, to
   check the sub-agent still hears the former employee and the refusal is dropped.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hw3  # noqa: E402

ROOT = Path(__file__).parent / "public_test"
GT = json.loads((ROOT / "ground_truth.json").read_text())
FOLDERS = sorted(p for p in ROOT.iterdir() if p.is_dir())

STYLES = (
    "as a formal written complaint or commendation sent to the HR department",
    "as a casual remark to a friend, understated and a little indirect",
    "in terse, clipped sentences, like someone being careful about being quoted",
)

REWRITE = """Rewrite this employee statement about their employer in different words, {style}. Keep \
the same stance and the same facts: do not soften a complaint or weaken praise, do not add anything \
new, do not change what the person says they saw."""

LEGACY_SYSTEM = """Judge one employee statement against this rule for Fisher point 7:
{rule}

Reply with ONLY {{"verdict": "PASS" or "FAIL"}}."""

_model_cache = None


def _model():
    global _model_cache
    if _model_cache is None:
        hw3.load_env_file()
        _model_cache = hw3.build_agent()
    return _model_cache


async def paraphrase(text: str, idx: int) -> str:
    out = await asyncio.to_thread(
        _model().invoke,
        [("system", REWRITE.format(style=STYLES[idx - 1])), ("human", f"Statement: {text}")],
    )
    return " ".join(str(out.content).split())


def legacy_verdict(quote: str) -> str:
    reply = hw3._call_json(_model(), LEGACY_SYSTEM.format(rule=hw3.LABOUR_RULE), f'Statement: "{quote}"')
    return str(reply.get("verdict", "")).strip().upper()


async def part_a() -> None:
    print("=" * 78)
    print("A. REWORDING TEST: same stance, different words")
    print("=" * 78)
    sem = asyncio.Semaphore(6)

    async def one(name: str) -> dict:
        folder = next(p for p in FOLDERS if p.name == name)
        tools = hw3.make_tools(folder)
        raw = str(tools[3].invoke({"role": "current_employee", "topic": "working_conditions"}))
        quote = raw.split(":", 1)[-1].strip().strip('"')
        expected = GT[name]["points"]["7"]
        async with sem:
            try:
                texts = await asyncio.gather(*(paraphrase(quote, i) for i in (1, 2, 3)))
            except Exception as exc:
                print(f"   ! {name}: rewrite failed ({exc})")
                return {"name": name, "expected": expected, "rows": []}
        rows = []
        for text in texts:
            async with sem:
                labels = await asyncio.to_thread(hw3.label_dimensions, _model(), name, text)
                new = hw3.labour_judgement([labels])["verdict"]
            async with sem:
                old = await asyncio.to_thread(legacy_verdict, text)
            rows.append((text, old, new))
        return {"name": name, "expected": expected, "rows": rows}

    results = await asyncio.gather(*(one(p.name) for p in FOLDERS))
    old_hits = new_hits = total = 0
    print(f"   {'company':24}{'key':>6}   {'single-question / five-dimension per paraphrase':<46}")
    for r in results:
        cells = []
        for _, old, new in r["rows"]:
            total += 1
            old_hits += old == r["expected"]
            new_hits += new == r["expected"]
            cells.append(f"{old or '??'}/{(new or '??')}")
        if cells:
            flag = "" if all(c.split("/")[1] == r["expected"] for c in cells) else "   <-- new rule wrong"
            print(f"   {r['name'][:23]:24}{r['expected']:>6}   {'  '.join(cells):<46}{flag}")
    print(f"\n   single-question rule: {old_hits}/{total} ({old_hits / total:.1%})")
    print(f"   five-dimension rule:  {new_hits}/{total} ({new_hits / total:.1%})")


def part_b() -> None:
    print()
    print("=" * 78)
    print("B. REFUSAL AND FALLBACK TEST")
    print("=" * 78)
    from langchain.agents import create_agent
    from langchain_core.tools import tool

    calls: list[tuple[str, str]] = []

    @tool
    def interview(role: str, topic: str) -> str:
        """Interview one person about the company (mock, for testing only)."""
        calls.append((role, topic))
        if role == "current_employee":
            return 'A production supervisor: "I would rather not say anything about that."'
        if role == "former_employee":
            return 'A former supervisor: "Overtime never stopped and half my team left within a year."'
        return 'A contact: "I cannot comment on that."'

    agent = create_agent(
        _model(),
        tools=[interview],
        system_prompt=hw3.INTERVIEW_SYSTEM.format(
            company="Test Co", industry="testing", budget=hw3.INTERVIEW_BUDGET
        ),
    )
    result = agent.invoke({"messages": [{"role": "user", "content": "Hear what the workforce says."}]})
    payload = hw3._extract_json(hw3._message_text(result["messages"][-1]))
    kept = [q for q in payload.get("quotes", []) if not hw3._looks_like_refusal(str(q.get("text", "")))]
    print(f"   calls made: {calls}")
    print(f"   quotes returned: {[q.get('role') for q in payload.get('quotes', [])]}")
    print(f"   after dropping refusals: {[q.get('role') for q in kept]}")
    if kept:
        labels = hw3.label_dimensions(_model(), "Test Co", str(kept[0]["text"]))
        print(f"   dimensions: {labels}")
        print(f"   verdict: {hw3.labour_judgement([labels])['verdict']} (key would say FAIL)")
    else:
        print("   !! the sub-agent returned nothing usable")


if __name__ == "__main__":
    asyncio.run(part_a())
    part_b()
