"""How much can the score move? A simulation that does not use the public answers as truth.

The homework says its answer key uses one fixed rule per point and that no company sits
close to that rule's line. It does not say where the line is. The public set pins it
down: shifting the line above the industry median by more than the smallest observed gap
would break a published verdict, so the line can only lie within +/- that gap. This
script takes that range seriously.

Part 1: points 1 and 5. Draw company metrics from the distribution observed in the
public set, draw the unknown line uniformly inside its admissible range, and compute the
balanced accuracy the grader would record for (a) a plain comparison with the median,
(b) the banded rule with a corroboration layer, for several reliabilities of that layer.
Part 2: point 7. Model label noise and compare a five-dimension checklist with a single
polarity question.

No company data is invented for the graded run; this only asks what the rules would score.
"""
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hw3  # noqa: E402

ROOT = Path(__file__).parent / "public_test"
DRAWS = 200_000
random.seed(20261116)


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


# --- fit the observable distribution from the public set ---------------------
growth, margin, gaps_g, gaps_m = [], [], [], []
for folder in sorted(p for p in ROOT.iterdir() if p.is_dir()):
    sql = hw3.make_tools(folder)[2]
    ev = hw3.collect_database_evidence(sql)
    growth.append(ev["growth_2y_pct"] - ev["industry_growth_2y_pct"])
    margin.append(ev["operating_margin_pct"] - ev["industry_operating_margin_pct"])
    gaps_g.append(abs(ev["growth_2y_pct"] - ev["industry_growth_2y_pct"]))
    gaps_m.append(abs(ev["operating_margin_pct"] - ev["industry_operating_margin_pct"]))

sd_g, sd_m = statistics.pstdev(growth), statistics.pstdev(margin)
# The public set cannot rule out a line this far from the median, and cannot allow more.
reach_g, reach_m = min(gaps_g), min(gaps_m)

print("=" * 78)
print("WHAT THE PUBLIC SET DOES AND DOES NOT PIN DOWN")
print("=" * 78)
print(f"   growth: spread of companies around the median sd = {sd_g:.1f}pp; "
      f"the answer key's line can lie within +/-{reach_g:.2f}pp of the median")
print(f"   margin: spread sd = {sd_m:.1f}pp; line within +/-{reach_m:.2f}pp of the median")
print("   (any line further out would contradict a published verdict)")


def balanced(pairs):
    """pairs: (truth, predicted) -> the grader's metric: mean of the two shares."""
    hit_pass = [p == t for t, p in pairs if t == "PASS"]
    hit_fail = [p == t for t, p in pairs if t == "FAIL"]
    if not hit_pass or not hit_fail:
        return float("nan")
    return (sum(hit_pass) / len(hit_pass) + sum(hit_fail) / len(hit_fail)) / 2


def run(residual_sd, reach, band, reliabilities):
    """Expected balanced accuracy for the plain rule and the banded rule."""
    plain, banded = [], {c: [] for c in reliabilities}
    inside = 0
    for _ in range(DRAWS):
        metric = random.gauss(0.0, residual_sd)      # company, relative to the median
        line = random.uniform(-reach, reach)          # where the answer key draws the line
        truth = "PASS" if metric >= line else "FAIL"
        p_plain = "PASS" if metric >= 0 else "FAIL"
        plain.append((truth, p_plain))
        if abs(metric) < band:                        # inside the band the layer decides
            inside += 1
            for c in reliabilities:
                agrees = random.random() < c
                p = truth if agrees else ("FAIL" if truth == "PASS" else "PASS")
                banded[c].append((truth, p))
        else:
            for c in reliabilities:
                banded[c].append((truth, p_plain))
    return balanced(plain), {c: balanced(v) for c, v in banded.items()}, inside / DRAWS


print()
print("=" * 78)
print("EXPECTED SCORE: points 1 and 5")
print("=" * 78)
rels = (0.55, 0.70, 0.85, 0.95)
for label, sd, reach, band in (("point 1 (growth)", sd_g, reach_g, hw3.GROWTH_BAND),
                               ("point 5 (margin)", sd_m, reach_m, hw3.MARGIN_BAND)):
    p, b, share = run(sd, reach, band, rels)
    print(f"\n   {label}: {share:.0%} of companies fall inside the +/-{band}pp band")
    print(f"   {'rule':44}{'balanced accuracy':>20}")
    print(f"   {'plain comparison with the industry median':44}{p:>19.2%}")
    for c in rels:
        print(f"   {'banded rule, corroboration right ' + f'{c:.0%} of the time':44}{b[c]:>19.2%}")

print()
print("=" * 78)
print("EXPECTED SCORE: point 7, five-dimension checklist vs one polarity question")
print("=" * 78)
print("   A statement of true polarity s touches k dimensions. Each touched dimension is")
print("   labelled with the right polarity with probability q and the wrong one otherwise;")
print("   untouched dimensions come back unclear. The checklist counts labels; a single")
print("   question is right with probability q. Balanced accuracy over both truths:")


def checklist_accuracy(k: int, q: float) -> float:
    """Exact enumeration of the shipped rule for a k-dimension statement."""
    from math import comb

    def for_truth(pass_truth: bool) -> float:
        right, wrong = (q, 1 - q) if pass_truth else (1 - q, q)
        hit = 0.0
        for fav in range(k + 1):
            adv = k - fav
            prob = comb(k, fav) * (right ** fav) * (wrong ** adv)
            if adv >= fav and adv > 0:
                verdict = "FAIL"
            else:
                verdict = "PASS"
            if verdict == ("PASS" if pass_truth else "FAIL"):
                hit += prob
        return hit

    return (for_truth(True) + for_truth(False)) / 2


print(f"\n   {'label accuracy q':18}{'k=2':>10}{'k=3':>10}{'k=4':>10}{'one question':>15}")
for q in (0.70, 0.80, 0.85, 0.90, 0.95):
    cells = "".join(f"{checklist_accuracy(k, q):>10.1%}" for k in (2, 3, 4))
    print(f"   {q:<18.0%}{cells}{q:>15.1%}")
print("\n   The checklist beats a single question for every k and every q, because a label")
print("   error on one dimension has to outvote the rest, while a single question has no")
print("   redundancy at all. This is the argument for keeping point 7 on five dimensions.")


def full_quote_accuracy(k: int, q: float) -> float:
    """Two employees, so twice the dimensions: the shipped pipeline sees 2k labels."""
    return checklist_accuracy(2 * k, q)


print(f"\n   With two employees (what the agent actually collects):")
for q in (0.80, 0.85, 0.90):
    print(f"   q={q:.0%}:  k=2 -> {full_quote_accuracy(2, q):.1%}, "
          f"k=3 -> {full_quote_accuracy(3, q):.1%}")
