#!/usr/bin/env python3
"""Ladder report — the Phase 3 proof table from the eval harness's episode records (D020/D021).

Reads the per-policy results the coordinator's eval mode writes (/data/eval/<model_version>.json)
and prints a markdown table plus seed-paired statistics against a baseline policy. Extension
files (same name + "-s<seed>", e.g. eval-ft-160ep-s1050.json) are merged with their base by
seed, so an N=100 run is base + one extension. Every comparison is paired on the identical
seeded scene, which removes scene difficulty from the variance.

  python3 ladder_report.py docs/eval-records/phase3-ladder \
      --baseline eval-teacher-v1 --rungs eval-ft-20ep:20,eval-ft-40ep:40,eval-ft-80ep:80,eval-ft-160ep:160

This is the static, reproducible form of the step-6 chart: no dashboard, no cluster, just the
records the loop produced.
"""
import argparse, glob, json, os
from math import comb


def load(dirpath, name):
    """Base results + any -s<seed> extensions, episodes merged and ordered by seed."""
    eps = []
    for f in sorted(glob.glob(os.path.join(dirpath, f"{name}.json")) +
                    glob.glob(os.path.join(dirpath, f"{name}-s*.json"))):
        eps += json.load(open(f))["episodes"]
    eps.sort(key=lambda e: e["seed"])
    return eps


def sign_test_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def summarize(eps, base=None):
    n = len(eps)
    succ = sum(e["task_success"] for e in eps)
    hist = [sum(1 for e in eps if e["cubes_placed"] == c) for c in range(4)]
    row = {"n": n, "succ": succ, "rate": succ / n, "cubes": sum(e["cubes_placed"] for e in eps) / n,
           "hist": hist, "smooth": sum(e["avg_smoothness"] for e in eps) / n}
    if base is not None:
        bm = {e["seed"]: e for e in base}
        pairs = [(bm[e["seed"]], e) for e in eps if e["seed"] in bm]
        fix = sum(1 for x, y in pairs if not x["task_success"] and y["task_success"])
        brk = sum(1 for x, y in pairs if x["task_success"] and not y["task_success"])
        both = [(x, y) for x, y in pairs if x["task_success"] and y["task_success"]]
        row.update(paired=len(pairs), fixed=fix, broken=brk, net=fix - brk, p=sign_test_p(fix, brk),
                   smooth_both=(sum(x["avg_smoothness"] for x, _ in both) / len(both),
                                sum(y["avg_smoothness"] for _, y in both) / len(both)) if both else None)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--baseline", required=True, help="model_version of the incumbent (v1)")
    ap.add_argument("--rungs", required=True, help="comma list of model_version:label")
    a = ap.parse_args()
    base = load(a.dir, a.baseline)
    rows = [("0 (" + a.baseline + ")", summarize(base))]
    for spec in a.rungs.split(","):
        name, label = spec.split(":")
        eps = load(a.dir, name)
        # pair only on seeds the baseline also has
        rows.append((label, summarize(eps, base)))
    print("| curated successes | N | success | mean cubes | 0/1/2/3 | fixed / broken | net | sign p | smooth (both solved) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for label, r in rows:
        hs = "/".join(map(str, r["hist"]))
        if "fixed" in r:
            sb = f"{r['smooth_both'][0]:.4f}→{r['smooth_both'][1]:.4f}" if r["smooth_both"] else "—"
            print(f"| {label} | {r['n']} | {r['rate']:.0%} ({r['succ']}/{r['n']}) | {r['cubes']:.2f} | {hs} | "
                  f"{r['fixed']} / {r['broken']} | {r['net']:+d} | {r['p']:.3f} | {sb} |")
        else:
            print(f"| {label} | {r['n']} | {r['rate']:.0%} ({r['succ']}/{r['n']}) | {r['cubes']:.2f} | {hs} | — | — | — | — |")


if __name__ == "__main__":
    main()
