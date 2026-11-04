#!/usr/bin/env bash
# PICO で両腕（2026-11-04・作業者: Sun (Suraj)）: PICO → フォロワーの両腕収集と、PICO → リーダーの両腕を 1 つの入口で。
#
#   bash scripts/pico_bi.sh setup                 # 1 回だけ: 両腕用の lerobot と openarm-orboh の別フォルダを作る／確かめる
#   POSE_SET=<set> bash scripts/pico_bi.sh sender [position|wrist] [tag]    # 端末 A: PICO の送り手（両腕・60 Hz）
#   POSE_SET=<set> OPERATOR=<名前> bash scripts/pico_bi.sh follower [dry|gate|session] [本数]   # 端末 B: フォロワー両腕を録る
#   POSE_SET=<set> bash scripts/pico_bi.sh leader                            # 端末 B: リーダー両腕（受け手 2 本・esc 1 回で両方終わる）
#
# 両腕のフォロワーは openarm-orboh main ではまだ録れない（REMOTE=true を止める）。マージまでは別フォルダで動かす:
#   ~/projects/lerobot-wt-biremote      = この PC の lerobot ＋ Kento の bi_openarm_leader_remote（44b260d6）
#   ~/projects/openarm-orboh-wt-biremote = branch suraj/feat-bimanual-remote-teleop（REMOTE=true の両腕）
# どちらも共有の checkout（~/projects/lerobot・~/projects/openarm-orboh）のブランチは変えない。
# 送り手を先に、受け手（follower / leader）を 30 秒以内に。受け手だけ止めたら送り手も起こし直す（R2）。
# 実機を動かすのは、見張りが非常停止の横にいて、点検表（docs/hardware-runbook.md）の今日の列がそろっているときだけ。
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ORBOH_MAIN="$HOME/projects/openarm-orboh"
LR_MAIN="$HOME/projects/lerobot"
LR_WT="${PICO_BI_LEROBOT:-$HOME/projects/lerobot-wt-biremote}"
OR_WT="${PICO_BI_ORBOH:-$HOME/projects/openarm-orboh-wt-biremote}"
KENTO_COMMIT=44b260d6                 # teleoperators: add bi_openarm_leader_remote（Kento・2026-11-01）
OR_BRANCH=suraj/feat-bimanual-remote-teleop
ENV_FILE_DEFAULT=config/bimanual-collect.exp_pipe.env
CMD="${1:-}"; shift || true

need_pose_set() {
  : "${POSE_SET:?POSE_SET を指定すること（送り手・受け手・録り手で同じ開始姿勢セット）}"
  [ -d "$ORBOH_MAIN/config/pose_sets/$POSE_SET" ] || { echo "[pico-bi] 開始姿勢セットが無い: $POSE_SET" >&2; exit 2; }
}

