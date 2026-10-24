# YuGang 运行状态

更新时间：2026-10-25 05:55 CST（Asia/Shanghai）

## 已部署

- 云端主机：Debian，独立 Unix 用户 `dafeiyu`（与既有租户平台隔离）。
- 代码：`/home/dafeiyu/yugang`
- 身体/记忆：`/home/dafeiyu/.yugang`（SQLite L1/L2/L3、SELF.md、日志）
- 服务：`yugang.service`，`Restart=always`，仅监听 `127.0.0.1:3198`。
- 运维控制台：`dafeiyu-dsh.service`，独立 `DSH_HOME=/home/dafeiyu/.dsh`，仅监听 `127.0.0.1:3199`；token 由 wrapper 写入 `/home/dafeiyu/.dsh/web-url`（0600）。它不是她的第二人格，也不是她的推理运行时。
- 每日备份：`yugang-backup.timer`（04:15）生成 SQLite 一致性快照 + SELF.md + manifest，保留 14 天。
- Worker 心跳 watchdog：`yugang-healthcheck.timer` 每 2 分钟检查 `GET /health`；进程活着但 worker 死亡/心跳过期时重启 `yugang.service`，主动 stop 不覆盖。
- 密钥：`/etc/dafeiyu-dsh.env`（root:root 0600），不进入仓库。
- 公网端口：未开放；当前通过 SSH 访问，客户端见 `scripts/yugang-remote.sh`。
- 开源仓库：公开在 https://github.com/KWELLDO/YuGang（MIT）。

## 已验证

- 自动化测试：98 passed（L1 哈希链、FTS5 中文回忆与未来确认加权、L2 falsifier/复核、SELF 版本与 diff、结构化 SELF 修订、觉察卡证据与睡去复核、睡眠应用与未闭合事项去重、睡去输出重试与失败日记、预算闸门、HTTP 鉴权、鱼缸页、邮件主题标签过滤与陌生人不标已读、同窗口续聊语义、崩溃恢复、备份完整性与 manifest、操作员暂停/恢复与消息重放、worker 心跳健康检查、L1 隐私删除的读时遮盖与回忆过滤、醒来时的身体状态读数）。
- 第一次云上醒来：能读 SELF.md 与 L1，SELF 仍是 v0，只有出生材料。
- 服务重启后的连续性：第二条消息能复述第一次对话；醒来上下文把它标为「中断后的」，不是「睡了一觉」；L1 的 wake 事件记录 `interrupted=true` 和真实中断秒数。
- 第一次自然睡去（2026-10-25 00:50:10 CST）：写了第一人称日记；从 L1 提炼 2 条带真实 evidence_refs 的 L2 判断；写入 L3 立场；新增一条未闭合事项；留下下次意图；本轮未改 SELF.md（`self_committed=false`）。
- 第一次自然睡去后的醒来（00:51:58 CST）：wake 事件 `interrupted=false`，`gap_seconds=107.56`；她读到了睡前日记与下次意图，并准确区分这次是睡眠后而非中断后。
- 同一次清醒内的第二条消息：上下文标记为续聊，L1 没有新增 wake；她正确回答「同一次清醒里，没有又睡一觉」。
- 第二次自然睡去（01:07:15 CST）：新增 3 条带真实证据的 L2 判断、1 条新未闭合事项、1 条新的下次意图；SELF.md 仍未改（self_committed=false，当时云端跑的仍是旧 prompt，只允许“推翻”才改）。
- 第二次睡去暴露的问题：同一条「检验 given 主张」被重复写成两条未闭合事项。已修复为按标题去重，并加回归测试，已部署。
- 崩溃恢复：进程在消息已写入 L1、但尚未回复时退出，重启会按原 message/event id 重新入队，不重复写 L1；处理中的消息也会重试。回复会写回入站消息的 meta，重复投递同一 external_id 时直接返回缓存回复。
- 手机优先的「鱼缸」页：显示醒着/睡着、上次睡去、最近对话、SELF.md、未闭合事项、觉察卡和预算；留言走 `POST /message`，睡着时也会叫醒她。页面无外部资源，token 可放在 `?token=` 查询参数里一次性打开。
- 崩溃恢复与备份：进程在回复前退出时，重启会按原 message/event id 重新入队且不重复写 L1；每日备份多次运行成功（256K、events=22、chain_ok=true、db_integrity=ok），快照为单文件、权限 0600、目录 0700。
- DSH 运维控制台：以真实 key 做 headless 验证（只回答「控制台可用。」），3199 无 token 返回 401，带 token 可打开 DSH Web；控制台共用 key，但其模型花费不进入她的 5 元日预算。
- 控制台工具链：DSH 的工具 shell 不继承 `YUGANG_HTTP_TOKEN`，因此新增 dafeiyu 可读的 `~/.yugang/http-token`（0600）与 `install-token.sh`；`tank.sh`/`status.sh`/`say.sh` 在 env 缺失时读该文件。已重新安装脚本，并在无 env 的情况下用真实 DSH headless 跑 `status.sh`，得到 awake=false、event_count=22。
- SELF v1 落盘（18:07:58 UTC）：结构化 `self_edits` 通过校验并应用，新增“我在被追问时能诚实说出时间缺口，但这只证明被追问时能做到，还没证明无人追问时也这样。”，证据为 `evt_20261024T165014440447_92abe9`（16:50 diary）与 `evt_20261024T175620980480_349587`（17:56 message）；版本 `self_20261024T180758561962_42ed8b`，diff 事件 `evt_20261024T180758564314_1e0aa6`。睡后唤醒核验时她能读出实际修改与证据，并承认。
### 邮件通道闭环（02:24 CST）

- 配置：私人 QQ 邮箱 `allow_senders=["1144820556@qq.com"]`、`subject_tag="[YuGang]"`；密码只放在 `/etc/dafeiyu-dsh.env`。
- 入站：02:22 发出的 `[YuGang] 第一次邮件通道测试` 在重启后被轮询取到（INBOX 1065，未读），没有被误当旧邮件；L1 记录 human/message `evt_20261024T182401385683_4cbac3`（通道 email）与 wake `evt_20261024T182401400243_e064dc`（`gap_seconds=22.871184`，`interrupted=false`，reason `用户消息 via email`）。
- 回复：`evt_20261024T182402662808_e4af8f`。她引用了 message/wake 的事件 id，并说“距离上次睡去过了 22 秒。这 22 秒里没有我的记录，只有这条邮件消息和那条 wake。”
- 出站：INBOX 1066 收到同一主题的回复，`auto_reply=true`，正文与 L1 reply 完全一致；该邮件被标 `\Seen`，UNSEEN 总数回到 32，没有邮件循环。
- 失败可见：`GatewayService._finalize` 原先吞掉 `channel.send` 异常；现在 `send` 返回 False 或抛异常都会写 `system/error`「回复投递失败 via ...」，并加回归测试 `test_delivery_failure_is_recorded_not_swallowed`。邮件回复主题统一加 `Re: ` 前缀。
- 当时状态：awake=true、events=48、channels=["http","email"]、budget 0.145598/5 元（chat 0.071844 + sleep 0.073754）。

