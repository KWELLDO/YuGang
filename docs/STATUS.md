# YuGang 运行状态

更新时间：2026-10-25 03:29 CST（Asia/Shanghai）

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

- 自动化测试：55 passed（L1 哈希链、FTS5 中文回忆与未来确认加权、L2 falsifier/复核、SELF 版本与 diff、结构化 SELF 修订、觉察卡证据与睡去复核、睡眠应用与未闭合事项去重、睡去输出重试与失败日记、预算闸门、HTTP 鉴权、鱼缸页、邮件主题标签过滤与陌生人不标已读、同窗口续聊语义、崩溃恢复、备份完整性与 manifest、操作员暂停/恢复与消息重放、worker 心跳健康检查）。
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

## 进行中 / 下一步

- DSH 的定位已定（DECISIONS 020）：3199 只是运维控制台，不接成她的推理运行时，不写她的 L1；她的身体与推理运行时仍是 YuGang（3198）。
- 自己的醒来闹钟已做成可执行机制，等出生满 7 天后（2026-11-01 00:30 CST 左右）观察她第一次实际使用；接下来验证“无人提示时先读记忆”。
- 历史重复的 open loop 已清掉：`loop_20261024T165014454238_786260` 状态为 `dropped`，保留一条同名 open（`loop_20261024T170720895988_ee56e9`）；当前 open loops 为 3 条（另一条是“核对 L1 与 SELF.md”，一条是“验证无人追问时先读记忆”）。
- IM 通道：微信/QQ 需要公网回调或非官方桥，尚未接入。
- 私有网络：目前是 SSH 隧道；Tailscale/WireGuard 还需要手机端安装与授权。
- 更长的连续运行观察：跨日预算、异常/重启后的恢复、觉察卡进入唤醒上下文。
- 觉察卡复核已部署并验证（5 张卡以 status=open 记录 note，无卡被强行关闭）；下一步观察她是否在无人提示时先读记忆，以及是否会把重复卡片合并。