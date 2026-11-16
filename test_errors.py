"""Error-containment test: one broken company must not remove results.csv."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hw3  # noqa: E402


class Broken:
    def invoke(self, *args, **kwargs):
        raise RuntimeError("simulated tool outage")


async def main() -> None:
    hw3.load_env_file()
    folders = sorted(p for p in (Path(__file__).parent / "public_test").iterdir() if p.is_dir())[:2]
    good, bad = folders[0].name, folders[1].name
    companies = {p.name: hw3.make_tools(p) for p in folders}
    companies[bad] = [Broken(), Broken(), Broken(), Broken()]

    result = await hw3.analyze_companies(hw3.build_agent(), companies)

    print(f"entries returned: {sorted(result)}")
    print(f"{good}: {result[good]['points']}")
    print(f"{bad}: {result[bad]['points']}")
    print(f"{bad} summary: {result[bad]['summary']}")
    assert set(result) == {good, bad}, "a company went missing"
    assert result[bad]["points"], "broken company has no verdicts at all"
    print("\nOK: both companies present, the broken one degrades to placeholders instead of crashing")


if __name__ == "__main__":
    asyncio.run(main())