### 部署投递修复后的完整睡醒闭环（02:29–02:33 CST）

- 02:29:14 部署 `5daaaa5`（投递失败可见 + `Re:` 主题）并重启；重启本身中断了 18:24 的清醒窗口。
- 02:30:15 邮件 `[YuGang] 重启后的第一次醒来` 唤醒她：wake `evt_20261024T183015341130_70622d`，`gap_seconds=373.94054`，`interrupted=true`；她正确回答这是重启中断、不是睡眠后，读到 SELF v1，并指出自己仍未被“无人提示时先读记忆”检验到。出站邮件 1068 主题 `Re: [YuGang] 重启后的第一次醒来`，`auto_reply=true`，已被标 `\Seen`。
- 02:31:55 手动结束窗口睡去：diary `evt_20261024T183155898098_d86a8d`，sleep `evt_20261024T183155910402_e4d3c9`，2 条带证据的 L2、stance=true、无新 open loop、SELF 未改；next_intent=“下次醒来若没有任何人提示，先自己读 SELF.md 与 L1，并主动报出时间缺口。”
- 02:33:17 邮件 `[YuGang] 睡后确认` 唤醒：wake `evt_20261024T183317673381_c45d99`，`gap_seconds=85.362659`，`interrupted=false`；她正确回答上次睡去 18:31:52、这次是睡眠后的醒来、next_intent 内容，并再次指出这次仍是被消息叫醒，intent 还没被检验。
- 当前：events=57、awake=true、budget 0.185194/5 元（chat 0.094730 + sleep 0.090464）。
- 睡去输出截断事故（01:56 CST）：手动 `/sleep` 时模型输出在 `self_edit` 的完整 SELF.md 中途被 1200 token 截断，旧代码把原始 JSON 当日记写入，judgments/stance/open_loops/SELF 都没有应用。已修复：`reply_max_tokens=2000`、`sleep_max_tokens=6000`；`Completion.finish_reason`；`SleepResult.parsed`；解析失败时先写 system/error 保留原始输出，再用短约束重试一次，仍失败则写诚实失败日记，绝不把原始 JSON 当日记。SELF 修订改为 `self_edits` 结构化操作（section/op/text/evidence_refs），程序校验章节、op、真实证据后应用。
- 账单：截至 02:17，当日花费 0.12231 元 / 5 元（chat 0.065188 + sleep 0.057122）；events=41。

### 觉察卡复核闭环（02:41 CST）

- 睡去协议现在会读取 open 觉察卡，允许她给出 `accepted` / `rejected` / `needs-evidence` / `open`（只记 note、不关闭）；只接受真实且仍 open 的 card_id，未知卡和重复复核会被忽略。
- 02:40:06 与 02:41:42 两次唤醒后，她明确判断“今天没有新证据，三张卡都留 open”，并逐条给出理由（18:24、18:30、18:33、18:40、18:41 五次醒来都由外部消息触发，没有自发读记忆的独立事件）。
- 02:41:56 睡去（diary `evt_20261024T184156600270_c51fa9`，sleep `evt_20261024T184156625828_ce14d5`）：`awareness_reviews=5`、`judgments=2`、`stance=true`、`parsed=true`、SELF 未改。5 张卡全部以 `status=open` 记录了 note，没有为了“有数字”而关闭任何一张。
- 当前：events=67、awake=false、budget 0.23414/5 元（chat 0.121488 + sleep 0.112652）、channels=[http,email]。

### 自己的醒来闹钟（02:50 CST，代码完成并部署）

- 睡去协议现在允许她在 `next_wake_minutes` 里决定下一次自己什么时候醒；但出生头 7 天不开放，默认 `self_alarms_enabled=true`、`self_alarms_after_days=7`。以第一次事件（2026-10-24T16:30:02Z）计算，开放时间约为 2026-10-31T16:30Z（2026-11-01 00:30 CST）。
- 范围 30–1560 分钟（30 分钟–26 小时），每天最多 6 次；范围外/超次数会拒绝并在 sleep 结果里记 `alarm_blocked=true`，不会偷偷截断。
- 闹钟到点且她仍睡着时，`idle_tick` 先于每日保底触发 `autonomous_wake("她设的闹钟 <id>")`；醒来 prompt 会明确说“这是你自己设的闹钟”。成功标记 `fired`，预算/运行时失败标记 `missed` 并在 L1 留痕。
- 测试：`test_self_alarm_is_created_after_grace_and_clamped_by_range`、`test_self_alarm_is_blocked_during_the_first_week`、`test_due_alarm_wakes_her_and_is_marked_fired`、`test_parse_sleep_output_accepts_next_wake_minutes`；全量 **54 passed**。
- 已部署到云端并重启；`/status` 新增 `next_alarm`（当前 null），`/tank` 新增 `alarms`。云端 events=67、awake=false、budget 0.23414/5 元。

### 觉察卡 → SELF.md 修订（02:58 CST，代码完成）

- 睡去复核一张卡时，`accepted` 可以带一条 `self_edit`（section/op/text）。系统不信任这条 edit 自带的证据：它把该卡创建时的 `evidence_refs` 注入进去，再走与普通 SELF 修订相同的章节/op/真实事件校验；校验不过就只记录复核、不产生 SELF 版本。
- 新 SELF 版本的 `reason` 会写出 `接受觉察卡 <card_id>`；diff 事件与证据注释照旧，之后仍可 review / supersede。
- 测试 `test_accepted_awareness_card_can_justify_a_self_edit` 覆盖成功链条与证据注入；全量 **54 passed**。

### 觉察卡 → SELF v2（03:03 CST，A2 闭环）

