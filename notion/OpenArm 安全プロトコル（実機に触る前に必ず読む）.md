<!--
Notion から取得した本文をそのまま保存したもの（Notion-flavored Markdown。要約・編集なし）。
- 元ページ: 🛑 OpenArm 安全プロトコル（実機に触る前に必ず読む）
- URL: https://app.notion.com/p/3fc194a1c56281a78de0d118bb2fb910
- 場所: Kota0 / Model & Control System Development Hub - モデル・制御システム開発ハブ / 📚 モデル開発の文書 / 安全の決まり（実機に触る前に必ず読む）
- 親ページは「アーカイブ（2026-10-30・更新しない）」の凍結版。正本: https://github.com/Orboh/doma-sorting-dev/blob/main/08-archive/openarm-vla-2610/openarm-safety-protocol.md （元は Kento の Vault）。
  ブランチ名・開始姿勢セットなどの事実は古くなりうるので、使う前に openarm-orboh の README で今の状態を確かめる。
- Notion の最終編集: 2026-10-30T14:06:37Z ／ 取得: 2026-10-31（Kota の PC の Claude Code・操作は Kento）
-->

# OpenArm 収集・自律実行 安全プロトコル

> <b>移した元:</b> orboh-company-vault（Kento の作業場）の文書（2026-10-16 に移した。以後はこのファイルが正本で、変更は PR で行う）。移すときに、接続先・鍵・AWS の ID・顧客との共有物・チーム外の個人名を除いた。

> <b>この文書について:</b> OpenArm での teleop 収集（Part A）と学習済みポリシーの自律実行（rollout・Part B評価）の物理安全を定める。既存文書に散在していた停止手段・torque残留・monitor gotcha を集約し、<b>未整備項目（HW E-stop 実在確認・rollout 停止手段）を TODO として明示</b>する。外部人員（他のメンバー）向けの最小 SOP は本書 §4 が正本で、収集マニュアル（顧客と共有している Vault にある。パスは除いた）§7 に転記して運用する。データの保全は <mention url="https://app.notion.com/p/3fc194a1c56281e0832cf0c60c0928bc" />、機密は FDG（別軸）。G1 の安全は G1 の設計書の統合安全 HMI 管理の章（F-11。顧客と共有している Vault にある。パスは除いた）が正本で、本書は OpenArm 側の対応物。

---

## 0. 現状の実態（文書化済み事実の集約）

