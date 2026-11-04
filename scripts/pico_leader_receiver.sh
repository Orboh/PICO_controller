#!/usr/bin/env bash
# PICO → LEADER arm（2026-11-04・作業者: Sun (Suraj)）: PICO の指令でリーダー腕を動かす受け手。1 本の腕だけ。
# 中身は openarm-orboh の run_teleop_remote_<side>.sh と同じ lerobot-teleoperate で、--robot.port だけを
# リーダーの CAN（can_lead_*）にする。リーダーとフォロワーはモーター表と較正の形式が同じ（DM8009/4340/4310・
# 同じ JSON）なので、フォロワーの位置制御（kp 240…・保持・途絶で最終姿勢ホールド）でリーダーが姿勢を保つ。
# lerobot のコードは変えない。較正はリーダーの較正の写し（robots/openarm_follower/leader_<side>_pico.json）。
#
# 使い方:
#   POSE_SET=kitakyushu-takeei-v5-pico-v1 bash scripts/pico_leader_receiver.sh left
#   （別の端末で）POSE_SET=kitakyushu-takeei-v5-pico-v1 bash scripts/arm_session.sh left position
# 腕は起動時に自分で開始姿勢へ動く（経由地つき・数秒）。esc で経由地を逆に戻って脱力する（R4）。
# 止める: 握りを離して（HOLD）から esc。Ctrl-C は脱力ではなく即停止なので、腕を支えてから。
#
# 止める条件（ここで止めて動かさない）:
#   - leader_agent か、同じ CAN を使う lerobot / hold_pose が走っている（同じモーターを 2 つが指令する）
#   - POSE_SET が無い（送り手と受け手の開始姿勢を必ず同じ名前でそろえる）
#   - リーダーの CAN が UP でない
set -euo pipefail
SIDE="${1:?usage: POSE_SET=<set> pico_leader_receiver.sh left|right}"
case "$SIDE" in
  left)  PORT=can_lead_l; ZMQ=5702; ECHO=5712 ;;
  right) PORT=can_lead_r; ZMQ=5701; ECHO=5711 ;;
  *) echo "side must be left or right" >&2; exit 1 ;;
esac
: "${POSE_SET:?POSE_SET を指定すること（送り手 arm_session.sh にも同じ名前を渡す）}"

ORBOH="$HOME/projects/openarm-orboh"
BIN="$HOME/projects/lerobot/.venv/bin/lerobot-teleoperate"
CAL_DIR="$HOME/.cache/huggingface/lerobot/calibration"
CAL_ID="leader_${SIDE}_pico"
CAL_SRC="$CAL_DIR/teleoperators/openarm_leader/leader_${SIDE}.json"
CAL_DST="$CAL_DIR/robots/openarm_follower/${CAL_ID}.json"

# 1) 同じ腕を指令するものが走っていないか
#    leader_agent はどちらの腕でも止める（リーダーを触る別の流れと混ぜない）
if pgrep -f "[l]erobot\.teleoperators\.openarm_leader_remote\.leader_agent|[r]un_leader_agent_(left|right)\.sh" >/dev/null; then
  echo "[pico-leader] leader_agent が走っている。先に止める（腕を支えてから Ctrl-C）:" >&2
  pgrep -af "[l]erobot\.teleoperators\.openarm_leader_remote\.leader_agent|[r]un_leader_agent_(left|right)\.sh" >&2; exit 2
fi
if pgrep -f "[p]ort[= ]$PORT( |$)" >/dev/null; then
  echo "[pico-leader] $PORT を使うプログラムが走っている:" >&2; pgrep -af "[p]ort[= ]$PORT( |$)" >&2; exit 2
fi
# 2) CAN
ip -br link show "$PORT" 2>/dev/null | grep -q UP || { echo "[pico-leader] $PORT が UP でない（sudo bash $ORBOH/scripts/canup.sh）" >&2; exit 2; }
# 3) 開始姿勢（openarm-orboh と同じ読み方）
source "$ORBOH/scripts/lib/pose_set.sh"
pose_set_load current || exit 1
[ "$SIDE" = left ] && POSE="$LPOSE" || POSE="$RPOSE"
[ -f "$POSE" ] || { echo "[pico-leader] $POSE_SET に $SIDE の姿勢ファイルが無い" >&2; exit 2; }
# 4) 較正の写し（無ければリーダーの較正から作る。あれば中身が同じかを見る・上書きしない）
[ -f "$CAL_SRC" ] || { echo "[pico-leader] リーダーの較正が無い: $CAL_SRC" >&2; exit 2; }
if [ ! -f "$CAL_DST" ]; then
  cp "$CAL_SRC" "$CAL_DST"; echo "[pico-leader] 較正を写した: $CAL_SRC -> $CAL_DST"
elif ! cmp -s "$CAL_SRC" "$CAL_DST"; then
  echo "[pico-leader] $CAL_DST がリーダーの今の較正と違う（較正をやり直した?）。確かめてから消して作り直す" >&2; exit 2
fi

echo "[pico-leader] PICO → LEADER $SIDE: $PORT・ポート $ZMQ/$ECHO・開始姿勢 $POSE_SET（$POSE）"
echo "[pico-leader] リーダー腕が自分で開始姿勢へ動く。腕と机から手を引く"
[ "${DRY_RUN:-0}" = 1 ] && { echo "[pico-leader] DRY_RUN=1: 動かさずに打つものだけ出す"; BIN="echo $BIN"; }
exec $BIN \
  --robot.type=openarm_follower --robot.port="$PORT" --robot.side="$SIDE" --robot.id="$CAL_ID" \
  --robot.initial_pose_path="$POSE" \
  --teleop.type=openarm_leader_remote --teleop.id="leader_${SIDE}_remote" \
  --teleop.zmq_port="$ZMQ" --teleop.echo_port="$ECHO" \
  --fps=200