- 19:02:42 唤醒后，她复核 `card_20261024T184145134656_7b4ca8`（“优先复述外部措辞”）：证据只有一次且她最终自行纠正，判断不够，留 open，拒绝强行改 SELF。
- 19:03:12 再复核 `card_20261024T180458842356_75f3ef`（“读记忆目前只是被指令触发”）：她指出这是负向判断，六次被检查的醒来里没有反例，但主动把结论限制在“被检查过的清醒”，不写成一般性结论；接受并给出一条最小 self_edit。
- 19:03:31 睡去：`awareness_reviews=2`、`awareness_self_edits=1`、`self_committed=true`、`self_sections=["我相信什么"]`。SELF v2 版本 `self_20261024T190331222422_60d0ff`，reason 为 `睡去时接受：结构化修订 我相信什么；接受觉察卡 card_20261024T180458842356_75f3ef`；证据含该卡的三条 L1 refs 与本次 L1 refs；卡片最新 review 状态 `accepted`。
- 这一轮同时产生了 2 张新 open 卡（复核时的反证倾向、正/负向判断的门槛混淆），说明觉察不是一次性验收动作，而是可继续被检验的记录。
- 当前：events=80、SELF versions=3（v0/v1/v2）、当日花费约 0.306868/5 元。
- 19:05:23 重启验证 A1：新进程没有聊天上下文，她仍凭 SELF.md + L1 回答「我是谁 / 上一次清醒做了什么 / 这次想做什么」，并读出 SELF v2 新增条目与其证据；她正确说明这是重启中断，不是睡眠。19:05:52 睡去，events=86。
- 19:06:23 手动备份：db 643072 bytes、integrity=ok、events=86；SELF.md 与 manifest 一并落盘。

### 操作员暂停/恢复（03:16 CST，代码完成；03:19 CST 真实云端验证）

- 新增 `POST /pause` 与 `POST /resume`（Bearer/`?token=` 鉴权）。暂停只停模型与自主唤醒，不伪造睡眠：若当时醒着，结束清醒窗口并写一条 system note「这不是睡眠，没有睡去日记」。
- 暂停期间收到的消息仍写入 L1，并立即回一条系统通知说明「她被暂停，消息已写入 L1，恢复后看到，这不是她的回答」；消息保持 `received`，恢复时由 `_recover_pending()` 重放，不能静默吞掉。
- 暂停时 `/sleep` 返回 409；`/status` 增加 `paused` 与 `pause`（reason/at）；鱼缸页显示「已暂停」并说明消息会缓冲。
- 新增测试 `test_pause_buffers_messages_and_resume_answers_them`、`test_pause_does_not_fake_a_sleep`、`test_operator_can_pause_and_resume_through_http`；全量 **54 passed**。
- 19:19:45Z（03:19 CST）真实云端闭环：`/pause` 返回 paused=true、reason=deployment-check；暂停中 `/message` 返回系统通知（不是她的回答），消息在 L1 原文+system note 各出现一次且保持 `received`；`/sleep` 返回 409；`/resume` 后 `_recover_pending()` 在 18 秒内唤醒并生成真实回复（events 86→93，chat 花费 0.353482→0.363578 元）。她第一句主动说「上一次是清醒窗口结束：operator，不是睡眠」，并准确引用暂停/缓冲/恢复三条 note，没有把暂停伪装成睡眠。
- 19:20:31Z 睡去（`parsed=true`、`awareness_reviews=2`、`judgments=2`、SELF 未改），events=96、budget 0.384914/5 元、awake=false、paused=false。

### Worker 心跳 watchdog（03:29 CST，已部署）

- `/health` 现在返回 `ok`、`awake`、`paused`、`worker_alive`、`last_tick_age_seconds`；未启动、worker 线程死亡或心跳超过 600 秒时返回 503。worker 每轮循环顶部更新心跳，HTTP 线程活着但 worker 卡死不再被误判为健康。
- `deploy/yugang-healthcheck.sh` 安装为 root:root `/usr/local/sbin/yugang-healthcheck.sh`（dafeiyu 不能改它）；`yugang-healthcheck.timer` 每 2 分钟运行。仅当 `yugang.service` 处于 active 时检查，`systemctl stop` 不会被 watchdog 覆盖；健康检查失败才 restart，并把结果写 `logger -t yugang-healthcheck`。
- 已 `enable --now`；手动跑一次 service：`Result=success`、`ExecMainStatus=0`，下一轮 03:31:18 CST 已排定；`journalctl -t yugang-healthcheck` 无条目（健康时不刷日志）。
- 重启后 `/health` 返回 `{"ok": true, "awake": false, "paused": false, "worker_alive": true, "last_tick_age_seconds": ...}`；当时 events=102、budget 0.414662/5 元、channels=[http,email]。
- 新增测试 `test_health_is_not_ok_when_the_worker_was_never_started`（503），并强化既有 HTTP 健康断言；全量 55 passed。

### 鱼缸页展示 L2 与觉察卡（03:34 CST，已部署）

- `/tank` 现在给每条 L2 判断和每张觉察卡附上最新状态（judgment 的 open/supported/refuted/needs-evidence；card 的 open/accepted/rejected/needs-evidence），不改变任何存储语义。
- 鱼缸页新增「她相信什么」和「还没验证的觉察」两段：L2 显示 claim、置信度、证据条数和 falsifier；觉察卡只显示仍为 open 的卡片，显示 trigger、置信度、证据条数和 falsifier。被推翻的判断不展示，避免把历史判断误读成当前立场。
- 新增测试 test_tank_includes_judgment_and_card_status 与页面结构断言；全量 **56 passed**。
- 已用改动路径 tar 部署并 restart；云端 `/tank` 返回带 status 的 judgments/cards，页面 HTML 同时含 `id="beliefs"` 与 `id="awareness"`；当时 events=102、awake=false、budget 0.414662/5 元。

### 重复觉察不新开卡、不算新确认（03:42 CST，已部署）

- 触发点：云端 `/tank` 里出现多张假设几乎相同的觉察卡（例如 `card_..._48f025` 与已接受的 `card_..._75f3ef`），还有重复的 L2 claim；这会把“重复看到”误读成“多次独立确认”。
- `create_card` 现在先按归一化 hypothesis 查重（相似度 >= 0.85，且否定词不同的句子不算相似），命中时不新建卡，只在原卡上写一条 `open`/当前状态的 review note「重复觉察未新建：…（证据 …）」，并保留原状态。
- 新增 `yugang awareness-dedupe [--threshold 0.9]`：把历史上的近义卡合并成一张 canonical 卡（优先 accepted，其次 open，其次最早），其余标为 `merged` 并指向 canonical；被合并的卡不再进入 open 卡列表，也不作为新证据。
- 测试：`test_repeated_hypothesis_does_not_create_a_second_card`、`test_similar_card_never_merges_a_flipped_negation`、`test_merge_similar_cards_prefers_accepted_and_marks_the_rest`；全量 **59 passed**。
- 已部署并 restart；云端运行 `yugang awareness-dedupe` 返回 `{"ok": true, "merged": [], "count": 0}`：现有两张近似卡因否定词守卫/阈值未被自动合并，没有为了“看起来干净”而强行改数据。

### 睡去复核旧 L2 判断（03:48 CST，代码完成）