<table fit-page-width="true" header-row="true">
<tr>
<td>
項目
</td>
<td>
実態
</td>
</tr>
<tr>
<td>
確実な脱力手段
</td>
<td>
<b>power cycle（電源OFF→10秒→ON）が唯一確実</b>。Damiao の error latch（個別 disable がスタックする状態）は disable ではソフトから戻せないことを実機で確認済み。<b>2026-09-21 追記: ROTOR\_OVERTEMP のラッチはクリアコマンド（data FF×7+FB・</b><b><code>scripts/damiao_errcheck.py --clear</code></b><b>）で電源再投入なしに解除できた（右 follower joint\_7・左 follower gripper で実証）。種類の特定は同スクリプト（受信のみ・teleop 中も安全）</b>
</td>
</tr>
<tr>
<td>
teleop / record の停止
</td>
<td>
Ctrl-C。bimanual ラッパ（現行 <code>launch_bimanual.sh</code>・旧名 <code>16_launch_bimanual.sh</code>）は Ctrl-C 1回で両腕停止＋全 bus disable まで自動
</td>
</tr>
<tr>
<td>
異常終了（segfault等）
</td>
<td>
<b>cleanup が走らず torque ON が残留</b>（過熱リスク）。terminal が死んでも腕は通電したまま＝放置禁止。恒久対処（trap＋retry で disable 確実化）は「別PR候補」のまま<b>未実装</b>
</td>
</tr>
<tr>
<td>
<code>openarm-can-cli monitor</code>
</td>
<td>
<b>モータを ENABLE する（トルクON）</b>。読み取り専用ではない（誤解の訂正履歴あり）。read-only は <code>show_param</code>
</td>
</tr>
<tr>
<td>
重力補償 teleop
</td>
<td>
disconnect（Ctrl-C 経由含む）で必ず torque disable される設計（実装済み安全機構）。leader は torque ON で動く運用（scale 1.2）
</td>
</tr>
<tr>
<td>
<code>max_relative_target</code> クランプ
</td>
<td>
lerobot <code>send_action</code> が各ステップ「現在角±max\_rel」にクランプ（IK 経路 3 / rollout 1.0）。<b>MIT 位置制御ではこれが「移動量制限」でなく「復元トルク上限 = kp × max\_rel」として効く</b>（2026-06-23 に「駆動トルク∝kp×max\_rel」として認識・<b>2026-09-20 に右腕実機で顕在化</b>: clamp 1.0 × 既定 kp では手首 0.44 Nm・肩 4.2 Nm で、開始姿勢の重力負荷 約 1.2 / 6.5 Nm に負け、「現在位置を保持せよ」の指令で joint\_7 が 74°→1° まで垂れた）。<b>hold（最後の指令の再送）は保持にならない</b>。対策と数値は <b>2026-09-20-openarm-jev-stage-a-real-arm</b>（Orboh 社内の文書。必要ならメンバーに聞く） §4-5（関節別 dict・追従誤差ガード・意図姿勢の保持）。同じ経路の GR00T rollout にも潜在（方策が常に現在から離れた位置を出すため顕在化しにくい・未点検）
</td>
</tr>
<tr>
<td>
HW E-stop
</td>
<td>
<b>実在未確認</b>。「E-stopが押し込まれていないか」という診断チェックリスト項目と「e-stop前提」という記述はあるが、物理ボタンの実在・位置を確認した記録が無い（→TODO#1）
</td>
</tr>
<tr>
<td>
rollout（自律実行）の停止
</td>
<td>
<b>GR00T </b><b><code>eval_openarm.py</code></b><b> は検証済み（2026-07-22・下の「実機検証記録」参照）／他経路は未定義</b>（→TODO#2）。<b>経路は複数</b>（2026-07-12 更新）: ①ACT/SmolVLA 用の実行ループは<b>まだ存在せず新規実装</b>（lerobot-rollout 相当をゼロから書く・GR00T pipeline 設計 §6.1 調査）②GR00T G4 は新設ブリッジ <code>eval_openarm.py</code>（<code>eval_so100.py</code> 3層構成の OpenArmAdapter 置換・PolicyServer/Client 分離）。ハブ P1-7「即停止可」は未着手タスクの合否基準にすぎない
</td>
</tr>
<tr>
<td>
右系統CAN
</td>
<td>
bus-off 再発リスク残存（2026-06-16 再嵌合で暫定復旧・恒久対策なし）。収集中の片系統停止はエピソード破損・危険動作につながる
</td>
</tr>
</table>

> 根拠: 顧客と共有している Vault の OpenArm テレオペ setup guide（C-2〜C-4・power cycle・monitor訂正）、同 2026-06-03 の teleop setup 記録（Kota#7 error latch 実証）（いずれもパスは除いた）、<b>lerobot-gravity-comp-port</b>（disconnect disable・torque残留・TMAX）、<b>2026-06-23-openarm-ik-reach-realarm</b> §3-4（max\_rel）、<b>2026-06-12-openarm-teleop-instability-handoff</b>（右CAN）、別案件の ACT 計画 §9（rollout・停止手段の記載なし。ノート名は除いた）

---

### 実機検証記録: GR00T ブリッジ <code>eval_openarm.py</code> の停止挙動（2026-07-22・TODO#2 の該当分をクローズ）

<b>対象</b>: Thor＋OpenArm <b>右腕</b>・<code>eval_openarm.py</code>（<code>~/projects/openarm-orboh/scripts/g4/</code>）・<code>--dry_run=true</code>（指令は送らず、接続に伴う通電と自動整列のみが実機イベント）。

<b>3経路すべてで </b><b><code>torque disable</code></b><b> 到達を確認した。</b>

