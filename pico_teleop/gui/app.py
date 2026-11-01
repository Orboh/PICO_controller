"""Collection GUI (log-038, review fixes log-039): one page for leader-arm and PICO episodes, in the runbook's order.

Run: `bash scripts/gui.sh` and open http://localhost:8090 (on this PC). The page starts and stops the teleop child
and the recorder, types the recorder's keys, and shows live status. It never switches robot power.
Roles: the PAGE OPERATOR (a third person at the PC) presses the buttons; the SPOTTER keeps the e-stop and the
power switch and confirms step ② by name; the WEARER / leader operator teleoperates.
Only one browser tab controls the session; other tabs only watch.
"""
from __future__ import annotations

import argparse
import time

from nicegui import app as ng_app
from nicegui import context, ui

from .session import EMERGENCY, EXPECTED_SHA, SIDES, CollectionSession, Options, SessionError

try:  # the headset's facing angle, same formula as the sender's facing check
    from pico_teleop.sender import headset_facing_deg
except Exception:  # pragma: no cover - only without the SDK path
    headset_facing_deg = None

STATE_COLOR = {"HOLD": "grey-7", "ENGAGED": "positive", "FAULT": "warning", "STOPPED": "negative", "—": "grey-5"}


def build(sess: CollectionSession) -> None:
    o = Options()
    me = context.client.id
    if sess.controller is None:
        sess.controller = me

    def mine() -> bool:
        return sess.controller == me

    def act(fn, *a, ok: str | None = None, **k):
        if not mine():
            ui.notify("this tab only watches: another tab controls the session", type="warning")
            return None
        try:
            r = fn(*a, **k)
            if ok:
                ui.notify(ok, type="positive")
            return r
        except SessionError as e:
            ui.notify(str(e), type="negative", multi_line=True, timeout=9000)

    ui.page_title("OpenArm collection — leader / PICO")
    with ui.header().classes("items-center bg-blue-grey-9"):
        ui.label("OpenArm collection").classes("text-h6")
        ui.label("leader arm or PICO · one arm · same recorder").classes("text-caption")
        ui.space()
        gng = ui.label().classes("text-body2")
    banner = ui.label().classes("w-full text-h6 text-white bg-negative q-pa-sm")
    leader_note = ui.label("Leader method: the joint_7 leader/follower offset (~89°, log-021) is still open — data for the "
                           "comparison only after Kento's fix.").classes("w-full text-body2 bg-amber-2 q-pa-xs")
    watch = ui.row().classes("w-full items-center bg-blue-1 q-pa-xs")
    with watch:
        ui.label("This tab only watches (another tab controls).")
        ui.button("Take control here", on_click=lambda: (setattr(sess, "controller", me), ui.notify("this tab controls now")))

    with ui.row().classes("w-full no-wrap items-start q-pa-sm gap-4"):
        # ------------------------------------------------------------------ left: setup + episodes
        with ui.column().classes("w-1/3 min-w-[320px] gap-3"):
            with ui.card().classes("w-full") as setup_card:
                ui.label("1  Setup (locked while a session runs)").classes("text-subtitle1 text-weight-bold")
                ui.toggle({"pico": "PICO controllers", "leader": "Leader arm"}, value="pico").bind_value(o, "method")
                ui.toggle({s: f"{s} arm" for s in SIDES}, value="left").bind_value(o, "side")
                with ui.row().classes("w-full"):
                    ui.number("Episodes", value=5, min=1, max=50, precision=0).bind_value(o, "episodes", forward=int).classes("w-28")
                    ui.number("Seconds each", value=30, min=5, max=300, precision=0).bind_value(o, "episode_time_s", forward=int).classes("w-28")
                ui.input("Operator name (latin letters, required)").bind_value(o, "operator").classes("w-full")
                ui.input("Task (same text for both methods)").bind_value(o, "task").classes("w-full")
                with ui.column().classes("w-full").bind_visibility_from(o, "method", value="pico"):
                    ui.switch("Smooth motion (60 Hz + ease in/out, log-037)", value=True).bind_value(o, "smooth")
                    ui.switch("Wrist rotation (roll/pitch/yaw)", value=False).bind_value(o, "wrist")
                    ui.number("Pre-move forward (cm, 0 = off)", value=0, min=0, max=15).bind_value(o, "premove_cm", forward=float).classes("w-64")
                running_opts = ui.label().classes("text-caption text-blue-grey-8")

            with ui.card().classes("w-full"):
                ui.label("Episodes (notes saved at once)").classes("text-subtitle1 text-weight-bold")
                ep_box = ui.column().classes("w-full gap-1")

        # ------------------------------------------------------------------ middle: run + live status
        with ui.column().classes("w-1/3 min-w-[320px] gap-3"):
            with ui.card().classes("w-full"):
                ui.label("2  Run — the page operator presses, in this order").classes("text-subtitle1 text-weight-bold")
                now_step = ui.label().classes("text-body1 text-weight-bold text-primary")
                b_teleop = ui.button("① Start teleop", on_click=lambda: act(sess.start_teleop, Options(**vars(o)), ok="teleop started")).classes("w-full")
                with ui.row().classes("w-full no-wrap items-center"):
                    spotter_in = ui.input("Spotter name").classes("w-40")
                    b_power = ui.button("② Spotter: \"arm may move\", power ON",
                                        on_click=lambda: act(sess.confirm_power, spotter_in.value or "", ok="spotter confirmed")).classes("grow")
                b_rec = ui.button("③ Start recording (the arm moves to the start pose)",
                                  on_click=lambda: act(sess.start_recorder, ok="recorder started")).classes("w-full")
                ui.separator()
                with ui.row().classes("w-full no-wrap"):
                    k_next = ui.button("▶ Next episode", on_click=lambda: act(sess.key, "next")).classes("grow")
                    k_rerec = ui.button("↺ Discard + re-record", on_click=lambda: act(sess.key, "rerecord")).classes("grow")

                pending = {"key": "stop"}
                with ui.dialog() as hands_dialog, ui.card():
                    hands_text = ui.label()
                    with ui.row():
                        ui.button("Yes, the leader arm is held still", on_click=lambda: (hands_dialog.close(), act(sess.key, pending["key"], hands_off=True)))
                        ui.button("Cancel", on_click=hands_dialog.close)

                def leader_key(name: str):
                    if sess.opts.method == "leader":
                        pending["key"] = name
                        hands_text.text = ("The leader arm moves too / goes limp at the end. Is the operator holding the leader arm still?")
                        hands_dialog.open()
                    else:
                        act(sess.key, name)
                with ui.row().classes("w-full no-wrap"):
                    k_reset = ui.button("⟲ Return to start (arm moves)", on_click=lambda: leader_key("reset")).classes("grow")
                    k_stop = ui.button("■ End recording (not an e-stop)", on_click=lambda: leader_key("stop")).classes("grow")
                b_pre = ui.button("Pre-move forward (arm moves slowly)", on_click=lambda: act(sess.premove)).classes("w-full")
                ui.separator()

                with ui.dialog() as held_dialog, ui.card():
                    ui.label("Ctrl-C makes the LEADER arm go limp. Is someone holding the leader arm?")
                    with ui.row():
                        ui.button("Yes, the leader arm is held",
                                  on_click=lambda: (held_dialog.close(), act(sess.stop_teleop, leader_held=True, ok="teleop stopped")))
                        ui.button("Cancel", on_click=held_dialog.close)

                def stop_teleop():
                    if sess.opts.method == "leader" and sess.teleop_running and not sess.recorder_running:
                        held_dialog.open()
                    else:
                        act(sess.stop_teleop, ok="teleop stopped")
                b_stop = ui.button("④ Stop teleop (after the recording ended)", on_click=stop_teleop).classes("w-full")
                ui.label("The spotter keeps the e-stop and the power switch. This page never switches power and is "
                         "not an emergency stop.").classes("text-caption")

            with ui.card().classes("w-full"):
                ui.label("Live status").classes("text-subtitle1 text-weight-bold")
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
                ui.label("Session log").classes("text-subtitle1 text-weight-bold")
                sess_log = ui.log(max_lines=200).classes("w-full h-40")
            with ui.card().classes("w-full"):
                ui.label("Recorder output").classes("text-subtitle1 text-weight-bold")
                rec_log = ui.log(max_lines=300).classes("w-full h-56")
            with ui.card().classes("w-full"):
                ui.label("Teleop output / events").classes("text-subtitle1 text-weight-bold")
                tel_log = ui.log(max_lines=300).classes("w-full h-56")

    seen = {"sess": 0, "rec": 0, "tel": 0, "ev": 0, "eps": None, "rec_obj": None, "tel_obj": None}
    COLORS = {b_teleop: "primary", b_power: "orange", b_rec: "deep-orange", k_next: "positive", k_rerec: "blue-grey",
              k_reset: "orange", k_stop: "red-9", b_pre: "orange", b_stop: "primary"}

    def enable(b, on: bool):
        """Disabled buttons are grey (Goldfish GUI #4), not a paler copy of their colour."""
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

    def refresh_episodes():
        key = (tuple(sorted(sess.notes)), sess.episode, tuple(sorted((k, v["text"]) for k, v in sess.notes.items())))
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
                    ui.label(f"ep {ep}" + (" ●" if ep == sess.episode and sess.recording_now else "")).classes("w-14")
                    ui.checkbox("grasp", value=n["grasp"], on_change=lambda e, ep=ep: sess.set_note(ep, grasp=e.value))
                    ui.checkbox("placed", value=n["placed"], on_change=lambda e, ep=ep: sess.set_note(ep, placed=e.value))
                    ui.input(placeholder="note", value=n["text"], on_change=lambda e, ep=ep: sess.set_note(ep, text=e.value)).classes("grow")
                    ui.label(f"boundary {sess.episode_walls.get(ep, 0.0):.1f} s").classes("text-caption w-24")

    def tick():
        sess.poll()
        ok, why = sess.gng(o.side if not sess.running else sess.opts.side)
        gng.text = ("✅ go/no-go: " if ok else "⛔ go/no-go: ") + why
        tel, rec = sess.teleop_running, sess.recorder_running
        locked = sess.locked()
        foreign = sess.monitor is not None and sess.monitor.status.foreign
        watch.set_visibility(not mine())
        leader_note.set_visibility((o.method if not sess.running else sess.opts.method) == "leader")
        banner.text = ("ANOTHER SENDER DRIVES THIS ARM — E-STOP NOW" if foreign else
                       sess.alarm or ("LOCKED: release, End recording, ④ stop the teleop, start again (R2)" if locked else ""))
        banner.set_visibility(bool(banner.text))
        setup_card.classes(remove="opacity-60")
        for el in setup_card.descendants():
            if hasattr(el, "set_enabled"):
                el.set_enabled(not sess.running and mine())
        running_opts.text = ("running: " + ", ".join(f"{k}={v}" for k, v in vars(sess.opts).items())) if sess.running else ""
        enable(b_teleop, not tel and not rec)
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
        now_step.text = "Now: " + ("① Start teleop" if not tel and not rec else
                                   "④ Stop teleop (then ① for a new recording)" if sess.recorder_used and not rec else
                                   "② Spotter types the name and confirms the power" if not sess.power_confirmed else
                                   "③ Start recording" if not rec else
                                   "recording episode — End / Discard / wait" if sess.recording_now else "▶ Next episode, or ■ End recording")
        if sess.monitor is not None:
            s = sess.monitor.status
            live = tel and sess.monitor.fresh()
            st_badge.text = "LOCKED" if locked else (s.state if live else "EXITED" if not tel else "no status")
            st_badge.props(f"color={'negative' if locked else STATE_COLOR.get(s.state, 'grey-5') if live else 'grey-5'}")
            st_link.text = f"link {s.link}" if live else ""
            st_wall.text = (f"BOUNDARY: {s.wall} — move back" if (s.wall and live) else "") + f"   (at the boundary {s.wall_s:.1f} s this session)"
            face = "—" if s.facing_deg is None else f"{s.facing_deg:+.0f}°" + ("  ⚠ turn to face forward" if abs(s.facing_deg) > 75 else "")
            st_in.text = (f"PICO input {s.input_hz:.0f} Hz" + ("  ⚠ NO DATA: turn sending on in the app" if s.input_hz < 5 else "")
                          + f" · facing {face}") if live else ""
            st_last.text = f"last: {s.last_event[:110]}"
            if seen["ev"] < sess.monitor.n_events:
                n = sess.monitor.n_events - seen["ev"]
                for t, e in list(sess.monitor.events)[-n:]:
                    tel_log.push(f"{t:7.1f}s  {e}")
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
        st_rec.text = (("RECORDING episode " + str(sess.episode) + elapsed) if sess.recording_now else
                       ("recorder waiting" if rec else "recorder off")) + (f" · data {sess.dataset}" if sess.dataset else "")
        if seen["sess"] < sess.n_log:
            n = sess.n_log - seen["sess"]
            for line in list(sess.log)[-n:]:
                sess_log.push(line)
            seen["sess"] = sess.n_log
        pull(rec_log, sess.recorder, "rec")
        refresh_episodes()

    ui.timer(0.5, tick)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this PC only (default)")
    ap.add_argument("--port", type=int, default=8090)
    a = ap.parse_args(argv)
    sess = CollectionSession(facing=headset_facing_deg)

    def on_shutdown():
        for m in sess.shutdown():
            print(f"[gui] {m}", flush=True)
    ng_app.on_shutdown(on_shutdown)

    @ui.page("/")
    def index():
        build(sess)

    ui.run(host=a.host, port=a.port, title="OpenArm collection", reload=False, show=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