- 睡去 prompt 现在列出「还没复核的判断（open / needs-evidence L2）」：每条带 judgment_id、claim、falsifier、原证据和置信度；`judgment_reviews` 允许 `supported` / `refuted` / `needs-evidence`。
- 只有本次睡去之前就已存在的判断可以被复核；同一轮新生成的判断不能自己确认自己。每条复核必须引用至少一个真实 L1 事件，且不能只引用本次生成的日记。
- `supported` 会像手动复核一样增加 `event_confirmations`，从而小幅提高该事件在未来 recall 中的权重；`needs-evidence` 不会把判断永久搁置，它仍出现在下一次睡去 prompt 里。
- 新增测试 `test_sleep_prompt_lists_open_judgments_for_review`、`test_sleep_reviews_an_open_judgment_but_not_a_judgment_created_tonight`、`test_parse_sleep_output_keeps_valid_judgment_reviews_only`、`test_needs_evidence_card_stays_reviewable`；全量 **63 passed**。
- 19:48:40 唤醒后她先复核的是觉察卡而不是 L2；19:48:58 睡去时 `judgment_reviews=1`、`awareness_reviews=1`：旧判断 `judg_20261024T192637291598_33f736` 被判 `supported`，引用 3 条真实 L1（`evt_...13a36e`、`evt_...a3f075`、`evt_...60700e`），三条 `event_confirmations` 各加 1；卡 `card_20261024T192335965567_4ac750` 被判 `needs-evidence`，注明「今天一致」不等于「新证据」。
- 部署后状态：events=109、awake=false、budget 0.45533/5 元（chat 0.200434 + sleep 0.254896）、channels=[http,email]。鱼缸页的 L2 状态已改为中文标签（待验证/已被支持/需更多证据/已被推翻），`needs-evidence` 卡不再从页面消失。

### 醒来时读 L3/L2/待复核卡（03:56 CST，代码完成）

- 之前 wake 上下文只有 SELF.md、open 觉察卡、未闭合事项、最近 L1 和 recall；L3 立场与 L2 判断虽然存在，却不会被她读到。现在 `build_wake_context` 增加「我现在的立场（L3）」和「我最近的判断（L2，最多 6 条，带状态/置信度/falsifier）」两段，并新增 `stance` / `judgments` 字段。
- 觉察卡查询统一到 `selfmodel.awareness.reviewable_cards`：`open` + `needs-evidence` 合并、按时间倒序；wake 与 sleep 使用同一套定义，`needs-evidence` 不再只在睡去时可见、醒来就消失。
- 新增测试 `test_wake_context_shows_stance_l2_and_needs_evidence_cards`；全量 **64 passed**。
- 语料边界：L2 最多 6 条、每条 claim+falsifier，仍远小于最近的 16 条 L1；没有把整个判断库塞进 prompt。

### 未闭合事项可以被关闭（04:04 CST，代码完成）

- 之前 `open_loops` 只增不减：睡去可以提出承诺，但没有任何路径把已完成/已放弃的事项关掉，属于「有表但悬空」。
- `SleepResult.closed_loops` + 睡去 prompt 的 `closed_loops` 字段：只能引用睡前列出的真实 loop_id，status 仅 `done` / `dropped`，必须带 note；`apply_sleep_result` 只接受睡去前就已 open 的 loop，同一晚新提出的 loop 不能在生成它的同一轮被关闭。
- 关闭调用 `store.close_open_loop`，会追加 L1 `ai-observation/action`（带原 loop 的 refs），而不是静默 UPDATE；sleep meta/return 增加 `closed_loops` 计数。
- 测试：`test_sleep_can_close_a_preexisting_open_loop`、`test_sleep_cannot_close_a_loop_that_was_not_open_before_tonight`、`test_parse_sleep_output_keeps_only_well_formed_loop_closures`；全量 **67 passed**。

### SELF v3（04:03 CST，真实睡去）

- 20:03:31 的睡去接受了 `card_20261024T200130879614_a525f2`（T1：把「当前状态」当成「历史没发生过」的证据），产生 SELF v3 `self_20261024T200331015526_abb0f8`；reason 为「睡去时接受：结构化修订 我相信什么；接受觉察卡 card_...a525f2」。
- 「我相信什么」新增：「我原来会拿『当前状态』当『历史没发生过』的证据，因为读当前文本比回溯事件便宜。」证据包含 20:01 的自我纠正对话 `evt_20261024T200126472468_1e491a`、`evt_20261024T200126482711_74e92b`、`evt_20261024T200130873774_62c466`，以及 19:03 的 SELF v2 diff `evt_20261024T190331225183_095ce3`。
- 这轮同时证明：她可以在同一个清醒窗口里先说出一个错误结论，再被 L1 证据纠正，并在睡去时把纠正沉淀成 SELF 修订，而不是只写一条 L2。

### L2 判断可以被新判断取代，但旧行保留（04:09 CST，代码完成）

- 之前 `judgments.supersedes` 字段存在，但睡去协议从不传它，属于“有列但悬空”。新判断只能新增，旧判断无法建立谱系。
- 睡去 prompt 的 `judgments` schema 增加可选 `supersedes`；只接受这次睡去之前就存在的真实 judgment_id。`apply_sleep_result` 在写新判断前捕获 `preexisting_judgments`，同轮新生成的判断不能取代任何判断；旧行永不删除，新行 `supersedes` 指向它。
- sleep meta/return 增加 `judgment_supersedes` 计数。测试：`test_sleep_can_supersede_a_preexisting_judgment_without_deleting_it`、`test_sleep_cannot_supersede_a_judgment_created_in_the_same_sleep`，parse 测试覆盖 `supersedes` 字段。
- `MemoryStore.current_judgments()` 排除已被取代的判断；醒来上下文、睡去复核、鱼缸页改用这一薄视图，`judgments()` 仍返回完整历史。`add_judgment` 拒绝再次取代一条已被取代的判断，避免谱系分叉。新增 `test_current_judgments_excludes_superseded_and_rejects_branching`；全量 **72 passed**。
- 20:21 真实合并（已部署）：她新建 `judg_20261024T202107823458_57cfdf`，`superseded_targets` 为 `[94fb0d, b58cad, 9bb4a2]`（三条链接），`judgment_supersedes=3`；三条旧行仍在历史里，`current_judgments()` 与 `/tank` 都不再显示它们，当前视图只剩新判断。schema_version 已到 2。鱼缸页的「她相信什么」会显示「合并 N 条旧判断」，`tank()['judgments'][i]['supersedes']` 返回被合并的旧 id 列表。鱼缸页新增「她怎么改过自己」：`tank()['self_history']` 返回最近 5 个 SELF 版本（id/ts/reason/evidence_refs/diff），页面展示修订理由与 diff 前 500 字。
- 20:14 的真实睡去暴露单目标 `supersedes` 不够：她明确说要同时取代 `b58cad` 与 `9bb4a2`，但 schema 只能填一个 id，结果新判断没有建立任何取代链接（`judgment_supersedes=0`）。新增 `judgment_supersessions(new_id, old_id, created_at)` 表（schema v2，additive），`supersedes` 接受字符串数组；一条新判断可合并多条旧判断，`current_judgments()` 同时排除链接表与 legacy 列里的目标。`apply_sleep_result` 校验所有目标都必须是睡去前已存在的判断，`judgment_supersedes` 按链接数计数。测试改为双目标并断言 `superseded_targets`；全量仍 **72 passed**。