<table fit-page-width="true" header-row="true">
<tr>
<td>
経路
</td>
<td>
契機
</td>
<td>
結果
</td>
</tr>
<tr>
<td>
<b>Ctrl-C（最悪タイミング）</b>
</td>
<td>
<b><code>soft_move_to_position()</code></b><b> で腕が動いている最中</b>に SIGINT
</td>
<td>
<b>約1秒で </b><b><code>torque disable 成功</code></b><b> → </b><b><code>disconnect</code></b><b> 到達</b>。ループは1周も回らず <code>finally</code> へ直行
</td>
</tr>
<tr>
<td>
例外
</td>
<td>
ブリッジ側のバグで実行中に例外
</td>
<td>
即座に停止処理 → 同上
</td>
</tr>
<tr>
<td>
正常終了
</td>
<td>
<code>max_steps</code> 到達
</td>
<td>
同上
</td>
</tr>
</table>

<b>Ctrl-C の実測タイムライン</b>

```javascript
11:51:45  Soft-aligning 8 motors to initial pose over 2.2s...
11:51:47  ^C  → シグナル 2 受信 → 停止処理に入る（torque disable まで待つ）
11:51:48  torque disable 成功 → robot.disconnect() を実行する
11:51:49  後片付け完了
```

<b>この経路が守られている意味</b>: <code>connect()</code> は内部で <code>enable_torque()</code> → <code>soft_move_to_position()</code> を行う＝<b>接続処理そのものが腕を動かす</b>。ここが <code>try/finally</code> の外にあると、<b>整列中の Ctrl-C で torque ON のまま抜ける</b>。2026-07-21 の安全レビュー指摘（A-1）で <code>connect()</code> を保護内へ移し、signal handler を <code>connect()</code> より前に設置した結果、この最悪タイミングでも脱力に到達した。

<b>挙動の性質（把握しておくべき点）</b>

- <b>1回目の Ctrl-C は動作中の処理を即座に中断しない</b>（PEP 475 によりフラグを立てるだけ）。整列や <code>input()</code> は完了してから停止処理に入る。<b>反応が数秒遅れても連打しないこと</b>——2回目の Ctrl-C は後片付け自体を中断する設計（固まった場合の逃げ道）なので、押すと逆に torque が残りうる
- 推論の応答待ちに入っていると<b>最大 15 秒程度</b>遅延しうる（<code>PolicyClient</code> のタイムアウト）
- <b>ソフト disable は保証ではない</b>。Damiao の error latch 時は効かないため、<code>disable_torque()</code> の成否を実装側で握り、失敗時は power cycle を警告する（レビュー指摘 B-3。<code>bus.disconnect()</code> 任せだと失敗が握りつぶされる）

<b>残る未検証</b>: SIGKILL / OOM / カーネルフリーズでは signal handler 自体が動かない。<b>この経路はソフト層では原理的にカバーできず、HW E-stop（TODO#1）が唯一の解</b>。

> 根拠: 2026-07-22 実走ログ（全文は Dev-Log <b>vla-track-devlog</b> 2026-07-22）。実行 runbook は <b>gr00t-openarm-pipeline-design</b> §6.6。

---

## 1. 停止手段の階層（上から順に試す・下ほど確実）

<table fit-page-width="true" header-row="true">
<tr>
<td>
レベル
</td>
<td>
手段
</td>
<td>
効果
</td>
<td>
使いどころ
</td>
</tr>
<tr>
<td>
L0 予防
</td>
<td>
クランプ・低速設定（max\_rel / 低fps / 短chunk）
</td>
<td>
暴走の振れ幅を事前に制限。<b>注意: max\_rel は MIT 位置制御では復元トルク上限（kp × max\_rel）を兼ねる。小さすぎると重力に負けて hold が垂れる（2026-09-20・§0）。関節別 dict で「重力負荷 × 1.2 以上」を確保し、実行ループ側に追従誤差ガード（\|実測 − 指令\| > 8° で停止）を持たせる</b>
</td>
<td>
実行前の設定
</td>
</tr>
<tr>
<td>
L1 通常停止
</td>
<td>
Ctrl-C（teleop/record/rollout）
</td>
<td>
プロセス停止。重力補償 teleop は disable まで自動。<b>rollout は disable 保証未検証（TODO#2）</b>
</td>
<td>
通常終了・異常の初動
</td>
</tr>
<tr>
<td>
L1.5 ソフト E-stop
</td>
<td>
別端末から <code>touch /tmp/openarm_estop</code>（実行ループが毎 tick 監視 → hold → disable → disconnect）
</td>
<td>
<b>2026-09-20 に </b><b><code>scripts/jev/jev_arm.py</code></b><b> で右腕実機検証（touch から 1 秒以内に torque disable）。</b><b><code>eval_openarm.py</code></b><b> は未移植（TODO#3 残）</b>
</td>
<td>
プログラム（監視系）と人の共通の停止入口
</td>
</tr>
<tr>
<td>
L2 ソフト脱力
</td>
<td>
全ch disable（<code>for ch in can0..3; openarm-can-cli disable</code>・<code>~/thor_disable_check.py</code>）
</td>
<td>
torque OFF（error latch 時は効かないことがある）
</td>
<td>
Ctrl-C 後の確認・異常終了後
</td>
</tr>
<tr>
<td>
L3 物理遮断
</td>
<td>
<b>power cycle（各アームの DC PSU を OFF→10秒→ON）</b>
</td>
<td>
唯一確実な脱力
</td>
<td>
L2 で脱力しない時・緊急時
</td>
</tr>
</table>

