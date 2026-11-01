"""Collection GUI (log-038): one page for leader-arm and PICO episodes, in the order of the runbook.

Run: `bash scripts/gui.sh` and open http://localhost:8090 (on this PC). The page starts and stops the teleop child
and the recorder, types the recorder's keys, and shows live status. It never switches robot power.
"""
from __future__ import annotations

import argparse

from nicegui import ui

from .session import EXPECTED_SHA, METHODS, SIDES, CollectionSession, Options, SessionError, go_no_go_today

try:  # the headset's facing angle, same formula as the sender's facing check
    from pico_teleop.sender import headset_facing_deg
except Exception:  # pragma: no cover - only without the SDK path
    headset_facing_deg = None

STATE_COLOR = {"HOLD": "grey-7", "ENGAGED": "positive", "FAULT": "warning", "STOPPED": "negative", "—": "grey-5"}


def build(sess: CollectionSession) -> None:
    o = Options()

    def act(fn, *a, ok: str | None = None, **k):
        try:
            r = fn(*a, **k)
            if ok:
                ui.notify(ok, type="positive")
            return r
        except SessionError as e:
            ui.notify(str(e), type="negative", multi_line=True, timeout=8000)

    ui.page_title("OpenArm collection — leader / PICO")
    with ui.header().classes("items-center bg-blue-grey-9"):
        ui.label("OpenArm collection").classes("text-h6")
        ui.label("leader arm or PICO · one arm · same recorder").classes("text-caption")
        ui.space()
        gng = ui.label().classes("text-body2")

    with ui.row().classes("w-full no-wrap items-start q-pa-sm gap-4"):
        # ------------------------------------------------------------------ left: setup + steps
        with ui.column().classes("w-1/3 min-w-[320px] gap-3"):
            with ui.card().classes("w-full"):
                ui.label("1  Setup").classes("text-subtitle1 text-weight-bold")
                ui.toggle({"pico": "PICO controllers", "leader": "Leader arm"}, value="pico").bind_value(o, "method")
                ui.toggle({s: f"{s} arm" for s in SIDES}, value="left").bind_value(o, "side")
                with ui.row().classes("w-full"):
                    ui.number("Episodes", value=5, min=1, max=50, precision=0).bind_value(o, "episodes", forward=int).classes("w-28")
                    ui.number("Seconds each", value=30, min=5, max=300, precision=0).bind_value(o, "episode_time_s", forward=int).classes("w-28")
                ui.input("Operator (half-width)").bind_value(o, "operator").classes("w-full")
                ui.input("Task").bind_value(o, "task").classes("w-full")
                with ui.column().classes("w-full").bind_visibility_from(o, "method", value="pico"):
                    ui.switch("Smooth motion (60 Hz + ease in/out, log-037)", value=True).bind_value(o, "smooth")
                    ui.switch("Wrist rotation (roll/pitch/yaw)", value=False).bind_value(o, "wrist")
                    ui.number("Pre-move forward (cm, 0 = off)", value=0, min=0, max=15).bind_value(o, "premove_cm", forward=float).classes("w-64")

            with ui.card().classes("w-full"):
                ui.label("Episodes").classes("text-subtitle1 text-weight-bold")
                ep_box = ui.column().classes("w-full gap-1")

        # ------------------------------------------------------------------ middle: run + live status
        with ui.column().classes("w-1/3 min-w-[320px] gap-3"):
            with ui.card().classes("w-full"):
                ui.label("2  Run (in this order)").classes("text-subtitle1 text-weight-bold")
                b_teleop = ui.button("① Start teleop", on_click=lambda: act(sess.start_teleop, Options(**vars(o)), ok="teleop started")).classes("w-full")
                b_power = ui.button("② Spotter: \"arm may move\" — power ON", color="orange",
                                    on_click=lambda: act(sess.confirm_power, ok="spotter confirmed")).classes("w-full")
                b_rec = ui.button("③ Start recording (the arm moves to the start pose)", color="deep-orange",
                                  on_click=lambda: act(sess.start_recorder, ok="recorder started")).classes("w-full")
                ui.separator()
                with ui.row().classes("w-full no-wrap"):
                    k_next = ui.button("▶ Next episode", color="positive", on_click=lambda: act(sess.key, "next")).classes("grow")
                    k_rerec = ui.button("↺ Re-record", on_click=lambda: act(sess.key, "rerecord")).classes("grow")
                with ui.row().classes("w-full no-wrap"):
                    k_reset = ui.button("⟲ Reset (arm moves)", color="orange", on_click=lambda: act(sess.key, "reset")).classes("grow")
                    k_stop = ui.button("■ Stop recording", color="negative", on_click=lambda: act(sess.key, "stop")).classes("grow")
                b_pre = ui.button("Pre-move forward (arm moves slowly)", color="orange",
                                  on_click=lambda: act(sess.premove)).classes("w-full")
                ui.separator()

                with ui.dialog() as held_dialog, ui.card():
                    ui.label("Ctrl-C makes the LEADER arm go limp. Is someone holding the leader arm?")
                    with ui.row():
                        ui.button("Yes, the leader arm is held", color="negative",
                                  on_click=lambda: (held_dialog.close(), act(sess.stop_teleop, leader_held=True, ok="teleop stopped")))
                        ui.button("Cancel", on_click=held_dialog.close)

                def stop_teleop():
                    if sess.opts.method == "leader" and sess.teleop_running and not sess.recorder_running:
                        held_dialog.open()
                    else:
                        act(sess.stop_teleop, ok="teleop stopped")
                b_stop = ui.button("④ Stop teleop (after the recording ended)", on_click=stop_teleop).classes("w-full")
                ui.label("Spotter keeps the e-stop and the power switch. This page never switches power.").classes("text-caption")

            with ui.card().classes("w-full"):
                ui.label("Live status").classes("text-subtitle1 text-weight-bold")
                with ui.row().classes("items-center"):
                    st_badge = ui.badge("—").classes("text-h6 q-px-md")
                    st_link = ui.label()
                st_lock = ui.label().classes("text-negative text-weight-bold")
                st_wall = ui.label().classes("text-orange-9 text-weight-medium")
                st_in = ui.label()
                st_sha = ui.label()
                st_rec = ui.label()
                st_last = ui.label().classes("text-caption")

        # ------------------------------------------------------------------ right: logs
        with ui.column().classes("grow min-w-[320px] gap-3"):
            with ui.card().classes("w-full"):
                ui.label("Session log").classes("text-subtitle1 text-weight-bold")
                sess_log = ui.log(max_lines=200).classes("w-full h-40")
            with ui.card().classes("w-full"):
                ui.label("Recorder output").classes("text-subtitle1 text-weight-bold")
                rec_log = ui.log(max_lines=300).classes("w-full h-56")
            with ui.card().classes("w-full"):
                ui.label("Teleop output / events").classes("text-subtitle1 text-weight-bold")
                tel_log = ui.log(max_lines=300).classes("w-full h-56")

    seen = {"sess": 0, "rec": 0, "tel": 0, "ev": 0, "eps": None}

    def push(log, buf, key):
        items = list(buf)
        new = items[seen[key]:] if len(items) >= seen[key] else items
        for line in new:
            log.push(line)
        seen[key] = len(items)

    def refresh_episodes():
        key = (tuple(sorted(sess.notes)), sess.episode)
        if key == seen["eps"]:
            return
        seen["eps"] = key
        ep_box.clear()
        with ep_box:
            if not sess.notes:
                ui.label("no episode yet").classes("text-caption")
            for ep in sorted(sess.notes):
                n = sess.notes[ep]
                with ui.row().classes("items-center no-wrap w-full"):
                    ui.label(f"ep {ep}" + (" ●" if ep == sess.episode else "")).classes("w-14")
                    ui.checkbox("grasp", value=n["grasp"], on_change=lambda e, ep=ep: sess.set_note(ep, grasp=e.value))
                    ui.checkbox("placed", value=n["placed"], on_change=lambda e, ep=ep: sess.set_note(ep, placed=e.value))
                    ui.input(placeholder="note", value=n["text"], on_change=lambda e, ep=ep: sess.set_note(ep, text=e.value)).classes("grow")

    def tick():
        sess.poll()
        ok, why = go_no_go_today()
        gng.text = ("✅ go/no-go today: " if ok else "⚠️ go/no-go today: ") + why
        tel, rec = sess.teleop_running, sess.recorder_running
        b_teleop.set_enabled(not tel and not rec)
        b_power.set_enabled(tel and not sess.power_confirmed)
        b_rec.set_enabled(tel and sess.power_confirmed and not rec)
        for b in (k_next, k_rerec, k_reset, k_stop):
            b.set_enabled(rec)
        b_pre.set_visibility(sess.opts.method == "pico" and sess.opts.premove_cm > 0)
        b_stop.set_enabled(tel and not rec)
        if sess.monitor is not None:
            s = sess.monitor.status
            st_badge.text = "LOCKED" if s.locked else s.state
            st_badge.props(f"color={'negative' if s.locked else STATE_COLOR.get(s.state, 'grey-5')}")
            st_link.text = f"link {s.link}"
            st_lock.text = "LOCKED: release, Stop recording, then restart both (R2)" if s.locked else ""
            st_wall.text = (f"WALL: {s.wall} — move back" if s.wall else "") + f"   (at a wall {s.wall_s:.1f} s this session)"
            face = "—" if s.facing_deg is None else f"{s.facing_deg:+.0f}°" + ("  ⚠ turn to face forward" if abs(s.facing_deg) > 75 else "")
            st_in.text = f"PICO input {s.input_hz:.0f} Hz" + ("  ⚠ NO DATA: turn sending on in the app" if s.input_hz < 5 else "") + f" · facing {face}"
            st_last.text = f"last: {s.last_event[:110]}"
            ev = list(sess.monitor.events)
            for t, e in ev[seen["ev"]:]:
                tel_log.push(f"{t:7.1f}s  {e}")
            seen["ev"] = len(ev)
        else:
            st_badge.text = "leader" if sess.opts.method == "leader" and tel else "—"
            st_badge.props("color=grey-6")
            st_link.text = st_lock.text = st_wall.text = st_in.text = st_last.text = ""
            if sess.teleop is not None:
                push(tel_log, sess.teleop.lines, "tel")
        sha, sha_ok = sess.sha_line()
        st_sha.text = "" if sess.opts.method != "pico" or not tel else (
            f"start pose sha {sha} ✅" if sha_ok else f"start pose sha {sha or '…'} (expected {EXPECTED_SHA[sess.opts.side]})")
        st_rec.text = ("recording" if rec else "recorder off") + (f" · episode {sess.episode}" if sess.episode is not None else "") + (
            f" · dataset {sess.dataset}" if sess.dataset else "")
        push(sess_log, sess.log, "sess")
        if sess.recorder is not None:
            push(rec_log, sess.recorder.lines, "rec")
        refresh_episodes()

    ui.timer(0.5, tick)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this PC only (default)")
    ap.add_argument("--port", type=int, default=8090)
    a = ap.parse_args(argv)
    sess = CollectionSession(facing=headset_facing_deg)

    @ui.page("/")
    def index():
        build(sess)

    ui.run(host=a.host, port=a.port, title="OpenArm collection", reload=False, show=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