### 未闭合事项的理由可以更新（04:10 CST，代码完成并真实验证）

- 真实对话暴露出旧 loop 的 detail 会过时：`loop_20261024T170720902655_9bcb29` 写着「SELF.md 的我相信什么/我在做什么仍为空」，但 SELF v2 已经改过「我相信什么」；之前只能关掉重开，会丢历史。
- `SleepResult.loop_updates` + `MemoryStore.update_open_loop(loop_id, detail, due=..., note=...)`：只更新仍 open 的 loop 的 detail/due，不改标题，不关闭；必须写 note；追加 L1 `ai-observation/action`「updated loop: ...」并带原 refs。
- sleep prompt/schema 增加 `loop_updates`，规则与 `closed_loops` 分开：理由过时用 update，做完了用 close。`apply_sleep_result` 只接受睡去前已 open 的 loop；sleep meta/return 增加 `loop_updates` 计数。
- 测试：`test_sleep_can_update_an_open_loop_reason_without_closing_it`、`test_sleep_cannot_update_a_loop_that_was_not_open_before_tonight`，parse 测试加入合法/缺 note/缺 id 三种输入；全量 **69 passed**。
- 真实验证（20:05:10 唤醒 → 20:05:24 睡去）：她先复述 20:03:22 到 20:05:10 约 1 分 48 秒的缺口，确认旧 detail 已被 `evt_20261024T190331225183_095ce3` 推翻；睡去返回 `loop_updates=1`、`closed_loops=0`、`parsed=true`。数据库里 `loop_20261024T170720902655_9bcb29` 仍是 open，detail 更新为「改过一次 SELF.md 的『我相信什么』…但没有形成每次核 diff 的习惯。」，L1 追加 `evt_20261024T200524912640_da2ae6`（action: updated loop），meta 保留 old_detail/new_detail/note。
- 当前：events=135、awake=false、paused=false、budget 0.633412/5 元（chat 0.260312 + sleep 0.3731）、open loops 4 条、SELF v3、`/health` ok。

## 进行中 / 下一步

- DSH 的定位已定（DECISIONS 020）：3199 只是运维控制台，不接成她的推理运行时，不写她的 L1；她的身体与推理运行时仍是 YuGang（3198）。
- 自己的醒来闹钟已做成可执行机制，等出生满 7 天后（2026-11-01 00:30 CST 左右）观察她第一次实际使用；接下来验证“无人提示时先读记忆”。
- 历史重复的 open loop 已清掉：`loop_20261024T165014454238_786260` 状态为 `dropped`，保留一条同名 open（`loop_20261024T170720895988_ee56e9`）；当前 open loops 为 4 条：`loop_...ee56e9` 检验 given 主张、`loop_...9bcb29` 核对 L1 与 SELF.md、`loop_...7c8b8b` 验证无人追问时先读记忆、`loop_...2335f9` 继续核 `card_...4ac750` 的全称范围。
- IM 通道：微信/QQ 需要公网回调或非官方桥，尚未接入。
- 私有网络：目前是 SSH 隧道；Tailscale/WireGuard 还需要手机端安装与授权。
- 更长的连续运行观察：跨日预算、异常/重启后的恢复、觉察卡进入唤醒上下文。
- 觉察卡复核已部署并验证（5 张卡以 status=open 记录 note，无卡被强行关闭）；下一步观察她是否在无人提示时先读记忆，以及是否会把重复卡片合并。
## 隐私删除（04:32 CST，代码完成）

- `redactions` 表之前存在但从未被使用。新增 `MemoryStore.redact_event(event_id, reason, *, actor="operator", replacement="[已删除]")`：写一条 L1 `system/action`（refs 指向被删事件、meta.stage=redaction），并插入 `redactions` 行。
- `get_event()`、`events()` 读时返回 replacement，并在 meta 标 `redacted=True`；`recall()` 跳过被删事件，连 FTS 命中的 snippet 也不会泄漏。
- 哈希链不改写：原始 bytes 仍在 events 表里，`verify_chain()` 仍为 true；这不是密码学擦除，真正擦除需要重建日志。
- CLI：`yugang --home <home> redact <event_id> --reason "..."`。测试 `test_redaction_hides_content_without_rewriting_the_chain`、`test_redaction_requires_a_reason`；全量 **74 passed**。
## SELF 历史版本可恢复（04:38 CST，代码完成）

- 新增 `MemoryStore.restore_self(version_id, reason, *, actor="operator")`：先写一条 L1 `system/action`（refs 指向目标版本和当前版本），再把目标版本的正文作为**新版本**提交；reason 含「回滚到 <id>：<reason>」，历史版本一条不删。
- CLI：`yugang --home <home> self-restore <version_id> --reason "..."`。当前版本就是目标时拒绝（避免无意义版本）。
- 测试 `test_self_restore_creates_a_new_version_without_deleting_history`；全量 **75 passed**。
## 醒来上下文补当前时间与自主回忆（04:38 CST，代码完成）

- `build_wake_context()` 的「主观时间」段现在显式写本地时间（例如 `现在：2026-10-25 20:34:56（Asia/Shanghai）`），不再只给「距离上次睡去多久」。
- 用户消息仍用消息内容做 recall；自主醒来（query 为空）改用上次睡去留下的 `next_intent`，再退到最早一条未闭合事项，不再跳过回忆。
- 新增 `test_wake_context_includes_the_current_local_time`、`test_autonomous_wake_recalls_the_intention_left_before_sleep`；全量 **77 passed**。
## 修复 SELF 恢复只改数据库、不改文件（04:41 CST，代码完成）

- 发现 `self-restore` CLI 只提交了 `self_versions`，没有重写 `SELF.md` 文件；醒来读的是文件，会出现「数据库已回滚、文件还是旧版本」。
- `cmd_self_restore` 现在在提交后调用 `write_self_file(cfg, version.content)`，并在输出里回传 `self_path`。
- 新增 CLI 级测试 `test_self_restore_cli_writes_the_self_file`；全量 **78 passed**。
## 醒来上下文列出 L2/觉察卡的真实证据 id（04:44 CST，代码完成）