<b>運用原則: 実行者は L3（電源スイッチ）に手が届く位置取りで作業する。</b> L3 の位置（どの PSU がどの腕か）を作業開始前に指差し確認する。

<b>自動監視系の受け口（2026-07-12 追加）</b>: L0〜L3 は全て人手操作。<b>プログラム（Foresight F5 のオンライン警報等）が叩ける停止入口は soft E-stop（TODO#3・</b><b><code>/tmp/openarm_estop</code></b><b> 監視）が唯一の設計</b>で、導入後は L1 と L2 の間（L1.5）に位置づける。よって <b>Foresight F5（警報→停止階層へ接続）を実施する場合、TODO#3 は「推奨」から必須に格上げ</b>される。導入までの自動系警報は「人間に通知→人手 L1」のみ＝チャンク境界レイテンシでの自動停止はできない。

---

## 2. teleop 収集（Part A）の安全 SOP

<b>起動前</b>

1. <s>leader / follower を似た姿勢に合わせる</s> <b>（2026-07-12 更新）</b> 07-08 の起動時整列（align\_on\_connect・fork commit <code>77809ba</code>）導入で手合わせは不要になった。代わりに新注意2点: ①<b>接続時に腕が約2.2秒ひとりでに動く</b>——可動域を確保し、動き終わるまで手を出さない ②起動時は腕を<b>真っ直ぐ下</b>に垂らしておく（零位焼き込みへの影響防止）。リミット越え・破損の先行事例の教訓（可動域確保）は不変
2. follower の可動域に人・PC・ケーブル・カメラマウントが無いことを確認
3. 電源（PSU）に手が届く位置取りを確認（§1 運用原則）
4. <code>monitor</code> は使わない（トルクONになる）。状態確認は <code>show_param</code> か <code>can_check.sh</code>

<b>実行中</b>

- 異音・異常追従・振動の急変があれば即 Ctrl-C → L2 → 必要なら L3
- 右系統の bus-off が起きたら収集を中断（片腕だけの継続はしない）→ 再嵌合・Kento に報告

<b>終了後（毎回・異常終了時は特に）</b>

1. 全ch disable を確認（L2）。<b>クラッシュ終了した場合はモータ通電が残っている前提で扱う</b>
2. 脱力しないモータがあれば power cycle（L3）
3. 腕を安全姿勢（垂らす）で置く

---

## 3. 自律実行（rollout・Part B 評価）の安全 SOP

自律実行は teleop と危険度が違う: 人が leader を握っておらず、<b>開ループ chunk 実行中はポリシーが盲目で走る</b>。盲目窓は経路依存（2026-07-12 更新・07-08 実行系確定を反映）: <b>ACT/SmolVLA = chunk50 全消化 @30Hz ≈ 壁時計1.67秒</b>・<b>GR00T = chunk16 既定 @30Hz ≈ 0.53秒</b>。しかも評価は held-out 位置＝学習分布の外縁で、ポリシーが最も暴れやすい入力を意図的に与える行為である。

<b>前提ゲート（これを満たすまで本評価に入らない・実行系ごとに1回）</b>

