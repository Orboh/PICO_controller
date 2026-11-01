"""Collection GUI (log-038/039/040): one page for leader-arm and PICO episodes, in the runbook's order, English/Japanese.

Run: `bash scripts/gui.sh` and open http://localhost:8090 (on this PC). The page checks the connections (PICO Wi-Fi,
PC Service, headset, PICO input, leader / follower CAN, cameras), starts and stops the teleop child and the recorder,
types the recorder's keys, and shows live status. It never switches robot power.
Roles: the PAGE OPERATOR presses the buttons; the SPOTTER keeps the e-stop and the power switch and confirms step ②
by name; the WEARER / leader operator teleoperates. Only one browser tab controls the session; others watch.
Stages (from the Dream-Lab Collect tab): dry (2, check) → gate (10, reviewer's OK) → session.
"""
from __future__ import annotations

import argparse
import threading
import time

from nicegui import app as ng_app
from nicegui import context, run, ui

from . import checks
from .i18n import refusal, t
from .session import EXPECTED_SHA, SIDES, STAGE_EPISODES, CollectionSession, Options, SessionError

try:  # the headset's facing angle, same formula as the sender's facing check
    from pico_teleop.sender import headset_facing_deg
except Exception:  # pragma: no cover - only without the SDK path
    headset_facing_deg = None

STATE_COLOR = {"HOLD": "grey-7", "ENGAGED": "positive", "FAULT": "warning", "STOPPED": "negative", "—": "grey-5"}


class CheckBoard:
    """Background connection checks for the current method / arm (every 3 s), shared by all tabs."""

    def __init__(self, sess: CollectionSession):
        self.sess = sess
        self.target = ("pico", "left")
        self.results: dict[str, checks.Check] = {}
        self.slow: dict[str, tuple[float, checks.Check]] = {}   # input test and cameras: on demand, valid 60 s
        self.testing = False                        # the input test holds the PICO reader lock (① waits)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                self._once()
            except Exception as e:  # never freeze silently (Elephant 11-01 #3)
                self.results = {"chk.board": checks.Check("chk.board", False, f"check loop error: {e}", "fix.board")}
            time.sleep(3.0)

    def _once(self) -> None:
        if True:
            method, side = (self.sess.opts.method, self.sess.opts.side) if self.sess.running else self.target
            res = {c.key: c for c in checks.quick(method, side)}
            ok, why = self.sess.gng(side)
            res["chk.gng"] = checks.Check("chk.gng", ok, why, "fix.gng")
            if not self.sess.running:
                busy = self.sess.probe()
                res["chk.free"] = checks.Check("chk.free", not busy, ", ".join(busy) or "free", "fix.free")
            if self.sess.monitor is not None and self.sess.teleop_running:
                s = self.sess.monitor.status
                live = self.sess.monitor.fresh()
                res["chk.input"] = checks.Check("chk.input", live and s.input_hz >= 30,
                                                f"{s.input_hz:.0f} Hz (live from the sender)" if live else "no fresh status", "fix.input")
            self.results = res

    def all(self) -> list[checks.Check]:
        now = time.monotonic()
        merged = {k: c for k, (t0, c) in self.slow.items() if now - t0 < 60.0}
        merged.update(self.results)
        return list(merged.values())