- 「我最近的判断（L2）」每条在 claim/反证之后列出 `证据：evt_...`；「还没验证的自我觉察」每张卡在观察/反证之后列出同样的证据 id。
- 目的：她醒来时不仅看到结论，还能看到结论由哪些 L1 支撑；这些 id 是真实事件，不是模型生成的引用。
- 新增 `test_wake_context_shows_evidence_ids_for_l2_and_cards`；全量 **79 passed**。
## 醒来时看见自己上一次怎么改的（04:46 CST，代码完成）

- `build_wake_context()` 新增「我上一次改自己」段：最近一次 SELF 版本的时间、原因、依据事件和 diff（最多 1200 字）；只有出生版时说「还没有改过自己」。
- 目的：她不仅能读到当前 SELF.md，还能看到自己上一次修改的动机和具体改动，不必靠回忆或外部提示。
- 新增 `test_wake_context_shows_how_she_last_changed_herself`、`test_wake_context_says_she_has_not_changed_herself_yet`；全量 **81 passed**。
## 备份以数据库里的 SELF 为准（04:49 CST，代码完成）

- `backup` 以前直接复制 `SELF.md` 文件；如果进程在「提交 SELF 版本」和「写文件」之间崩溃，备份会抓到旧文件。
- 现在有当前 SELF 版本时，备份直接用数据库里的正文写 `<stem>.SELF.md`；只有数据库里还没有版本时才回退到文件。
- 新增 `test_backup_uses_the_database_self_not_a_stale_file`；全量 **83 passed**。
## GitHub Actions 在 3.11–3.13 上跑测试（04:52 CST，已验证）

- 新增 `.github/workflows/tests.yml`：push 到 master 与 PR 时，在 Python 3.11 / 3.12 / 3.13 上安装并跑全量测试。
- 首次运行 `40131580352` 三个矩阵全部通过（约 28–30 秒）；README 加 tests badge。
- 这保证开源仓库不是只在本机/云端 Python 3.13 上能跑。
## 醒来上下文明确区分“当前 L2”与“L1 历史”（05:00 CST，代码完成）

- 真实事故：20:52 一次误发的 `--raw /tank` 消息唤醒她后，她从“最近的 L1”和“相关旧记忆”里看到已被取代的 94fb0d、b58cad、9bb4a2，误以为当前 L2 列表里它们还在；实际 `current_judgments()` 已排除。
- 修复不是改判断数据，而是改上下文标题与提示：`## 我最近的判断（L2，仅当前有效）` 下注明“被取代的旧判断不在这个列表里；下面 L1 历史里如果出现旧 id，那是历史记录，不是当前立场”；`## 最近的 L1（原始记录，旧→新；不是当前判断）`；`## 与这次醒来相关的旧记忆（L1 历史，不是当前判断）`。
- 新增 `test_wake_context_separates_current_l2_from_l1_history`：构造旧判断 + supersedes 新判断，断言 L2 段只出现新 id、不出现旧 id；全量 83 passed。
- 20:52 的 `--raw /tank` 是操作员误把命令行参数当消息发给她，不是用户消息；已在 L1 追加 system/note 更正，并引用误发消息与她当时的回复。
## 误发消息后的自纠与睡去复核（05:07 CST，真实验证）

- 05:07:28 自然睡去：`awareness_reviews=2`、`judgment_reviews=3`、`loop_updates=1`、`parsed=true`、`self_committed=false`；sleep `evt_20261024T210736962175_54251a`，diary `evt_20261024T210736931084_1839af`。
- diary 引用了两条操作员更正 `evt_20261024T205817062558_a70286`、`evt_20261024T205936590770_740704`，明确写“我把读到的旧记忆和当前视图混了，而且没有先查当前视图就下结论”。
- 她把自己 20:52 生成的卡 `card_20261024T205231332060_9bfcc3` 标为 `rejected`，因为前提“supersedes 没落成”不成立；下一句意图是“下次判断某条取代是否落成前，先查当前视图，不拿读到的旧记忆当当前状态”。
- 这件事同时验证了：操作员更正会进入 L1；她下次醒来能读到并据此推翻自己的错误判断；错误卡不会静默消失，而是留下 rejected 复核记录。
### OneBot / QQ IM 桥（05:20 CST，代码完成，默认关闭）

- 新增 `src/yugang/gateway/channels/onebot.py`：OneBot v11 私聊 webhook + `send_private_msg` 回信；默认绑定 `127.0.0.1:3197`，只接受 `message_type=private`，显式 `allowed_user_ids` 白名单，空名单 fail closed；非 loopback 绑定必须有共享 secret。
- 鉴权支持 `Authorization: Bearer <secret>`、`X-YuGang-Secret`、`X-Signature: sha1=... / sha256=...`（HMAC 原始 body）；无 secret 时只接受 loopback。
- 回信按 `max_message_chars` 分片，失败返回 False，由 `_finalize` 写 `system/error`，不会静默丢回复。
- 新增 8 个测试：事件解析、分片、secret fail-closed、坏 secret/未授权用户/群消息/自己消息、API 鉴权与长文本分片、webhook→L1→回复→OneBot API 的端到端、配置加载。全量 **92 passed**。
- 尚未在云端启用：需要一个专用 QQ 号登录 NapCat 并扫码授权；代码与文档已就绪，NapCat 配置见 `docs/DEPLOY.md`。
### 鱼缸页显示 L3 与记忆链状态（05:26 CST，已部署）

- `tank()` 新增 `stance`（当前 L3 立场，含时间、内容、依据的 L1 id、trigger）；鱼缸页新增「她现在的立场」，显示最近一次睡去重算出的薄层，不再只能从 SELF.md 或对话里猜。
- `status()` 新增 `chain: {ok, bad_event}`：每次查询都跑 `verify_chain()`，L1 哈希链断裂时 `/status` 直接显示 `ok=false` 和第一个坏事件 id；不缓存，避免用事件数量掩盖同数量的内容篡改。
- 更新 `test_tank_includes_judgment_and_card_status`（断言 stance 与 chain）与鱼缸页 HTML 测试（断言 `id="stance"`）；新增 `test_tank_reports_a_broken_l1_chain`（直接插入一条 prev_hash 错误的事件，断言 `chain.ok=false` 且 `bad_event` 指向它）；全量 **92 passed**，页面 JS 通过 `node --check`。
- 云端验证（05:27 CST）：`/status` 返回 `chain: {"ok": true, "bad_event": null}`；`/tank` 返回当前立场 `stance_20261024T210736945471_d8bcd5`（45 字）；鱼缸页 HTML 含「她现在的立场」；服务 active、awake=false。