> <b>（2026-07-12 一般化）</b> ゲートの対象は「G4/Part B で<b>実際に使う実行系</b>」である。旧記述は <code>lerobot-rollout</code> 固定だったが、①ACT/SmolVLA の実行ループは<b>未実装（新規に書く）</b>②GR00T G4 は新設 <code>eval_openarm.py</code>（PolicyServer/Client 分離）と経路が複数になった。<b>別の実行系で検証を済ませても、使う経路の保証にはならない。</b>

1. <b>停止挙動の検証（TODO#2）</b>: 使う実行系の Ctrl-C→disable 挙動（<code>openarm_follower</code> の disconnect が確実に走るか）を実機で1回検証し、結果を本書 §0 に追記する
2. <b>soft E-stop の導入（推奨・Foresight F5 実施時は必須＝§1）</b>: G1 の FileEStop（<code>touch /tmp/<停止ファイル></code> で停止・<code>rm</code> で再開・<code>harvest/safety.py</code>。ファイル名は別案件の名前を含むので除いた）を OpenArm 側に移植（<code>/tmp/openarm_estop</code> 監視）。導入までは「Ctrl-C＋電源即断できる人員配置」で代替
3. <b>出力クランプの確認</b>: 使う実行系に max\_relative\_target 相当のクランプが効いているかを確認し、<b>無ければ導入してから実機に入れる</b>（IK 経路にはあるが自律実行経路は全て未確認。特に GR00T は absolute 位置学習＝chunk 先頭が現在角から大きく跳ぶ指令になり得るため、クランプ無しの生 chunk 送出は不可）
4. <b>分離構成（Server/Client）固有の失敗モード対策（2026-07-12 追加・GR00T 案A）</b>: PolicyServer 死亡・ネットワーク断でクライアントが応答待ちブロック→<b>腕が通電保持のまま固まる</b>（PolicyClient は recv タイムアウト未設定をコード実読で確認済み）。ブリッジ実装に①recv タイムアウト＋例外時 disable ②Ctrl-C を打つ正はクライアント（制御ループ）側——サーバを止めても腕は disable されない、を明記・実装すること

<b>実行ルール</b>

- 初回・新チェックポイントの1本目は<b>必ず Kento 同席</b>。低速・短 chunk（<code>n_action_steps</code> 短縮）で1試行→挙動を見てから通常設定へ
- <b>外部人員（他のメンバー）単独での自律実行は、前提ゲート1〜3が済むまで禁止</b>（現行マニュアル §6 は「その外部人員 (or Kento) が実行」とするが、本書が優先する）
- 実行者以外はアーム可動域外。実行者は電源に手が届く位置
- ロールアウト間のリセット（対象物の差し替え・シーン復元。タスクを問わない）は<b>アーム disable 状態で行う</b>（enable のまま作業空間に手を入れない）

---

## 4. 外部人員（他のメンバー）向け最小 SOP（マニュアル §7 転記用の正本）

1. <b>止める</b>: 異常があれば Ctrl-C。止まらない・脱力しない時は<b>アームの電源スイッチを切る</b>（OFF→10秒→ON で復帰）。迷ったら電源を切ってよい（壊れない・較正は消えない）
2. <b>クラッシュしたら</b>: 画面が死んでもモータは通電していることがある（触ると熱い・危険）。Kento に連絡し、指示どおり disable → 必要なら電源 OFF
3. <b><code>monitor</code></b><b> コマンドは使わない</b>（アームに力が入る）。確認系は手順書にあるコマンドだけを使う
4. <b>収集中の立ち位置</b>: アームの可動範囲に体・ケーブル・私物を入れない。リセット作業（対象物の差し替え）は録画の合間（アームが止まっている時）だけ
5. <b>自律実行（Part B）は単独でやらない</b>（Kento 同席・整備完了まで）

---

## 5. TODO（優先順・担当 Kento）

