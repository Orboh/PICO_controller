"""PICO の 1 回の握り（squeeze）で腕先（TCP）がどれだけ動いたかを、送り手の出力から読んで大きく出す（2026-11-04・作業者: Sun (Suraj)）。

使い方（別の端末で。送り手 arm_session.sh を動かしている間）:
    python3 scripts/tcp_watch.py runs/<tag>-sender.out [left|right] [--target 10]
送り手は 1 秒に 1 回 "TCP moved x … y … z … cm"（開始姿勢からの腕先の移動）を出す。握った瞬間の値を 0 として、
その握りの中での移動距離を出す。2 cm と目標（既定 10 cm）に届いたら印を出し、12 cm を超えたら止めるよう出す。手首モード（送り手 --orientation）では
握った瞬間からの回転（roll=x・pitch=y・yaw=z）も出し、--rot-target（既定 10°）と --rot-max（既定 15°）で印を出す。
読むだけ。腕にも送り手にも何も送らない。
"""
import argparse
import math
import re
import sys
import time

LINE = re.compile(r"^\[(left |right)\].*?\b(HOLD|ENGAGED|FAULT|LOCKED)\b.*TCP moved x *([-+0-9.]+) y *([-+0-9.]+) z *([-+0-9.]+) cm"
                  r"(?: rot x *([-+0-9.]+) y *([-+0-9.]+) z *([-+0-9.]+) deg)?")


def follow(path: str):
    """tail -F のように、ファイルに足された行を順に返す。"""
    while True:
        try:
            f = open(path, encoding="utf-8", errors="replace")
            break
        except OSError:
            print(f"waiting for {path} …", flush=True)
            time.sleep(1.0)
    f.seek(0, 2)
    while True:
        line = f.readline()
        if line:
            yield line
        else:
            time.sleep(0.2)


def main() -> None:
    """送り手の行を読み、握りごとの移動距離と 2 cm / 目標 / 12 cm の印を出す。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("sender_out")
    ap.add_argument("side", nargs="?", default="left", choices=["left", "right"])
    ap.add_argument("--target", type=float, default=10.0)
    ap.add_argument("--max", type=float, default=12.0)
    ap.add_argument("--rot-target", type=float, default=10.0, help="手首モード: 目標の回転角（度）")
    ap.add_argument("--rot-max", type=float, default=15.0, help="手首モード: これを超えたら戻すよう出す")
    a = ap.parse_args()
    anchor = None
    ranchor = None
    best = 0.0
    n = 0
    print(f"watching {a.sender_out} ({a.side} arm): squeeze → distance this squeeze; marks at 2 cm, {a.target:g} cm, stop over {a.max:g} cm", flush=True)
    for line in follow(a.sender_out):
        m = LINE.match(line)
        if not m or m.group(1).strip() != a.side:
            continue
        state = m.group(2)
        p = tuple(float(v) for v in m.group(3, 4, 5))
        if state == "ENGAGED":
            r = tuple(float(v) for v in m.group(6, 7, 8)) if m.group(6) is not None else None
            if anchor is None:
                anchor, best, n = p, 0.0, n + 1
                ranchor = r
                print(f"\n=== squeeze {n}: started ===", flush=True)
            d = [p[i] - anchor[i] for i in range(3)]
            dist = math.sqrt(sum(v * v for v in d))
            best = max(best, dist)
            axis = "xyz"[max(range(3), key=lambda i: abs(d[i]))]
            word = {"x": "forward/back", "y": "left/right", "z": "up/down"}[axis]
            if dist > a.max:
                mark = f"  !!! {dist:.1f} cm — TOO FAR, MOVE BACK NOW !!!"
            elif dist >= a.target:
                mark = f"  >>> {a.target:g} cm REACHED — hold 2 s, then come back <<<"
            elif dist >= 2.0:
                mark = "  ✓ 2 cm reached"
            else:
                mark = ""
            rtxt = ""
            if r is not None and ranchor is not None:
                dr = [r[i] - ranchor[i] for i in range(3)]
                k = max(range(3), key=lambda i: abs(dr[i]))
                ang = abs(dr[k])
                rname = ("roll", "pitch", "yaw")[k]
                if ang > a.rot_max:
                    rm = f" !!! {rname} {ang:.0f}° — TOO FAR, TURN BACK NOW !!!"
                elif ang >= a.rot_target:
                    rm = f" >>> {rname} {a.rot_target:g}° REACHED — hold 2 s, turn back <<<"
                else:
                    rm = ""
                rtxt = f"  | turn roll {dr[0]:+.0f} pitch {dr[1]:+.0f} yaw {dr[2]:+.0f} deg{rm}"
            print(f"  {dist:5.1f} cm  ({word}: x {d[0]:+.1f} y {d[1]:+.1f} z {d[2]:+.1f}){mark}{rtxt}", flush=True)
        else:
            if anchor is not None:
                print(f"=== squeeze {n}: released (furthest {best:.1f} cm) ===", flush=True)
                anchor = None
            if state in ("FAULT", "LOCKED"):
                print(f"  *** {state} — stop and tell the spotter ***", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