def build(sess: CollectionSession, board: CheckBoard) -> None:
    o = Options()
    o.episodes = STAGE_EPISODES[o.stage]          # dry starts with 2
    me = context.client.id
    lang = {"v": "en"}
    if sess.controller is None:
        sess.controller = me

    def L(key, **kw):
        return t(key, lang["v"], **kw)

    def mine() -> bool:
        return sess.controller == me

    def act(fn, *a, ok: str | None = None, **k):
        if not mine():
            ui.notify(refusal("this tab only watches: another tab controls the session", lang["v"]), type="warning")
            return None
        try:
            r = fn(*a, **k)
            if ok:
                ui.notify(ok, type="positive")
            return r
        except SessionError as e:
            ui.notify(refusal(str(e), lang["v"]), type="negative", multi_line=True, timeout=9000)

    texts: list[tuple[object, str, str]] = []       # (element, attribute, key) re-texted on language change

    def tx(el, key, attr="text"):
        texts.append((el, attr, key))
        _set(el, attr, L(key))
        return el

    def _set(el, attr, value):
        if attr == "text":
            el.text = value
        elif attr == "label":
            el.props(f'label="{value}"')
        elif attr == "placeholder":
            el.props(f'placeholder="{value}"')

    def switch_lang():
        lang["v"] = "ja" if lang["v"] == "en" else "en"
        for el, attr, key in texts:
            _set(el, attr, L(key))
        seen["eps"] = None
        seen["chk"] = None

    ui.page_title("OpenArm collection — leader / PICO")
    with ui.header().classes("items-center bg-blue-grey-9"):
        tx(ui.label(), "title").classes("text-h6")
        tx(ui.label(), "subtitle").classes("text-caption")
        ui.space()
        gng = ui.label().classes("text-body2")
        tx(ui.button(on_click=switch_lang).props("flat color=white"), "lang")
    banner = ui.label().classes("w-full text-h6 text-white bg-negative q-pa-sm")
    leader_note = tx(ui.label(), "leader_note").classes("w-full text-body2 bg-amber-2 q-pa-xs")
    watch = ui.row().classes("w-full items-center bg-blue-1 q-pa-xs")
    with watch:
        tx(ui.label(), "watch")
        tx(ui.button(on_click=lambda: (setattr(sess, "controller", me), ui.notify("ok"))), "take")

    with ui.row().classes("w-full no-wrap items-start q-pa-sm gap-4"):
        # ------------------------------------------------------------------ left: checks + setup + episodes
        with ui.column().classes("w-1/3 min-w-[340px] gap-3"):
            with ui.card().classes("w-full"):
                tx(ui.label(), "checks").classes("text-subtitle1 text-weight-bold")
                tx(ui.label(), "checks_hint").classes("text-caption")
                chk_box = ui.column().classes("w-full gap-0")

                async def test_input():
                    if sess.teleop_running:
                        ui.notify(L("checks_hint"), type="warning")
                        return
                    ui.notify("…")
                    board.testing = True
                    try:
                        board.slow["chk.input"] = (time.monotonic(), await run.io_bound(checks.pico_input))
                    finally:
                        board.testing = False
                    seen["chk"] = None

                async def test_cams():
                    if sess.recorder_running:
                        return
                    board.slow["chk.cameras"] = (time.monotonic(), await run.io_bound(checks.cameras, o.side))
                    seen["chk"] = None
                with ui.row():
                    b_tin = tx(ui.button(on_click=test_input).props("outline"), "test_input")
                    tx(ui.button(on_click=test_cams).props("outline"), "test_cams")

            with ui.card().classes("w-full") as setup_card:
                tx(ui.label(), "setup").classes("text-subtitle1 text-weight-bold")
                m_toggle = ui.toggle({"pico": "PICO", "leader": "Leader"}, value="pico").bind_value(o, "method")
                tx(ui.label(), "planned").classes("text-caption text-blue-grey-8")
                s_toggle = ui.toggle({s: s for s in SIDES}, value="left").bind_value(o, "side")
                st_toggle = ui.toggle({"dry": "dry", "gate": "gate", "session": "session"}, value="dry")

                def on_stage(e):
                    o.stage = e.value
                    if e.value in STAGE_EPISODES:
                        o.episodes = STAGE_EPISODES[e.value]
                st_toggle.on_value_change(on_stage)
                stage_help = ui.label().classes("text-caption")
                both_note = ui.label().classes("text-caption text-blue-grey-8")
                tag_in = tx(ui.input().bind_value(o, "tag").classes("w-48"), "tag", "label")
                import pathlib as _pl
                scenes = sorted(p.stem for p in (_pl.Path("~/projects/openarm-orboh/scenes").expanduser()).glob("*.yaml") if not p.stem.startswith("_"))
                scene_in = tx(ui.select(["none"] + scenes, value="none").bind_value(o, "scene").classes("w-72"), "scene", "label")
                with ui.row().classes("w-full"):
                    tx(ui.number(value=2, min=1, max=50, precision=0).bind_value(o, "episodes", forward=lambda v: int(v or 1)).classes("w-28"), "episodes", "label")
                    tx(ui.number(value=30, min=5, max=300, precision=0).bind_value(o, "episode_time_s", forward=lambda v: int(v or 30)).classes("w-28"), "seconds", "label")
                tx(ui.input().bind_value(o, "operator").classes("w-full"), "operator", "label")
                tx(ui.input().bind_value(o, "task").classes("w-full"), "task", "label")
                with ui.column().classes("w-full").bind_visibility_from(o, "method", value="pico"):
                    tx(ui.switch(value=True).bind_value(o, "smooth"), "smooth")
                    tx(ui.switch(value=False).bind_value(o, "wrist"), "wrist")
                    tx(ui.number(value=0, min=0, max=15).bind_value(o, "premove_cm", forward=lambda v: float(v or 0)).classes("w-64"), "premove", "label")
                running_opts = ui.label().classes("text-caption text-blue-grey-8")

            review_card = ui.card().classes("w-full bg-amber-1")
            with review_card:
                tx(ui.label(), "review").classes("text-subtitle1 text-weight-bold")
                review_info = ui.label()
                reviewer_in = tx(ui.input(), "reviewer", "label").classes("w-full")
                tx(ui.button(on_click=lambda: act(sess.record_review, reviewer_in.value or "", ok="OK")), "review_ok")

            with ui.card().classes("w-full"):
                tx(ui.label(), "episodes_card").classes("text-subtitle1 text-weight-bold")
                ep_box = ui.column().classes("w-full gap-1")

        # ------------------------------------------------------------------ middle: run + live status
        with ui.column().classes("w-1/3 min-w-[320px] gap-3"):
            with ui.card().classes("w-full"):
                tx(ui.label(), "run").classes("text-subtitle1 text-weight-bold")
                now_step = ui.label().classes("text-body1 text-weight-bold text-primary")
                b_teleop = tx(ui.button(on_click=lambda: act(sess.start_teleop, Options(**vars(o)), ok="①")).classes("w-full"), "b.teleop")
                with ui.row().classes("w-full no-wrap items-center"):
                    spotter_in = tx(ui.input().classes("w-40"), "spotter", "label")
                    b_power = tx(ui.button(on_click=lambda: act(sess.confirm_power, spotter_in.value or "", ok="②")).classes("grow"), "b.power")
                b_rec = tx(ui.button(on_click=lambda: act(sess.start_recorder, ok="③")).classes("w-full"), "b.rec")
                ui.separator()
                with ui.row().classes("w-full no-wrap"):
                    k_next = tx(ui.button(on_click=lambda: act(sess.key, "next")).classes("grow"), "b.next")
                    k_rerec = tx(ui.button(on_click=lambda: act(sess.key, "rerecord")).classes("grow"), "b.rerec")

                pending = {"key": "stop"}
                with ui.dialog() as hands_dialog, ui.card():
                    tx(ui.label(), "hands_q")
                    with ui.row():
                        tx(ui.button(on_click=lambda: (hands_dialog.close(), act(sess.key, pending["key"], hands_off=True))), "hands_yes")
                        tx(ui.button(on_click=hands_dialog.close), "cancel")

                def leader_key(name: str):
                    if sess.opts.method == "leader":
                        pending["key"] = name
                        hands_dialog.open()
                    else:
                        act(sess.key, name)
                with ui.row().classes("w-full no-wrap"):
                    k_reset = tx(ui.button(on_click=lambda: leader_key("reset")).classes("grow"), "b.reset")
                    k_stop = tx(ui.button(on_click=lambda: leader_key("stop")).classes("grow"), "b.end")
                b_pre = tx(ui.button(on_click=lambda: act(sess.premove)).classes("w-full"), "b.pre")
                with ui.dialog() as abort_dialog, ui.card():
                    tx(ui.label(), "abort_q")
                    with ui.row():
                        tx(ui.button(on_click=lambda: (abort_dialog.close(), act(sess.abort_recorder, confirmed=True, ok="Ctrl-C"))).props("color=negative"), "abort_yes")
                        tx(ui.button(on_click=abort_dialog.close), "cancel")
                b_abort = tx(ui.button(on_click=abort_dialog.open).classes("w-full").props("color=negative outline"), "b.abort")
                ui.separator()

                with ui.dialog() as held_dialog, ui.card():
                    tx(ui.label(), "held_q")
                    with ui.row():
                        tx(ui.button(on_click=lambda: (held_dialog.close(), act(sess.stop_teleop, leader_held=True, ok="④"))), "held_yes")
                        tx(ui.button(on_click=held_dialog.close), "cancel")

                def stop_teleop():
                    if sess.opts.method == "leader" and sess.teleop_running and not sess.recorder_running:
                        held_dialog.open()
                    else:
                        act(sess.stop_teleop, ok="④")
                b_stop = tx(ui.button(on_click=stop_teleop).classes("w-full"), "b.stop")
                tx(ui.label(), "estop_note").classes("text-caption")

            with ui.card().classes("w-full"):
                tx(ui.label(), "live").classes("text-subtitle1 text-weight-bold")
                with ui.row().classes("items-center"):
                    st_badge = ui.badge("—").classes("text-h6 q-px-md")
                    st_link = ui.label()
                st_wall = ui.label().classes("text-orange-9 text-weight-medium")
                st_in = ui.label()
                st_sha = ui.label()
                st_rec = ui.label().classes("text-weight-medium")
                st_last = ui.label().classes("text-caption")

        # ------------------------------------------------------------------ right: logs
        with ui.column().classes("grow min-w-[320px] gap-3"):
            with ui.card().classes("w-full"):
                tx(ui.label(), "logs.session").classes("text-subtitle1 text-weight-bold")
                sess_log = ui.log(max_lines=200).classes("w-full h-40")
            with ui.card().classes("w-full"):
                tx(ui.label(), "logs.rec").classes("text-subtitle1 text-weight-bold")
                rec_log = ui.log(max_lines=300).classes("w-full h-56")
            with ui.card().classes("w-full"):
                tx(ui.label(), "logs.tel").classes("text-subtitle1 text-weight-bold")
                tel_log = ui.log(max_lines=300).classes("w-full h-56")

    seen = {"sess": 0, "rec": 0, "tel": 0, "ev": 0, "eps": None, "rec_obj": None, "tel_obj": None, "chk": None, "prev": {}}
    COLORS = {b_teleop: "primary", b_power: "orange", b_rec: "deep-orange", k_next: "positive", k_rerec: "blue-grey",
              k_reset: "orange", k_stop: "red-9", b_pre: "orange", b_stop: "primary"}

    def enable(b, on: bool):
        on = on and mine()
        b.set_enabled(on)
        b.props(f"color={COLORS[b] if on else 'grey-5'}")

    def pull(log, child, key):
        if child is None:
            return
        if seen[key + "_obj"] is not child:
            seen[key + "_obj"], seen[key] = child, 0
        lines, seen[key] = child.new_lines(seen[key])
        for line in lines:
            log.push(line)

    def refresh_checks():
        items = board.all()
        key = (lang["v"], tuple((c.key, c.ok, c.detail) for c in items))
        # notifications: a check that was OK and is now failing (or back), for every tab
        for c in items:
            before = seen["prev"].get(c.key)
            if before is True and c.ok is False:
                ui.notify(L("lost", what=L(c.key)), type="negative", position="top", timeout=10000)
            elif before is False and c.ok is True:
                ui.notify(L("back", what=L(c.key)), type="positive", position="top")
            seen["prev"][c.key] = c.ok
        if key == seen["chk"]:
            return
        seen["chk"] = key
        chk_box.clear()
        with chk_box:
            for c in items:
                with ui.row().classes("items-start no-wrap w-full"):
                    ui.label("✅" if c.ok else "⏸" if c.ok is None else "❌").classes("w-6")
                    with ui.column().classes("gap-0"):
                        ui.label(L(c.key)).classes("text-body2 text-weight-medium")
                        ui.label(c.detail).classes("text-caption text-grey-8")
                        if c.ok is False:
                            ui.label("→ " + L(c.fix)).classes("text-caption text-negative")

    def refresh_episodes():
        key = (lang["v"], tuple(sorted(sess.notes)), sess.episode, sess.recording_now,
               tuple(sorted((k, v["text"]) for k, v in sess.notes.items())))
        if key == seen["eps"]:
            return
        seen["eps"] = key
        ep_box.clear()
        with ep_box:
            if not sess.notes:
                ui.label(L("no_ep")).classes("text-caption")
            for ep in sorted(sess.notes):
                n = sess.notes[ep]
                with ui.row().classes("items-center no-wrap w-full"):
                    ui.label(f"ep {ep}" + (" ●" if ep == sess.episode and sess.recording_now else "")).classes("w-14")
                    ui.checkbox(L("grasp"), value=n["grasp"], on_change=lambda e, ep=ep: sess.set_note(ep, grasp=e.value))
                    ui.checkbox(L("placed"), value=n["placed"], on_change=lambda e, ep=ep: sess.set_note(ep, placed=e.value))
                    ui.input(placeholder=L("note"), value=n["text"], on_change=lambda e, ep=ep: sess.set_note(ep, text=e.value)).classes("grow")
                    ui.label(L("boundary_s", s=sess.episode_walls.get(ep, 0.0))).classes("text-caption w-24")

    def tick():
        sess.poll()
        if mine() and not sess.running:
            board.target = (o.method, o.side)
        if st_toggle.value != o.stage:
            st_toggle.value = o.stage
        both_note.text = (L("both.pico") if o.method == "pico" else L("both.leader")) if o.side == "both" else ""
        tag_in.set_visibility(o.side == "both" and o.method == "leader" and o.stage == "session")
        scene_in.set_visibility(not (o.side == "both" and o.method == "leader") and o.stage != "dry")
        stage_help.text = L(f"stage.{o.stage}") + ("" if o.stage != "session" else
                                                   ("  ✅ gate OK: " + sess.gate_ok(o.method, o.side)["reviewer"]) if sess.gate_ok(o.method, o.side)
                                                   else "  ⛔ no reviewed gate yet")
        if seen.get("optlang") != lang["v"]:
            seen["optlang"] = lang["v"]
            m_toggle.set_options({"pico": L("pico"), "leader": L("leader")}, value=o.method)
            s_toggle.set_options({s: L("arm", s=L(s)) for s in SIDES}, value=o.side)
        ok, why = sess.gng(o.side if not sess.running else sess.opts.side)
        gng.text = ("✅ go/no-go: " if ok else "⛔ go/no-go: ") + why
        tel, rec = sess.teleop_running, sess.recorder_running
        locked = sess.locked()
        foreign = sess.monitor is not None and sess.monitor.status.foreign
        watch.set_visibility(not mine())
        leader_note.set_visibility((o.method if not sess.running else sess.opts.method) == "leader")
        banner.text = ("ANOTHER SENDER DRIVES THIS ARM — E-STOP NOW / 別の送信が腕を動かしている — 非常停止" if foreign else
                       sess.alarm or ("LOCKED: release, End recording, ④, start again (R2)" if locked else ""))
        banner.text = refusal(banner.text, lang["v"]) if banner.text else ""
        banner.set_visibility(bool(banner.text))
        review_card.set_visibility(sess.gate_done is not None)
        if sess.gate_done:
            review_info.text = L("review_info", n=sess.gate_done["episodes"]) + (f"  ({sess.gate_done['dataset']})" if sess.gate_done.get("dataset") else "")
        for el in setup_card.descendants():
            if hasattr(el, "set_enabled"):
                el.set_enabled(not sess.running and mine())
        running_opts.text = ("running: " + ", ".join(f"{k}={v}" for k, v in vars(sess.opts).items())) if sess.running else ""
        b_tin.set_enabled(not tel)
        enable(b_teleop, not tel and not rec and not board.testing)
        enable(b_power, tel and not sess.power_confirmed and not sess.recorder_used)
        spotter_in.set_enabled(tel and not sess.power_confirmed and mine())
        enable(b_rec, tel and sess.power_confirmed and not rec and not sess.recorder_used and not locked)
        enable(k_next, rec and not locked)
        enable(k_rerec, rec and not locked)
        enable(k_reset, rec)
        enable(k_stop, rec)
        b_pre.set_visibility(sess.opts.method == "pico" and sess.opts.premove_cm > 0 and tel)
        enable(b_pre, rec and not sess.recording_now)
        enable(b_stop, tel and not rec)
        b_abort.set_visibility(sess.recorder_stuck() and mine())
        now_step.text = L("now") + L("s.teleop" if not tel and not rec else "s.stop" if sess.recorder_used and not rec else
                                     "s.power" if not sess.power_confirmed else "s.rec" if not rec else
                                     "s.ep" if sess.recording_now else "s.wait")
        if sess.monitor is not None:
            s = sess.monitor.status
            live = tel and sess.monitor.fresh()
            st_badge.text = "LOCKED" if locked else (s.state if live else "EXITED" if not tel else "no status")
            st_badge.props(f"color={'negative' if locked else STATE_COLOR.get(s.state, 'grey-5') if live else 'grey-5'}")
            st_link.text = f"link {s.link}" if live else ""
            st_wall.text = (f"BOUNDARY: {s.wall} — move back" if (s.wall and live) else "") + f"   ({s.wall_s:.1f} s)"
            face = "—" if s.facing_deg is None else f"{s.facing_deg:+.0f}°" + ("  ⚠ face forward" if abs(s.facing_deg) > 75 else "")
            st_in.text = (f"PICO input {s.input_hz:.0f} Hz" + ("  ⚠ NO DATA" if s.input_hz < 5 else "") + f" · facing {face}") if live else ""
            st_last.text = f"last: {s.last_event[:110]}"
            if seen["ev"] < sess.monitor.n_events:
                n = sess.monitor.n_events - seen["ev"]
                for tt, e in list(sess.monitor.events)[-n:]:
                    tel_log.push(f"{tt:7.1f}s  {e}")
                seen["ev"] = sess.monitor.n_events
        else:
            st_badge.text = ("leader agent running" if tel else "—") if sess.opts.method == "leader" else "—"
            st_badge.props("color=grey-6")
            st_link.text = st_wall.text = st_in.text = st_last.text = ""
            pull(tel_log, sess.teleop, "tel")
        sha, sha_ok = sess.sha_line()
        st_sha.text = "" if sess.opts.method != "pico" or not tel else (
            f"start pose sha {sha} ✅" if sha_ok else f"start pose sha {sha or '…'} (expected {EXPECTED_SHA[sess.opts.side]})")
        elapsed = (f" · {time.monotonic() - sess.episode_started:.0f} / {sess.opts.episode_time_s} s"
                   if sess.recording_now and sess.episode_started else "")
        keys = ("" if not rec else " · keys from this page ✅" if sess.keys_from_page else
                " · ⚠ keys NOT from this page (desktop keyboard?)" if sess.keys_from_page is False else " · keys: waiting for the recorder")
        saved = f" · saved {sess.saved_episodes} of {sess.opts.episodes}" if sess.saved_episodes is not None else ""
        st_rec.text = (("RECORDING episode " + str(sess.episode) + elapsed) if sess.recording_now else
                       ("recorder waiting" if rec else "recorder off")) + keys + saved + (f" · data {sess.dataset}" if sess.dataset else "")
        if seen["sess"] < sess.n_log:
            n = sess.n_log - seen["sess"]
            for line in list(sess.log)[-n:]:
                sess_log.push(line)
            seen["sess"] = sess.n_log
        pull(rec_log, sess.recorder, "rec")
        refresh_checks()
        refresh_episodes()

    ui.timer(0.5, tick)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this PC only (default)")
    ap.add_argument("--port", type=int, default=8090)
    a = ap.parse_args(argv)
    sess = CollectionSession(facing=headset_facing_deg)
    board = CheckBoard(sess)

    def on_shutdown():
        for m in sess.shutdown():
            print(f"[gui] {m}", flush=True)
    ng_app.on_shutdown(on_shutdown)

    @ui.page("/")
    def index():
        build(sess, board)

    ui.run(host=a.host, port=a.port, title="OpenArm collection", reload=False, show=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