<table fit-page-width="true" header-row="true">
<tr>
<td>
#
</td>
<td>
内容
</td>
<td>
目安
</td>
</tr>
<tr>
<td>
1
</td>
<td>
<b>HW E-stop の実在確認</b>＋電源（PSU）の手元配置・ラベリング（どのスイッチがどの腕か）
</td>
<td>
次回実機・5分
</td>
</tr>
<tr>
<td>
2
</td>
<td>
<b>自律実行の停止挙動検証</b>（使う実行系ごとに Ctrl-C→disable を確認・2026-07-12 一般化: ACT/SmolVLA 新規実行ループ／GR00T <code>eval_openarm.py</code> の両方が対象）
</td>
<td>
<b>✅ GR00T </b><b><code>eval_openarm.py</code></b><b> は検証済み（2026-07-22・下記 §0 参照）／⬜ ACT/SmolVLA 実行ループは未実装のため未検証</b>
</td>
</tr>
<tr>
<td>
3
</td>
<td>
soft E-stop 移植（G1 <code>harvest/safety.py</code> FileEStop → <code>/tmp/openarm_estop</code>）。<b>Foresight F5 の自動警報の接続先を兼ねる</b>（F5 実施時は必須・§1）
</td>
<td>
<b>✅ </b><b><code>scripts/jev/jev_arm.py</code></b><b> 内で実装・2026-09-20 右腕実機検証（1 秒以内に disable）／⬜ </b><b><code>eval_openarm.py</code></b><b> への移植は未</b>
</td>
</tr>
<tr>
<td>
4
</td>
<td>
record/teleop 異常終了時の自動 disable（trap＋retry・かねてからの「別PR候補」を実装）。<b>fork 側（</b><b><code>Orboh/lerobot</code></b><b> feat/gravity-comp・07-08 も更新あり）で既に手当てされた可能性を Vault からは否定できない＝repo 実物を確認してから着手</b>
</td>
<td>
任意（当面は §2 終了後手順で代替）
</td>
</tr>
<tr>
<td>
5
</td>
<td>
収集マニュアル §7 への §4 転記
</td>
<td>
✅ 済（2026-07-06）
</td>
</tr>
<tr>
<td>
6
</td>
<td>
<b>GR00T ブリッジ（</b><b><code>eval_openarm.py</code></b><b>）実装時の安全要件の組み込み</b>（§3 ゲート3=クランプ・ゲート4=recvタイムアウト＋例外時disable・2026-07-12 追加）
</td>
<td>
<b>✅ 済（2026-07-21 実装＋レビュー是正・2026-07-22 実機検証）</b>
</td>
</tr>
<tr>
<td>
7
</td>
<td>
<b><code>eval_openarm.py</code></b><b> 経路のクランプ＝トルク上限の点検</b>（2026-09-20 新設・§0）: rollout 値 1.0 × 既定 kp で手首・肩が重力に負ける条件は GR00T 経路でも同じ。chunk 境界や hold 時に垂れていないかを rollout ログ（present vs cmd）で点検し、必要なら関節別 dict ＋追従誤差ガードを移植
</td>
<td>
次回 rollout 前
</td>
</tr>
</table>

---

## 6. G1 への展開差分

- G1 は G1 設計書の統合安全 HMI 管理の章（F-11。パスは除いた）が正本: SafetyMonitor（グラフノード先頭の安全ゲート）＋ FileEStop（実装済）＋ TorqueCheck（既定off）等。OpenArm 側は本書がその軽量版に相当する。
- 逆輸入候補: OpenArm で soft E-stop（TODO#3）を導入したら、G1 の FileEStop と操作性（ファイルパス・再開手順）を揃えると教育コストが下がる。
- G1 側の未実装（人検知・BladeGuard 等）は本書のスコープ外（G1 設計書の課題）。

---

## 根拠ソース一覧