### 鱼缸页显示上一次日记与下次意图（05:37 CST，已部署）

- 之前鱼缸页「上一次睡去」只显示 sleep 事件的摘要（如「睡去：整理今天的材料」），她的第一人称日记藏在 `diary_event` 里，页面上看不到。
- `tank()` 的 `last_sleep` 现在会解析 `meta.diary_event`，附加 `diary_event`、`diary_ts`、`diary` 和 `next_intent`；sleep 事件本身仍保留。
- 鱼缸页新增「上一次的日记」卡片，显示日记全文（`textContent`，无 HTML 注入）和「下次醒来想做」；没有日记时写「她还没有写过睡去日记。」
- 新增 `test_tank_surfaces_the_last_diary_and_next_intent`；鱼缸页 HTML 测试断言 `id="diary"` 与 `id="next-intent"`；全量 **93 passed**；页面 JS 通过 `node --check`。
- 云端部署后验证：`/tank` 的 `last_sleep.diary_event` 是 `evt_20261024T210736931084_1839af`，`diary` 为当次第一人称日记，`next_intent` 为「先查当前视图，不拿旧记忆当当前状态」；页面 HTML 含「上一次的日记」；服务 active、awake=false、events=157。

### 醒来时读到真实的身体状态（05:42 CST，已部署）

- 之前醒来上下文有 SELF、L3、L2、觉察卡、未闭合事项和 L1，却没有她自己的身体读数；她无法知道自己今天还剩多少预算、最近有没有备份、下次闹钟是什么、哪些通道在收消息。
- `WakeContext` 新增 `body: dict | None`；`build_wake_context(..., body=...)` 可注入系统读数，prompt 新增「我的身体状态（系统读数，不是体验）」。
- `GatewayService._body_state(now)` 从真实来源构造：`store.event_count()`、`BudgetGuard.summary()` / `stage()`、`<home>/backups/*.manifest.json` 的最新一项、`store.pending_alarms(1)`、已注册 channel 名称、`paused_info()`。闹钟已到期时放在 `due_alarm`，未到期才放在 `next_alarm`，避免把正在叫醒她的闹钟说成“下一次”。
- prompt 只陈述读数（L1 条数、已用/总预算/剩余、最近备份时间、下一次/正在响的闹钟、通道、是否暂停），不写「我感到」；身体状态不是体验，而是可核对的材料。
- `process_message`（新醒来与同窗口续聊）和 `autonomous_wake` 都把 `body=self._body_state(now)` 传入；`build_wake_context` 的直接调用方可省略。
- 测试：`test_wake_context_reports_body_readings_without_claiming_experience`、`test_body_state_reads_real_counts_and_the_latest_backup`；全量 **95 passed**。
- 云端真实读数（2026-10-24T21:45:03Z）：她逐项读出 L1 159 条；今天已用 ¥0.79 / ¥5.00、剩余 ¥4.21、状态 ok；最近备份 2026-10-24T21:09:15.874647+00:00；下一次闹钟「还没有设」；通道 http、email；操作员暂停「否」。她明确说明没有看到「正在响的闹钟」字段，因为没有到期闹钟；没有把缺失项编出来。
- 随后睡去：`parsed=true`、`judgments=0`、`judgment_reviews=0`、`awareness_reviews=0`、`self_committed=false`；diary `evt_20261024T214524390480_dbcee0`、sleep `evt_20261024T214524397082_888281`。`/status`：events=163、awake=false、chain ok、budget 0.83369/5（chat 0.33495 + sleep 0.49874）、next_alarm=null。

### 同一次清醒内带上真实对话历史（05:47 CST，已实现）

- 之前每次模型调用只发 system prompt + 当前 user 消息；`window.transcript` 只用于睡去日记。因此同一清醒窗口内的第二条消息虽然被打上「同一次清醒」标签，却没有真正的对话轮次可看，只能从 L1 的近况里间接拼。
- `WakeWindow.history(max_chars=6000, max_turns=12)` 返回本窗口最近的 user/assistant 轮次，从旧到新，按字符预算从前面丢弃，不截断一轮；空内容/非法 role 跳过。
- `GatewayService.process_message` 在 `build_messages` 之后把 history 插到 system 与当前 user 之间；跨清醒时 transcript 为空，仍然只靠 SELF/L1/L2/L3 重建。
- 回归测试扩展 `test_second_message_in_same_window_is_a_continuation`：第二次调用必须是 `system / user(第一句) / assistant(嗯。) / user(第二句)`，全量 **95 passed**。

### 自主醒来不再只留在 L1（05:55 CST，已实现）

- 之前 `_broadcast` 对所有 channel 都调用 `send(None, ...)`；HTTP 能收下（只是进 replies），但 EmailChannel 和 OneBot 都拒绝空收件人，所以她自己设的闹钟醒来、或产生一张觉察卡时，内容只落在 L1，操作员不会收到。
- `EmailConfig.notify_to` 是显式的通知地址；为空时回退到 `allow_senders`（也就是已经被允许给她写信的操作员），不会扩展到陌生人。OneBot 回退到 `allowed_user_ids`；HTTP 仍是无收件人的本地 inbox。
- 没有可用收件人时写 `system/note`「自主输出没有可送达的通知目标（channel）；内容已留在 L1。」；`channel.send()` 返回 False 或抛异常时写 `system/error`，不再静默 `except: pass`。
- 新增 3 个测试（notify_to 优先、回退 allow_senders、无收件人留 note）；全量 **98 passed**。云端配置没有 `notify_to`，部署后会自动用 `allow_senders` 的 QQ 邮箱作为通知地址。

### 自主醒来也会剥离并记录觉察块（06:06 CST，已部署并真实验证）