case "$CMD" in
  setup)
    # lerobot: 今の checkout の HEAD ＋ Kento のコミット（無ければ作る。あれば中身を確かめるだけ）
    if [ ! -d "$LR_WT" ]; then
      git -C "$LR_MAIN" fetch -q origin
      base="$(git -C "$LR_MAIN" rev-parse HEAD)"
      git -C "$LR_MAIN" worktree add -q -b "suraj/biremote-on-$(git -C "$LR_MAIN" rev-parse --short HEAD)" "$LR_WT" "$base"
      git -C "$LR_WT" -c user.name="Suraj" -c user.email="surajpaliwal.developer@gmail.com" cherry-pick "$KENTO_COMMIT"
    fi
    [ -d "$LR_WT/src/lerobot/teleoperators/bi_openarm_leader_remote" ] || { echo "[pico-bi] $LR_WT に bi_openarm_leader_remote が無い" >&2; exit 2; }
    # openarm-orboh: REMOTE=true の両腕のブランチ（無ければ作る。あれば origin の先へ進める）
    git -C "$ORBOH_MAIN" fetch -q origin
    if [ ! -d "$OR_WT" ]; then
      git -C "$ORBOH_MAIN" worktree add -q "$OR_WT" "$OR_BRANCH"
    else
      git -C "$OR_WT" merge -q --ff-only "origin/$OR_BRANCH" || echo "[pico-bi] 注意: $OR_WT を origin/$OR_BRANCH へ進められない（手元の変更？）" >&2
    fi
    # 未追跡の設定（開始姿勢セット・タスクファイル・シーン）を main の checkout から写す（無いものだけ・上書きしない）
    for rel in config/pose_sets/kitakyushu-wood-pico-v1 "$ENV_FILE_DEFAULT" scenes/kitakyushu-exp-pipe-v1.yaml ${PICO_BI_COPY:-}; do
      if [ -e "$ORBOH_MAIN/$rel" ] && [ ! -e "$OR_WT/$rel" ]; then cp -rp "$ORBOH_MAIN/$rel" "$OR_WT/$rel"; echo "[pico-bi] 写した: $rel"; fi
    done
    echo "[pico-bi] lerobot:      $LR_WT ($(git -C "$LR_WT" log --oneline -1))"
    echo "[pico-bi] openarm-orboh: $OR_WT ($(git -C "$OR_WT" log --oneline -1))"
    PYTHONPATH="$LR_WT/src" "$LR_MAIN/.venv/bin/python" -c "import lerobot.teleoperators.bi_openarm_leader_remote" \
      && echo "[pico-bi] OK: 両腕の受け口を読める"
    ;;
  sender)
    need_pose_set
    MODE="${1:-wrist}"; TAG="${2:-pico-bi-$MODE-$(date +%Y%m%d-%H%M%S)}"
    case "$MODE" in position) ORI=();; wrist) ORI=(--orientation);; *) echo "mode: position か wrist" >&2; exit 2;; esac
    cd "$REPO" && exec bash scripts/pico_env.sh python -m pico_teleop.sender --host 127.0.0.1 --arms both "${ORI[@]}" \
      --hz 60 --max-joint-acc-dps2 300 --pose-set "$POSE_SET" --confirm-follower-at-start-pose --max-link-pause-s 30 \
      --log "runs/$TAG-sender.jsonl" 2> >(tee -a "runs/$TAG-sender.out" >&2) | tee -a "runs/$TAG-sender.out"
    ;;
  follower)
    need_pose_set
    : "${OPERATOR:?OPERATOR を指定すること（PICO をかぶる人・英字）}"
    STAGE="${1:-dry}"; N="${2:-10}"
    case "$STAGE" in dry|gate|session) ;; *) echo "stage: dry / gate / session" >&2; exit 2;; esac
    [ -d "$OR_WT" ] && [ -d "$LR_WT" ] || { echo "[pico-bi] 先に: bash scripts/pico_bi.sh setup" >&2; exit 2; }
    [ -d "$OR_WT/config/pose_sets/$POSE_SET" ] || cp -rp "$ORBOH_MAIN/config/pose_sets/$POSE_SET" "$OR_WT/config/pose_sets/"
    [ "$STAGE" = dry ] || echo "[pico-bi] 注意: $STAGE はシーンつきで録るので、開始姿勢セット $POSE_SET が最初の本番で凍結される" >&2
    # 保存を速く: 動画を録画しながら書き出す（STREAMING_ENCODING=false で従来どおり・openarm-orboh b690e87）
    cd "$OR_WT" && exec env REMOTE=true START_POSE_HOLD=false BILATERAL_FEEDBACK=false POSE_SET="$POSE_SET" \
      STREAMING_ENCODING="${STREAMING_ENCODING:-true}" ENCODER_THREADS="${ENCODER_THREADS:-2}" \
      ENV_FILE="${ENV_FILE:-$ENV_FILE_DEFAULT}" OPERATOR="$OPERATOR" PYTHONPATH="$LR_WT/src${PYTHONPATH:+:$PYTHONPATH}" \
      bash scripts/collect_bimanual.sh "$STAGE" "$N"
    ;;
  leader)
    need_pose_set
    cd "$REPO" && exec python3 scripts/pico_bi_leader.py
    ;;
  *)
    sed -n 2,15p "$0"; exit 2
    ;;
esac
