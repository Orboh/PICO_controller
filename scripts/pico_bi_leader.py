"""PICO → LEADER の両腕: 左右の受け手（pico_leader_receiver.sh）を 1 つの端末で動かす（2026-11-04・作業者: Sun (Suraj)）。

使い方（送り手 `pico_bi.sh sender` を先に動かしてから 30 秒以内に）:
    POSE_SET=<set> python3 scripts/pico_bi_leader.py
- 左右それぞれを擬似端末（pty）で動かし、出力に [L] / [R] を付けて出す。
- この端末で押したキーは両方に送る。**esc 1 回で両腕が経由地を戻って脱力する**（受け手の普通の終わり方）。
- Ctrl-C はこのプログラムが受けて、両方の受け手に Ctrl-C を送る（即座に脱力する＝非常時。先に腕を支える）。
- どちらかが止まったら、もう片方にも esc を送って両方を終わらせる（片腕だけ動き続けないように）。
"""
import os
import pty
import select
import signal
import subprocess
import sys
import termios
import time
import tty

HERE = os.path.dirname(os.path.abspath(__file__))
RECEIVER = os.path.join(HERE, "pico_leader_receiver.sh")


def spawn(side: str) -> tuple[subprocess.Popen, int]:
    """片腕の受け手を擬似端末で起こす。(process, pty の親側 fd) を返す。"""
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)          # 送ったキー（esc）を出力にこだまさせない
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    cmd = os.environ.get("PICO_BI_LEADER_CMD")          # テスト用: 受け手の代わりに動かすコマンド（{side} を置き換える）
    argv = ["bash", "-c", cmd.format(side=side)] if cmd else ["bash", RECEIVER, side]
    p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True, close_fds=True)
    os.close(slave)
    return p, master


def main() -> int:
    """左右の受け手を動かし、キーを両方へ送り、出力を印付きで出す。終わったら終了コードの大きい方を返す。"""
    if not os.environ.get("POSE_SET") and not os.environ.get("PICO_BI_LEADER_CMD"):
        print("[pico-bi-leader] POSE_SET を指定すること（送り手と同じ開始姿勢セット）", file=sys.stderr)
        return 2
    kids = {"L": spawn("left")}
    time.sleep(float(os.environ.get("PICO_BI_LEADER_GAP_S", "1.0")))   # 左右を少しずらして起こす（CAN の立ち上げを重ねない）
    kids["R"] = spawn("right")
    buf = {k: b"" for k in kids}
    esc_sent = False

    def send_all(data: bytes) -> None:
        for p, fd in kids.values():
            if p.poll() is None:
                try:
                    os.write(fd, data)
                except OSError:
                    pass

    def on_sigint(_sig, _frm) -> None:
        print("\n[pico-bi-leader] Ctrl-C: 両方の受け手に Ctrl-C を送る（即座に脱力）", flush=True)
        for p, _ in kids.values():
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGINT)

    signal.signal(signal.SIGINT, on_sigint)
    tty_in = sys.stdin.isatty()
    old = termios.tcgetattr(sys.stdin) if tty_in else None
    if tty_in:
        tty.setcbreak(sys.stdin.fileno())                 # 1 文字ずつ読む（esc を Enter なしで送る）
    print("[pico-bi-leader] 左右の受け手を起こした。両腕が自分で開始姿勢へ動く。esc = 両腕を終える・Ctrl-C = 非常時", flush=True)
    try:
        while True:
            fds = [fd for p, fd in kids.values() if p.poll() is None or True]
            r, _, _ = select.select(fds + ([sys.stdin] if tty_in else []), [], [], 0.2)
            for k, (p, fd) in kids.items():
                if fd in r:
                    try:
                        data = os.read(fd, 4096)
                    except OSError:
                        data = b""
                    if data:
                        buf[k] += data.replace(b"\r", b"\n")
                        *lines, buf[k] = buf[k].split(b"\n")
                        for ln in lines:
                            if ln.strip() and b"Teleop loop time" not in ln:
                                sys.stdout.write(f"[{k}] " + ln.decode("utf-8", "replace") + "\n")
                        sys.stdout.flush()
            if tty_in and sys.stdin in r:
                key = os.read(sys.stdin.fileno(), 16)
                if key:
                    send_all(key)
                    if b"\x1b" in key:
                        esc_sent = True
            done = [k for k, (p, _) in kids.items() if p.poll() is not None]
            if done and not esc_sent and len(done) < len(kids):
                print(f"[pico-bi-leader] {done[0]} の受け手が止まった: もう片方にも esc を送る", flush=True)
                send_all(b"\x1b")
                esc_sent = True
            if len(done) == len(kids):
                # 終わった受け手の最後の出力を読み切ってから終える（すぐ終わると最後の行が落ちていた）
                for k, (_p, fd) in kids.items():
                    while True:
                        try:
                            data = os.read(fd, 4096)
                        except OSError:
                            break
                        if not data:
                            break
                        buf[k] += data.replace(b"\r", b"\n")
                    for ln in buf[k].split(b"\n"):
                        if ln.strip() and b"Teleop loop time" not in ln:
                            sys.stdout.write(f"[{k}] " + ln.decode("utf-8", "replace") + "\n")
                    buf[k] = b""
                sys.stdout.flush()
                break
    finally:
        if tty_in:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
    codes = {k: p.returncode for k, (p, _) in kids.items()}
    print(f"[pico-bi-leader] 終わり: {codes}", flush=True)
    return max(abs(c or 0) for c in codes.values())


if __name__ == "__main__":
    sys.exit(main())