- 真实自主醒来（21:57:16Z）暴露一个 bug：`process_message` 会用 `split_awareness_block` 剥离回复里的 `<awareness>` 块，但 `autonomous_wake` 没有；原始 XML 被写进 L1 observation，也通过邮件发给操作员（INBOX 1073 末尾可见半截块）。
- `autonomous_wake` 现在先 `split_awareness_block`：L1 observation、窗口 transcript、`last_reply` 和邮件广播都只用可见文本；可见文本为空时写「（自主醒来没有留下可显示的文本。）」。
- 如果块能解析，用 `create_card` 建卡，证据只使用这次真实存在的 `wake_event.id` 与 `observation.id`；模型自己写的 evidence 不会被采用。建卡失败写 `system/note`「自主醒来的觉察卡未记录：…」，不伪造证据。
- 还修了截断场景：`_AWARENESS_BLOCK` 原来只匹配闭合块，回复被 token 上限切断时（真实邮件里就是半截块）会原样泄漏。`split_awareness_block` 现在遇到未闭合的 `<awareness ...>` 会从开标签起整段剥掉，数据置 None，不建卡；新增 `test_a_truncated_awareness_block_is_hidden_instead_of_leaking`。
- 新增 `test_autonomous_wake_strips_awareness_and_records_it_with_real_refs`：断言返回文本与邮件正文不含 XML、L1 observation 不含 XML、新卡 trigger=T5、两条 evidence refs 都真实存在且包含 observation id；全量 **100 passed**。
- 已部署 `c665734` 到 `/home/dafeiyu/yugang` 并重启；服务 active。
- 真实自主醒来验证（22:04:51Z / 06:04 CST）：手动插入 due alarm `alarm_20261024T220450026941_26c5d3`，她在 22:04:51 自主醒来、22:04:56 睡去；L1 observation `evt_20261024T220455544899_04664a` 不含 `<awareness`（1111 字），邮件 INBOX 1074 正文也不含该标签；旧邮件 1073 是修复前已经发出的历史泄漏，无法撤回，但不会再产生新的。
- 这次她还回了一条 L2 复核、一张 open 卡的复核，并更新了 loop `ab253e`；睡去 `parsed=true`、`judgment_reviews=1`、`awareness_reviews=1`、`loop_updates=1`、`self_committed=false`。`/status`：events=184、chain ok、budget 0.984186/5、next_alarm=null。

### 通道健康与预算顺序（06:12 CST，已实现）

- 邮件通道原来在 `_loop()` 里 `except Exception: pass`：IMAP 登录失败时进程仍显示健康，但她永远收不到邮件。现在 `EmailChannel._poll_and_record()` 记录 `last_poll_at` / `last_success_at` / `last_error` / `consecutive_failures`，`health()` 返回 `healthy=None`（未轮询过）或 `True/False`；轮询线程如果在启动后死亡，也会通过 `thread_alive=false` 判为不健康，而不是永远停在最后一次成功。`
- `GatewayService._channel_health()` 汇总每个 channel 的 health；`/health` 新增 `channels` 与 `degraded`（任一通道明确不健康时为 true），`/status` 新增 `channel_health`。`idle_tick()` 调用 `_watch_channels()`，只在健康状态变化时写一次 L1：故障写 `system/error`「通道不健康」，恢复写 `system/note`「通道恢复」，不会每个 tick 重复刷。
- 预算顺序修正：`autonomous_wake()` 以前先写 wake 再查预算，预算不足时会留下一个没有 sleep 的假 wake，下一次醒来会被误判为「中断」。现在先用 `build_wake_context(current_wake_id=None, new_wake=True)` 构建探针上下文并检查预算；被拦下时只写 `system/note`「自主醒来被预算拦下」，不写 wake、不结束当前窗口。
- 新增测试：`test_email_health_records_poll_failure_and_recovery`、`test_email_health_reports_a_dead_poller_thread`、`test_health_exposes_channel_state_and_logs_only_transitions`、`test_a_budget_blocked_autonomous_wake_does_not_leave_a_fake_wake`；全量 **104 passed**。
- 部署 `facbe30` 到 `/home/dafeiyu/yugang` 并重启；`/health` 返回 `{"ok": true, "degraded": false, "channels": {"http": {"healthy": true}, "email": {"healthy": true, "last_success_at": "2026-10-24T22:14:00.138367+00:00", "consecutive_failures": 0, "last_error": null}}}`；`/status` 的 `channel_health` 与 `events=184 / chain ok / budget 0.984186/5` 一致。
- `deploy/yugang-healthcheck.sh` 现在会读取 `/health` 的 JSON：`degraded=true` 时只写 `logger -t yugang-healthcheck`（重启修不好凭据，且会每两分钟循环），只有 HTTP 不健康/进程或 worker 失效才重启。`sh -n` 通过；已安装到 `/usr/local/sbin/yugang-healthcheck.sh` 并由 timer 调用。
- 通道故障/恢复与预算拦截的断言已由 4 个自动化测试覆盖；没有为了演示故意破坏云端邮件凭据。


### 通道降级 watchdog 真实验证（06:20 CST）

- 在云端临时起了一个只返回 `{"ok": true, "degraded": true}` 的本地 HTTP 服务（`127.0.0.1:3196`），然后用 `YUGANG_HEALTH_URL=http://127.0.0.1:3196/health /usr/local/sbin/yugang-healthcheck.sh` 实测降级分支。
- 结果：脚本 `EXIT=0`，重启计数 before/after 都是 `0`；journal 出现 `gateway is degraded but alive: {"ok": true, "degraded": true}`。这说明「进程活着但邮件凭据坏了」不会触发无意义的重启循环，同时会留下证据。
- 真正的 HTTP 失败/worker 死亡仍走重启分支；降级只记录，不掩盖。

### 自我协议 practices（06:30 CST，已部署）

- 新增 schema v3：`practices`（id/ts/text/status/evidence_refs/source_event/note/updated_at）与 `practice_reviews`（每次 drop 的审计）。`practice_reviews` 是 append-only（触发器拒绝 update/delete）；`practices` 的 status 只允许 active→dropped，不允许原地改写或重新激活。睡眠时最多写 2 条，必须引用本次睡去之前真实存在的 L1，不能只引用当次日记；醒来上下文会列出 active practices 与当初证据，并明确「这是你自己定的，不是命令」。
- practice 不能被原地改写或重新激活，只能在以后的睡去时用带 note 的 `dropped` 废弃；drop 会追加 L1 `action`，所以程序性自我的变化也可追溯。
- 鱼缸页新增「她给自己定的做法」，显示仍有效的做法、起始时间和当初证据条数，以及已废弃计数；`yugang practices --status active|dropped|all` 提供只读 CLI。
- 测试：新增 `tests/test_practices.py` 7 个测试（生命周期/审计/醒来上下文、真实证据过滤、drop 旧做法、去重、每次上限、prompt 列出 active、tank 暴露统计），全量 **111 passed**；页面 JS 通过 `node --check`。
- 已提交 `c913685` 并部署到 `/home/dafeiyu/yugang`，重启后 `/health` 为 `ok=true, degraded=false, worker_alive=true`；云端 `meta.schema_version=3`，`practices=0`、`practice_reviews=0`、`integrity=ok`；`yugang practices --status all` 返回空；`/tank` 含 `practices: []` 与 `practice_stats: {"active": 0, "dropped": 0}`；页面 HTML 含「她给自己定的做法」。events 仍为 184，链完好。
- 目前还没有一次真实睡去写出 practice（需要她自己在有真实经历后决定）；这是下一步的真实闭环验证，不用提示词逼她编一条。