<table fit-page-width="true" header-row="true">
<tr>
<td>
ソース
</td>
<td>
パス
</td>
</tr>
<tr>
<td>
power cycle・Ctrl-C・終了後 disable・monitor 訂正
</td>
<td>
顧客と共有している Vault の OpenArm テレオペ setup guide（C-2〜C-4。パスは除いた）
</td>
</tr>
<tr>
<td>
Damiao error latch の実証（Kota#7）・急追従・較正リスク
</td>
<td>
顧客と共有している Vault の 2026-06-03 teleop setup 記録（パスは除いた）
</td>
</tr>
<tr>
<td>
torque残留・disconnect disable・TMAX・重力補償運用
</td>
<td>
<b>lerobot-gravity-comp-port</b>（Orboh 社内の文書。必要ならメンバーに聞く）・<b>2026-06-16-lerobot-gravity-comp-port</b>
</td>
</tr>
<tr>
<td>
monitor=ENABLE の訂正記録
</td>
<td>
<b>2026-06-06-openarm-teleop-debug</b>（Orboh 社内の文書。必要ならメンバーに聞く）
</td>
</tr>
<tr>
<td>
max\_rel クランプとトルクの機構
</td>
<td>
<b>2026-06-23-openarm-ik-reach-realarm</b>（Orboh 社内の文書。必要ならメンバーに聞く） §3-4
</td>
</tr>
<tr>
<td>
右CAN bus-off 履歴・収集前警告
</td>
<td>
<b>2026-06-12-openarm-teleop-instability-handoff</b>（Orboh 社内の文書。必要ならメンバーに聞く）
</td>
</tr>
<tr>
<td>
G1 安全設計（FileEStop・SafetyMonitor）
</td>
<td>
顧客と共有している Vault の G1 設計書（F-11・全体設計書の安全前提。パスは除いた）
</td>
</tr>
<tr>
<td>
rollout 計画（§9 は即停止を確認項目として挙げるのみで手順・検証記録なし＝TODO根拠）
</td>
<td>
別案件の ACT 計画 §9（ノート名は除いた）・<b>openarm-grasping-pipeline-plan</b>（Orboh 社内の文書。必要ならメンバーに聞く） P1-7
</td>
</tr>
<tr>
<td>
GR00T G4 経路（eval\_[openarm.py](http://openarm.py)・Server/Client 分離・recvタイムアウト未設定）
</td>
<td>
<b>gr00t-openarm-pipeline-design</b>（Orboh 社内の文書。必要ならメンバーに聞く） §5.3/§6・<b>gr00t-openarm-validation-plan</b> G4
</td>
</tr>
<tr>
<td>
Foresight F5（警報→停止階層接続・TODO#3 格上げ根拠）
</td>
<td>
<b>foresight-openarm-validation-plan</b>（Orboh 社内の文書。必要ならメンバーに聞く） §4 F5
</td>
</tr>
<tr>
<td>
起動時整列（align\_on\_connect・07-08）・盲目窓の確定値（chunk50@30Hz=1.67s）
</td>
<td>
顧客と共有している Vault の OpenArm テレオペ setup guide（パスは除いた）・<b>vla-track-experiment-refinement-2026-07-08</b>（Orboh 社内の文書。必要ならメンバーに聞く）
</td>
</tr>
<tr>
<td>
収集マニュアル（§4 の転記先）
</td>
<td>
顧客と共有している Vault の他のメンバー用フォルダにある収集マニュアル §7（パスは除いた）
</td>
</tr>
</table>

---

## 改訂履歴

- <b>2026-09-20（Jev 段A 実機・</b>2026-09-20-openarm-jev-stage-a-real-arm<b>（Orboh 社内の文書。必要ならメンバーに聞く））</b>: ①§0 クランプ行を「復元トルク上限 = kp × max\_rel」に書き換え（clamp 1.0 で手首 0.44 / 肩 4.2 Nm が重力負荷 1.2 / 6.5 Nm に負けて hold が垂れた実測）②§1 に L1.5 ソフト E-stop を行として追加（<code>jev_arm.py</code> で実機検証済み）・L0 に max\_rel の注意と追従誤差ガードを追記 ③TODO#3 を部分完了に更新・TODO#7（GR00T 経路の同点検）を新設。
- <b>2026-07-12（ultracode検証 21エージェント・指摘反映）</b>: ①前提ゲートを実行系非依存に一般化（旧 lerobot-rollout 固定→ACT/SmolVLA 新規ループ＋GR00T <code>eval_openarm.py</code> の経路ごと検証）②自動監視系の受け口を §1 に定義（soft E-stop=L1.5・Foresight F5 実施時は必須格上げ）③§2 起動前手順を 07-08 起動時整列に追従（腕が2.2秒自動で動く新注意）④盲目窓を確定値に更新（1.67s/0.53s・経路依存）⑤リセット規定のタスク非依存化 ⑥TODO#2/#3/#4 更新＋#6 新設 ⑦参照名・文言の精度修正。
- <b>2026-07-06</b>（Orboh 社内の文書。必要ならメンバーに聞く）: 初版。
