"""Per-episode wall time and hand slip for PICO sessions (Kento 2026-11-03: the wall-episode rule).

Reads the collection page's notes (runs/gui-*-notes.json). For each PICO episode: seconds held at a wall, cm of
hand slip, and whether it is flagged for review (> 3 s or > 20 cm, provisional). Flagged episodes are NOT dropped:
before training, someone watches the flagged part and decides keep / trim the wall segment / drop.

With --from-log the numbers are recomputed from the sender's per-cycle JSONL inside each episode's sender-clock
window (the notes keep the window as `sender_t`), instead of trusting the page's live counters.

    python tools/wall_report.py                         # all notes under runs/
    python tools/wall_report.py runs/gui-pico-left-*-notes.json --from-log
    python tools/wall_report.py --json > runs/wall-report.json   # for Kento: the distribution + the flagged list
    python tools/wall_report.py --wall-s 3 --slip-cm 20          # try other thresholds on the same data

Leader-arm episodes are listed but never flagged and not counted in the distribution (a leader cannot slip).
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def from_log(log: pathlib.Path, t0: float, t1: float) -> tuple[float, float | None]:
    """(seconds with any arm at a wall, cm of slip summed over arms) for sender cycles with t0 <= t <= t1."""
    hz, cycles, first, last = 50.0, 0, None, None
    with open(log, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "t" not in d:
                hz = float((d.get("args") or {}).get("hz", hz))
                continue
            if not t0 <= d["t"] <= t1:
                continue
            arms = (d.get("arms") or {}).values()
            if any(a.get("wall") for a in arms):
                cycles += 1
            slips = [a.get("slip_m") for a in arms]
            slip = None if not slips or any(s is None for s in slips) else sum(slips)
            first = slip if first is None else first
            last = slip
    return cycles / hz, (None if first is None or last is None else (last - first) * 100.0)


def flag(method: str, wall_s: float, slip_cm: float | None, wall_lim: float, slip_lim: float) -> str:
    if method != "pico":
        return ""
    why = []
    if wall_s > wall_lim:
        why.append(f"wall {wall_s:.1f} s > {wall_lim:g}")
    if slip_cm is not None and slip_cm > slip_lim:
        why.append(f"slip {slip_cm:.0f} cm > {slip_lim:g}")
    return "; ".join(why)


def rows(paths: list[str], use_log: bool, wall_lim: float, slip_lim: float) -> list[dict]:
    out = []
    for p in sorted(paths):
        try:
            n = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        opts = n.get("options") or {}
        method = opts.get("method", "?")
        log = pathlib.Path(n["sender_log"]) if n.get("sender_log") else None
        for ep, e in sorted((n.get("episodes") or {}).items(), key=lambda kv: int(kv[0])):
            wall, slip, src = float(e.get("wall_s") or 0.0), e.get("slip_cm"), "page"
            if use_log and log and log.exists() and e.get("sender_t"):
                wall, slip = from_log(log, *e["sender_t"])
                src = "log"
            out.append({"tag": n.get("tag"), "episode": int(ep), "method": method, "side": opts.get("side"),
                        "stage": opts.get("stage"), "pose_set": n.get("pose_set") or opts.get("pose_set"),
                        "dataset": n.get("dataset"), "wall_s": round(wall, 2),
                        "slip_cm": None if slip is None else round(slip, 1), "source": src,
                        "grasp": e.get("grasp"), "placed": e.get("placed"), "note": e.get("text", ""),
                        "flag": flag(method, wall, slip, wall_lim, slip_lim)})
    return out


def quantiles(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)

    def q(p: float) -> float:
        k = (len(xs) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)
    return {"n": len(xs), "min": xs[0], "median": q(0.5), "p75": q(0.75), "p90": q(0.9), "max": xs[-1],
            "mean": round(sum(xs) / len(xs), 2)}


def histogram(xs: list[float], edges: list[float]) -> list[tuple[str, int]]:
    bins = []
    for lo, hi in zip([-math.inf] + edges, edges + [math.inf]):
        label = f"<= {hi:g}" if lo == -math.inf else f"> {lo:g}" if hi == math.inf else f"{lo:g}-{hi:g}"
        bins.append((label, sum(1 for x in xs if (lo < x <= hi) or (lo == -math.inf and x <= hi))))
    return bins


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notes", nargs="*", help="notes files (default: runs/gui-*-notes.json)")
    ap.add_argument("--from-log", action="store_true", help="recompute from the sender JSONL (needs sender_t in the notes)")
    ap.add_argument("--wall-s", type=float, default=3.0, help="flag above this many seconds at a wall (provisional 3)")
    ap.add_argument("--slip-cm", type=float, default=20.0, help="flag above this much hand slip (provisional 20)")
    ap.add_argument("--stage", choices=["dry", "gate", "session"], action="append",
                    help="only these stages (repeatable; default: all)")
    ap.add_argument("--json", action="store_true", help="print one JSON object (rows, distribution, flagged)")
    a = ap.parse_args(argv)
    paths = a.notes or glob.glob(str(REPO / "runs" / "gui-*-notes.json"))
    rs = [r for r in rows(paths, a.from_log, a.wall_s, a.slip_cm) if not a.stage or r["stage"] in a.stage]
    pico = [r for r in rs if r["method"] == "pico"]
    walls = [r["wall_s"] for r in pico]
    slips = [r["slip_cm"] for r in pico if r["slip_cm"] is not None]
    report = {"thresholds": {"wall_s": a.wall_s, "slip_cm": a.slip_cm, "provisional": True},
              "pico_episodes": len(pico), "episodes_without_slip_data": len(pico) - len(slips),
              "wall_s": quantiles(walls), "slip_cm": quantiles(slips),
              "wall_s_histogram": histogram(walls, [0, 1, 3, 5, 10]),
              "slip_cm_histogram": histogram(slips, [0, 5, 10, 20, 40]),
              "flagged": [r for r in pico if r["flag"]], "rows": rs}
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    print(f"{'tag':38s} {'ep':>3s} {'method':6s} {'wall s':>7s} {'slip cm':>8s}  flag")
    for r in rs:
        slip = "—" if r["slip_cm"] is None else f"{r['slip_cm']:.1f}"
        print(f"{(r['tag'] or '?')[:38]:38s} {r['episode']:3d} {r['method']:6s} {r['wall_s']:7.1f} {slip:>8s}  {r['flag']}")
    print(f"\nPICO episodes: {len(pico)} (no slip data: {report['episodes_without_slip_data']}; senders before log-047)")
    for k in ("wall_s", "slip_cm"):
        print(f"{k:8s} {report[k]}")
        print("         " + "  ".join(f"{lab}: {n}" for lab, n in report[k + "_histogram"]))
    print(f"flagged (> {a.wall_s:g} s or > {a.slip_cm:g} cm, provisional): {len(report['flagged'])} of {len(pico)} — "
          "review, do not drop automatically")
    return 0


if __name__ == "__main__":
    sys.exit(main())
