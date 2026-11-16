"""Stress tests for point 7, the only point a model touches.

A. Rewording test: paraphrase each real interview quote (the private set changes
   wording, not facts), then re-judge the paraphrases and compare with the key.
B. Fallback test: a mock interview tool where the current employee will not
   comment, to check the sub-agent actually falls back to a former employee.
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

REWRITE = """Rewrite this employee statement about their employer in different words. \
Keep the same stance and the same facts: if it is a complaint it stays a complaint, if it is \
praise it stays praise. Change the vocabulary and sentence structure as a different person would \
say it. Do not add new facts, numbers or opinions. Reply with the rewritten sentence only, \
without quotation marks."""

model = None


def _model():
    global model
    if model is None:
        hw3.load_env_file()
        model = hw3.build_agent()
    return model


async def rewrite(text: str, idx: int) -> str:
    out = await asyncio.to_thread(
        _model().invoke,
        [("system", REWRITE), ("human", f"Statement: {text}\n\nRewrite {idx}.")],
    )
    return " ".join(str(out.content).split())


async def part_a() -> None:
    print("=" * 78)
    print("A. REWORDING TEST: same meaning, different words (private set behaves like this)")
    print("=" * 78)
    sem = asyncio.Semaphore(6)

    async def one(name: str) -> list[tuple[str, str, str]]:
        tools = hw3.make_tools(next(p for p in FOLDERS if p.name == name))
        quote = str(tools[3].invoke({"role": "current_employee", "topic": "working_conditions"}))
        quote = quote.split(":", 1)[-1].strip().strip('"')
        expected = GT[name]["points"]["7"]
        variants = []
        async with sem:
            try:
                texts = await asyncio.gather(rewrite(quote, 1), rewrite(quote, 2))
            except Exception as exc:
                print(f"   ! {name}: rewrite failed {exc}")
                return []
        for text in texts:
            async with sem:
                verdict, reason = await asyncio.to_thread(hw3.judge_labour, _model(), text)
            variants.append((text, verdict, expected))
        return variants

    names = [p.name for p in FOLDERS]
    rows = await asyncio.gather(*(one(name) for name in names))
    total = correct = 0
    print(f"   {'company':26}{'expected':>9}{'rewrite 1':>11}{'rewrite 2':>11}")
    for name, variants in zip(names, rows):
        if not variants:
            continue
        cells = []
        for _, verdict, expected in variants:
            total += 1
            correct += verdict == expected
            cells.append(verdict)
        flag = "" if all(v == variants[0][2] for v in cells) else "   <-- FLIP"
        print(f"   {name[:25]:26}{variants[0][2]:>9}{cells[0]:>11}{cells[1]:>11}{flag}")
    print(f"\n   paraphrased verdicts matching the key: {correct}/{total} ({correct / total:.1%})")


def part_b() -> None:
    print()
    print("=" * 78)
    print("B. FALLBACK TEST: current employee will not comment -> sub-agent must ask a former one")
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
        system_prompt=hw3.INTERVIEW_SYSTEM.format(company="Test Co", industry="testing", budget=hw3.INTERVIEW_BUDGET),
    )
    result = agent.invoke({"messages": [{"role": "user", "content": "Interview the people who know."}]})
    payload = hw3._extract_json(hw3._message_text(result["messages"][-1]))
    quote = str(payload.get("quote", ""))
    print(f"   calls made: {calls}")
    print(f"   quote returned: {quote[:90]!r}")
    if quote:
        verdict, reason = hw3.judge_labour(_model(), quote)
        print(f"   verdict on that quote: {verdict} ({reason})")
        print(f"   expected: FAIL, and it took {len(calls)} of 10 interviews")
    else:
        print("   !! the sub-agent gave up instead of falling back")


if __name__ == "__main__":
    asyncio.run(part_a())
    part_b()
