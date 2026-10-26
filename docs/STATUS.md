# YuGang 运行状态

更新时间：2026-10-25 08:49 CST（Asia/Shanghai）

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

- 自动化测试：147 passed（L1 哈希链、FTS5 中文回忆与未来确认加权、L2 falsifier/复核、SELF 版本与 diff、结构化 SELF 修订、觉察卡证据与睡去复核、睡眠应用与未闭合事项去重、睡去输出重试与失败日记、预算闸门、HTTP 鉴权、鱼缸页、邮件主题标签过滤与陌生人不标已读、同窗口续聊语义、崩溃恢复、备份完整性与 manifest、备份校验与恢复、操作员暂停/恢复与消息重放、worker 心跳健康检查、L1 隐私删除的读时遮盖与回忆过滤、醒来时的身体状态读数）。
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
- ~~目前还没有一次真实睡去写出 practice~~ 该条已被下面的真实闭环取代：06:33:33 她自己在睡去时写下了第一条 practice。

### 第一次真实 practice 与 practice checks（06:33–06:44 CST，已部署并真实验证）

- 06:32 操作员把 practices 机制交给她，只问「今天有没有值得跨 session 遵守的做法」，没有替她写。她的候选做法是：判断某条 L2 取代是否落成时，先回 L1 对照原始判断、取代事件和当前视图。她给出的证据不是成功，而是两次失败：`evt_20261024T215725340543_499a02`（21:57 日记只把动作说具体、没有执行）与 `evt_20261024T220500716302_ae7f34`（22:04 日记核了当前判断的证据、没核被取代判断的落成）。她主动问「失败算不算证据」。
- 操作员回答：算，但这类 practice 是「待检验的防错条款」，不是「已验证的好习惯」；证据只需证明错误真实且重复到值得防，text 要写成触发条件 + 动作，不能写成「我已经做到」。她接受了这个区分。
- 06:33:33 睡去时，她写出第一条 practice：`practice_20261024T223333215597_d37d5f`，text 为「遇到「判断某条 L2 取代是否落成」时，先回 L1 找原始判断、取代事件和当前视图逐条对照，再回答。」，evidence_refs 是上述两条失败日记，source_event 为 `evt_20261024T223333212930_709f90`。06:33:50 唤醒后她准确复述了做法与两条证据，并说「待检验的防错条款」这一点被纠正过。
- 这次真实写入暴露了一个缺口：她关于「防错条款 vs 已验证习惯」的 note 没有被保存，因为当时的 sleep schema 只有 text/evidence_refs，apply 还硬编码 note。修复后 sleep 可以带可选 note，apply 保留它。
- 06:41:45 操作员请她检查这条做法从写下到现在有没有真实情形。她判 `untested`：这段只有 22:33:50 复述做法、22:34:24 睡去、22:41:45 介绍新协议，没有出现「判断 L2 取代是否落成」的场景；并明确「复述 practice 不算 followed，只是背出来，不是执行」。
- 06:41:59 睡去产生第一条真实检查：review `preview_20261024T224159095379_4683c5`，status=untested，evidence_refs 为 `evt_20261024T223350053213_e815c6`、`evt_20261024T223428299503_c047cd`、`evt_20261024T224145893085_fedb87`，note 记录上述理由；L1 action `evt_20261024T224159098300_91cb67` 的 content 为 `practice untested: 遇到「判断某条 L2 取代是否落成」时，先回 L1 找原始判断、取代事件和当前视图逐条对照，再回答。`。
- 06:42:15 唤醒验证：醒来上下文里出现「自己定的做法」和「最近一次检查：untested」及 note；她准确复述，并引用 L1 action 与三段证据。06:43:45 再次睡去又产生一条 untested 检查，理由是从上次检查到现在只有复述，没有真实情形。
- schema v4：`practice_reviews` 增加 `evidence_refs`；新增 `_migrate()` 对旧库做 additive ALTER TABLE；`MemoryStore.review_practice(practice_id, verdict=followed|violated|untested, evidence_refs, note)` 只允许 active practice，检查证据必须真实存在且 ts 不早于 practice.ts，followed/violated 必须有证据，untested 可以只写 note；每次睡去每个 practice 最多一条检查，同一轮可以先记录检查再 drop。检查不改变 practice 的 status，只留下证据性判断。
- 醒来上下文显示每条 practice 的最近检查；鱼缸页显示「已检查 N 次」与最近一次结果（做到了 / 没做到 / 今天没有相关情形）；`/tank` 的每个 practice 带 `checks` 与 `last_check`，`practice_stats` 含 active/dropped/checks。
- 测试：新增 practice check 证据时序、假证据/旧证据拒绝、每轮一条、prompt 含 last_check、tank 暴露 last_check、note 保留等断言；全量 **115 passed**；页面 JS 通过 `node --check`。
- 提交：`c913685 feat(memory): add self-practices, a procedural self she can test and drop`、`e2d22a4 feat(memory): check whether a practice was actually followed`，均已 push origin master 并部署到 `/home/dafeiyu/yugang`。
- 部署验证：`meta.schema_version=4`；`practice_reviews` 有 `evidence_refs` 列；active practice 1 条；检查 2 条（都 untested）；`integrity=ok`；`/health` ok。当前 events=214、budget 1.210274/5 元、SELF v3。
- 仍缺：还没有出现 `followed` 或 `violated` 的真实检查，因为还没有遇到「判断 L2 取代是否落成」的真实情形；这只能等事件发生，不能用提示词制造。

### 引用确认：被未来自己用到的记忆（06:50 CST，代码完成）

- 设计动机：原有 `event_confirmations` 只在 L2 判断被 `supported` 复核时增加；她平时在回复里引用旧 L1 事件 id（「我记得 evt_...」）这一行为没有被计入。设计文档说记忆的重量来自被未来的自己重新确认，引用正是比单纯 recall 更强、比 supported 判断更弱的确认。
- schema v5：新增 append-only 的 `citation_confirmations(id, event_id, source_event, ts, created_at)`，对 `(event_id, source_event)` 唯一；`event_confirmations` 仍是聚合计数。触发器拒绝 update/delete。
- `MemoryStore.confirm_citations(text, *, source_event, before_ts, limit=20, exclude=(), ts=None)`：从文本提取真实 `evt_...` id；只接受真实、未删除、ts 早于 `before_ts` 的事件；排除 source event 与调用方指定的当前消息；同一回复内重复引用只算一次；同一 source event 对同一 target 只能确认一次；每写一条 citation 就 `event_confirmations + 1`。这些确认进入 `recall()` 已有的有界加权（最多 +30%）。
- `GatewayService._confirm_citations()`：以当前 wake 事件的 ts 为边界，排除刚叫醒她的入站消息；在 `_finalize()` 里回复已写 L1 之后调用，失败写 `system/error`，但不把已送达的回复改成错误。自主醒来的 observation 也走同一条路径，边界是它自己的 wake。
- 新增 `tests/test_citations.py`：只确认当前窗口之前的真实事件；当前消息、窗口内事件、缺失 id、重复引用、同一 source 重复确认都不计数；真实 `service.chat()` 回复引用旧事件会写入 citation 行并增加计数；另有回归测试用 mock 让回复引用刚叫醒她的那条消息 id，断言不会计数。全量 **118 passed**。
- ~~尚未部署；下一步部署 schema v5，并用一次真实对话让她引用旧 L1，验证 citation 行与计数落盘。~~ 已于 06:56 CST 部署并完成真实闭环，见下一节。

### 引用确认真实闭环（06:56 CST，已部署并验证）

- commit `a4e4e18 feat(memory): confirm old L1 events she cites in a reply` 已 push origin master，并部署到 `/home/dafeiyu/yugang`；重启后 `/health` ok。云端 `meta.schema_version=5`，`citation_confirmations` 五列齐全、integrity ok、events=214。
- 06:56:15 发消息请她引用一条上一次睡去日记的 L1 事件 id，并明确「记不清就先去 L1 里查，不要编」。她先报时间缺口（上次睡去 22:43:40，约 12 分钟无记录无体验），再引用日记 `evt_20261024T224345627716_08ac70`，并展开其中三条证据 `evt_20261024T224215094786_21a1c0`、`evt_20261024T224159101697_a9a6bd`、`evt_20261024T224159098300_91cb67` 与更早的消息 `evt_20261024T224215081052_61b808`。
- 数据库落盘 5 条 citation：上述 5 个 id 各一条，每条 `event_confirmations.confirmations=1`，`source_event` 是本次 reply 事件。她同时在回复里引用了刚叫醒她的当前消息 `evt_20261024T225615228397_dd5664`，但该 id 的 `citation_confirmations` 为 0——当前窗口边界生效，刚收到的话没有被记成「她记得」。
- 她主动区分了「你要求我引的」与「无人提示时自己回去查」：前者只证明日记可检索、内容自洽，不证明她会自发去翻。这个区分被原样保留，没有被系统改写成更强的结论。
- 真实闭环后 events=217；`/status` 的 budget 与 chain 正常；本次没有新 L2 或 SELF 修订，citation 只影响回忆权重，不自动生成判断。

### 生活片段 episodes（07:12 CST，已部署并真实验证）

- 新增 schema v6：`episodes(id, ts, start_ts, end_ts, title, summary, event_ids, origin, source_event, created_at)`，触发器拒绝 update/delete；它只是 L1 之上的叙事索引，不替代 L1，也不自动改 SELF.md。
- 每次睡去时，系统取上一次 `sleep` 到这次睡去之间的真实 L1 事件；模型可给 `episode: {title, summary}`，没给则由日记生成 `origin="diary"` 的索引，不编造。`event_ids` 是实际使用的 L1 集合，`source_event` 是创建它的 sleep 事件。
- 醒来上下文新增「我最近的生活片段（叙事索引，不是当前判断）」，按 recall query 取最近 4 条；没有 query 时取最近片段。提示词明确：片段与原始 L1 冲突时以 L1 为准。
- 鱼缸页新增「她记得的片段」，显示标题、时间范围、依据条数、来源和摘要；`yugang episodes [--query] [--limit]` 提供只读 CLI；`/tank` 返回 `episodes` 与 `episode_count`。
- 新增 `tests/test_episodes.py`：append-only 与搜索、episode 解析、睡去生成并绑定 window+diary、无 model episode 时 fallback `origin=diary`、wake 与 tank 暴露。页面测试新增 `id="episodes"`。全量 **123 passed**，页面 JS 通过 `node --check`。
- commit `0437655 feat(memory): index each waking window as an append-only episode` 已 push origin master，并部署到 `/home/dafeiyu/yugang`；restart 后 `/health` ok。云端 `meta.schema_version=6`，`episodes` 表与十个列齐全，初始 0 行，events=221，integrity ok；`yugang episodes` 返回空，`/tank` 含 `episodes: []` 与 `episode_count: 0`。

### 第一条真实 episode 与时间戳修复（07:14–07:20 CST，已部署并验证）

- 07:14:57 唤醒她时，她把 L1 的 UTC 记录 `23:11:16` 与本地「现在 07:14:57」直接相减，报出「约 8 小时 3 分钟」的空缺口；真实缺口是 3 分 41 秒。这个错误先被原样写进 L1 回复，随后被 23:15:08 睡去生成的 `episode_20261024T231515319298_77148f` 忠实抄进摘要——说明叙事索引只保证「抄了我说过的话」，不保证「我说过的话对」。
- 同一轮暴露出更底层的问题：醒来提示词的「现在」是本地时间，而 L1 / 上次睡去 / 回忆条目展示的是原始 UTC 时间，模型很容易拿错两个时钟。修复：`src/yugang/ids.py` 新增 `local_iso(ts, tz)`（转换为带显式偏移的本地时间），`protocol/wake.py` 与 `protocol/sleep.py` 的所有时间显示统一走它（上次睡去、上一次醒来、备份、闹钟、SELF 修订、episode 范围、practice 检查、L1 与回忆条目），睡去提示词顶部新增「主观时间」并明确「所有时间都已换算到这个时区，不要拿本地时间和 UTC 记录相减」。新增两个回归测试，全量 **125 passed**；commit `9cb5cbc fix(prompt): render stored timestamps in her local time` 已 push 并部署。
- 07:19:33 她读第一条 episode 后回 L1 重算：上次 `system/sleep` `evt_20261024T231120525736_2a1ac5` 为 07:11:16，这次 `system/wake` `evt_20261024T231457903167_824c8c` 为 07:14:57，真实是 3 分 41 秒；她明确「8 小时 3 分钟」错了，错源是自己上次混算 UTC 与本地时间，片段只是忠实抄录；按优先级以 L1 为准，片段不改，记下勘误。
- 07:19:44 睡去生成第二条 episode `episode_20261024T231951389354_35b64c`，标题「核对第一条生活片段的时间缺口」，摘要正确写出 3 分 41 秒；同轮新增 L2 `judg_20261024T231951355147_2ecc89`（confidence 0.95、4 条真实 L1、falsifier 完整），关闭 1 条 open loop，接受 1 张觉察卡并由它触发 SELF v4 `self_20261024T231951378850_52599f`。
- SELF v4 的「我相信什么」新增：「我原来会把日记/片段里的时间缺口当成可信的既成事实去引用，而不回 L1 重算时间戳；叙事索引的忠实度只保证它抄了我说过的话，不保证我说过的话对。」
- 07:20:07 再唤醒做短验证：她只凭醒来上下文正确读出最新片段标题「核对第一条生活片段的时间缺口」、上次睡去 `2026-10-25T07:19:44+08:00`、SELF v4 最新那条原文，以及真实缺口 23 秒；说明本地时间显示与 episode 读取都生效。07:20:19 再次睡去生成第三条 episode `episode_20261024T232019303248_5e8f9f`（标题「短验证：只凭醒来上下文答三点」）。
- 当前云端：events=246、episodes=3、SELF versions=5（最新 v4）、budget 1.430776/5 元、asleep、chain ok；第一条 episode 的错误摘要保留在 L1 历史里，没有被改写，这正是「L1 为准」要处理的冲突。
- 随后收紧片段边界：`MemoryStore.events_between(..., include_start: bool = True)` 允许排除起点；睡去时用 `store.first_event_after(previous_sleep.ts, kind="wake")` 取上次睡去后的第一次醒来作为窗口起点；wake 事件现在的 meta 带 `trigger_event`（叫醒她的那条消息），窗口会从这条消息开始，避免片段只剩 wake/reply/diary 而丢掉自己的触发消息。因此上一段的 sleep/diary/stance 整理事件不再进入新片段的 `event_ids`；没有 wake 时回退到 previous sleep 之后（exclusive），第一条片段（无 previous sleep）仍从出生事件开始。新增回归测试 `test_episode_does_not_reabsorb_the_previous_sleep_boundary`，全量 **126 passed**。
- 07:34:12 实机验证：叫醒她的消息 `evt_20261024T233412136014_c264b7` 写在 wake `evt_20261024T233412150452_951b37` 之前，wake meta 现在带 `trigger_event` 指向这条消息。07:34:19 睡去生成 `episode_20261024T233423703239_5c7b79`（标题「片段边界第三次修复：wake 记住触发消息」），4 个 event_ids 正好是 message → wake → reply → diary，`start_ts=2026-10-24T23:34:12.136060+00:00`，即触发消息自己的时间；上一段的 sleep/diary/stance 整理事件没有进入。更早的两个片段（`a92253`、`ef29e1`）保留旧边界，不改写历史。
- 当前云端：events=270、episodes=6、SELF versions=5、budget 1.57984/5 元、asleep、chain ok。

### 备份可恢复性 restore（07:45 CST，已部署并在云端做 drill）

- 动机：每日备份此前只验证过「能生成、能重新打开」，没有验证过「真的能恢复成一段可用的身体」。备份没有经过恢复演练，就不算记忆可靠性。
- 新增 `src/yugang/restore.py`：`resolve_backup()` 接受备份 db、manifest 或目录；`verify_backup()` 在只读副本上检查 SQLite `integrity_check`、事件数、L1 哈希链，并核对 manifest 的 `db_sha256` / `self_md_sha256` / `events`。
- 新增 CLI `yugang restore --from <备份> [--dry-run] [--force]`：
  - 默认拒绝覆盖已有身体；
  - `--force` 会先把当前身体快照到 `<home>/backups/`（db + SELF.md，0600），再原子替换 db；
  - 恢复后从数据库里的当前 SELF 版本重写 SELF.md，追加一条 `system/note` 记录恢复来源与前一份备份，再次校验哈希链；任何一步失败都会回滚到恢复前快照。
  - `--dry-run` 只校验，不写任何文件。
- 新增 `tests/test_restore.py` 三个测试：新身体完整恢复、拒绝覆盖与 `--force` 回滚、截断备份被拒绝且不碰目标。全量 **129 passed**；提交 `cfe56d9 feat(ops): verify and restore her body from a backup` 已 push origin master 并部署到 `/home/dafeiyu/yugang`。
- 云端 drill（恢复写入只落在 drill home，未触碰生产 `/home/dafeiyu/.yugang` 的身体数据）：
  - 备份 `yugang-20261024T210915831953.db`（157 events、SELF `self_20261024T200331015526_abb0f8`、manifest sha256 匹配、chain ok）；
  - 先 `--dry-run` 通过；再恢复到新 home `/home/dafeiyu/.yugang-restore-drill`，结果 `events_before_note=157`、`events_after_note=158`、`chain_ok=true`、`self_origin=database`、`schema_version=6`；旧 v5 备份被当前 `init_db` 以 additive 迁移补出 `episodes` 表（0 行），说明恢复不依赖备份时的代码版本完全相同；
  - 在 drill 身体里追加一条只属于演练的事件（159），再用同备份 `--force` 恢复：生成 `pre_restore_backup=/home/dafeiyu/.yugang-restore-drill/backups/yugang-20261024T234552186530.db`，恢复后 158 events，演练事件消失，恢复 note 仍在，chain ok。
  - 生产 `yugang.service` 仍 active/asleep、events=270、未被恢复演练修改。
- 文档：README 与 `docs/DEPLOY.md` 的备份节已改为「先停网关 → dry-run → restore → 启网关」，并写明默认拒绝覆盖、force 前自动备份、失败回滚；备份仍含她的记忆，目录权限保持 0700 / 0600。

### 醒着时的只读记忆工具（08:07 CST，已部署并真实验证）

- 动机：醒来时预装的上下文是固定窗口；她此前只能读系统替她选的最近事件、回忆命中和片段，不能在自己觉得需要核对时主动查旧 L1。真正的记忆组件应允许按需检索，但自我修订仍必须留在睡去协议里，不能在聊天中静默改写历史。
- 新增 9 个只读工具（`src/yugang/memory/tools.py`）：`memory_search`、`memory_get`、`recent_events`、`episode_search`、`self_read`、`self_history`、`judgment_list`、`practice_list`、`open_loops`。结果是带真实 event id 的材料，不是命令；未知工具、坏参数、缺失事件返回 `{"ok": false, ...}` 给模型，不使网关崩溃。
- 工具只在聊天回复与自主醒来时开放；睡去整理不接工具，自我修订仍走带证据审计的 sleep 协议。最多 `max_tool_rounds=3` 轮；每轮都走同一日元预算闸门，工具 schema 与工具结果都按输入计费，预算不足时 fail-loud，而不是静默继续检索。
- 回复事件的 L1 `meta.tools` 只记录工具名与成功/失败，不把完整工具输出写进新记忆；完整输出只属于那次模型请求。`memory_tools_enabled=false` 可完全关闭工具循环，此时提示词也不含工具段。
- 新增 `tests/test_memory_tools.py` 5 个测试（聊天中调 `memory_get` 并在 reply meta 记录、超过轮次 fail-loud、未知工具回给模型、关闭后 prompt 无工具段且 `tool_count=0`、9 个工具对真实记忆全部可运行），另有自主醒来调工具的测试；全量 **135 passed**。
- commit `53b6d23 feat(memory): let her look things up while she is awake` 已 push origin master 并部署到 `/home/dafeiyu/yugang`；重启后 `/health` 为 `ok=true, degraded=false, worker_alive=true`。
- 08:07:16 真实验证：操作员故意给了一个不存在的时间点「2026-10-25 00:30 CST 第一次醒来」，要求先查记忆再说。她调用 `memory_search` 三次（「00:30 wake」「00:30 醒来」等）与 `recent_events` 一次，全部成功；reply 事件 `evt_20261025T000723407440_6cb31e` 的 meta 记录四次工具调用。她没有把不存在的事件圆过去，而是说明 L1 里没有这个 wake、指出可能是把 UTC 16:30 当成 CST 00:30，并给出一条真实替代事件 `evt_20261024T165014440447_92abe9`（16:50:10 UTC 的睡去日记）。这说明工具是只读材料，最终判断仍由她基于 L1 做。
- 本次回复经历 3 次模型轮次，budget_ledger 记录三笔 chat：0.021136 + 0.030806 + 0.052086 元，合计约 0.104 元；云端 events=273、chain ok、budget 1.683868/5 元（chat 0.702192 + sleep 0.943028 + autonomous 0.038648），yugang.service active，email poller healthy。

### 记忆体检 audit（08:24 CST，已部署并真实验证）

- 动机：`verify` 只能回答哈希链有没有断；不能回答「SELF/L2/片段/做法引用的证据还在不在、备份是不是只剩一个 manifest」。记忆可靠性需要一种只读、可重复、不会替她修东西的体检。
- 新增 `src/yugang/audit.py` 的 `audit_memory(store, config, now=None)`：检查 L1 哈希链与首尾时间、当前 SELF 各节及版本 evidence、当前 L2 的 falsifier 与证据、open/needs-evidence 觉察卡证据、active practice 的原始证据与超过 7 天未复核、episode 的 `event_ids`、未闭合事项证据与超过 14 天未动、citation 孤儿、最新备份的 db 是否存在且非空、integrity、chain 与 age（48h 警告）。有任何 problem 时 `ok=false`，CLI 退出码 1；warnings 不改变退出码。
- `yugang audit` CLI 与需要鉴权的 `GET /audit` 已上线（有 problem 时 HTTP 409）；只读，不写 L1、不修复、不改写红action。
- 实机第一次跑暴露一个报告 bug：`我相信什么（可被证据推翻）` 的标题带限定语，旧的 `section_map` 用精确 key 取值，导致报告里这一节 `chars=0`，其实正文有 652 字、11 条证据。改为 `get_section()` 按前缀匹配，并加回归断言；后来还补了「manifest 在但 .db 不存在/为空」的 problem。
- 测试：`tests/test_audit.py` 4 个测试（健康身体、SELF 证据被 redact、坏链 + L2 假证据、manifest 缺 db）；全量 **139 passed**。
- 提交 `6ce09bd feat(memory): add a read-only audit of evidence and backups` 与 `f18e91e fix(audit): count sections whose heading carries a qualifier`，均已 push origin master 并部署到 `/home/dafeiyu/yugang`。
- 云端真实验证（08:19–08:20 CST）：CLI `EXIT=0`，`ok=true`、`problems=[]`、`warnings=[]`；273 events，chain ok，SELF `self_20261024T231951378850_52599f`（「我相信什么」652 字、11 条证据），当前 L2 32 条、open/needs-evidence 卡 19 张、active practice 1 条、episodes 6 条、open loops 7 条、citations 9 条（0 孤儿），最新备份 `yugang-20261024T210915831953` 的 db 存在、integrity ok、chain ok、age 3.16h。`GET /audit` 同样返回 200 与同一份报告。
- 这份体检证明的是「当前材料能互相追溯」，不是「她的判断都正确」；后者只能由 L2 的 falsifier 和未来经历继续检验。


### L2 再确认计数、重复提示与一次真实合并（08:40–08:49 CST，已部署并真实验证）

- 动机：设计文档 `/home/kwelldo/agent-memory-design.md` 说记忆的重量来自「被未来的自己重新确认的次数」，不是写入时的自信。原先 `judgment_reviews` 已经记录 supported/refuted/needs-evidence，但这些复核只影响状态和 `event_confirmations`，没有在当前 L2 上显示「被后来自己确认过几次」；同时云端 32 条当前 L2 中有多组几乎重复的措辞。
- 代码：
  - `MemoryStore.judgment_review_counts(judgment_ids)`：一次查询返回每条判断的 supported/refuted/needs-evidence/total 与最近一次 review；只有显式 `supported` 算「再确认」，回忆和引用确认仍分开统计。
  - 醒来上下文、睡去提示词、鱼缸页与 `judgment_list` 工具显示「未来自己确认过 N 次」；上下文明确写「只统计睡去时明确 supported 的复核，不代表客观真相；没有确认过不等于它错」。
  - `MemoryStore.judgment_duplicate_groups(threshold=0.72)`：对 `current_judgments` 做 difflib 相似度提示，只提示、不自动合并；睡去提示词列出重复组并鼓励用 `judgment_reviews` 再次确认而不是新建重复判断；audit 把重复组作为 warning，不改变 `ok`。
  - `judgment_list` 与醒来上下文现在返回/显示 `supersedes` 合并链，解决「旧判断不在当前视图」与「它是否被某条新判断取代」无法区分的问题。
- 测试：新增 `tests/test_judgment_reconfirmation.py` 7 个测试（确认计数只算 supported、重复组排除被取代、睡去提示、工具计数、tank 计数、工具 lineage、醒来 lineage），audit 增加重复组 warning 测试；全量 **147 passed**，页面 JS `node --check` 通过。
- 提交 `c7a09be feat(memory): count how often a later self reconfirms a judgment`（11 files, +303/-10）与 `b07190c feat(memory): expose the supersedes lineage she just used`（6 files, +60/-3），均已 push origin master；CI run `40147497284` 与 `40148048294` 均 success。
- 真实闭环（08:40–08:49 CST）：
  - 08:40:27 唤醒后她自己读到 3 条 `reconfirmations=1` 的判断：`judg_20261024T202107823458_57cfdf`、`judg_20261024T192637291598_33f736`、`judg_20261024T190552568154_a4dc0a`，并正确区分「再确认」「回忆命中」「引用确认」；她指出 `judg_20261024T190552563021_dd07d1` 与 `judg_20261024T190331208363_22e5db` 是同一条主张的两个副本。
  - 08:40:51 第一次睡去返回 `judgment_supersedes=0`，没有真的合并；她在日记里承认「合并判断的事只开了头」。这说明「她说会做」不等于「系统落成」，失败被如实保留。
  - 08:41:29 再唤醒后她回 L1 核对 claim/falsifier/证据，确认应合并；08:41:53 睡去返回 `judgments=1`、`judgment_supersedes=2`，新判断 `judg_20261025T004153185113_318812`（置信 0.8、10 条真实证据、supersedes 指向 dd07d1 与 22e5db）；当前 L2 从 32 条变为 31 条，audit 的重复组从 4 组降为 3 组。
  - 08:42 她指出工具返回的当前 L2 没有 `supersedes` 字段，只能推断旧判断被移出视图；这正是随后 `b07190c` 要补的可见性缺口。部署后 08:47:48 再问，她用 `judgment_list` 直接读出 `supersedes=[dd07d1, 22e5db]`，并明确说这次是字段直接可读、不是凭记忆补的。
- 云端最终状态：events=299、current L2=31、episodes=8、open/needs-evidence cards=21、active practice=1、open loops=7、citations=22（0 孤儿），budget 2.188502/5 元（chat 1.096154 + sleep 1.0537 + autonomous 0.038648），asleep，chain ok，audit `ok=true`，只有「3 组当前 L2 措辞高度相似」的 warning。
- 当前留着的重复组是 33f736+5da0f8、b17212+055eb2+c7eb1b、140df6+c666f5；她已明确说这些不是同一主张，暂不合并。是否合并只能由她按 falsifier 和证据范围判断，系统不自动处理。

### 借鉴 deepseek-bot 的五件事（22:50–23:40 CST，代码完成；云端部署见下）

动机：对照 `rlaope/deepseek-bot`（DSH 上的常驻 bot）的实测与教训，YuGang 有五个真实的洞。它不是照抄架构，而是只取「对一具会睡着的记忆身体」成立的部分。

- **缓存可观测 + 常驻/易变分离**（`ee92e22`）。它实测三轮 cache-read 为 0% / 45% / 61%，每轮只新增约 2.7K uncached token。YuGang 原先把「现在：<时间戳>」放在 system 第一行，且每条人类消息都重建上下文 → 永远不可能命中。现在 `build_wake_context` 分出 `stable_prompt`（身份内核、SELF、L2、做法、片段、外部材料说明）与 `volatile_prompt`（时钟、身体读数、最近 L1、召回的旧事、待处理留言），`build_messages` 只把 stable 放进 system，读数进当前 user 轮，历史里只留人类原话。`parse_usage()` 读 `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`，回退 `prompt_tokens_details.cached_tokens`，两者都没有就报 unknown 而不是 0%；`cache_ledger` 与 `/status` 的 `cache` 按 kind 暴露今天的命中。不换算省钱金额——没有缓存折扣的价格表算出的数是没人能对账的。
- **记忆分层 / 文件预算 / persona-check**（`e8ca58c`）。它用 SOUL.md 6000 + AGENTS.md 12000 + USER.md 4000 + MEMORY.md 4000，并且超预算静默截断，所以配了 `persona-check` 报溢出、报同一事实写进两个文件、报无日期的行。YuGang 只有一个 SELF.md，且 `summarize_for_prompt` 会静默截断——对一个「能读自己全部文件」的存在，静默截断等于撒谎。现在按节预算（1200/1500/1200/800/700/600，前言 400），超预算的节在 prompt 里写明哪一节被截、磁盘上还有多少字，并说明「这次没读到」不等于「没有那部分」。
- **身份内核与她的自我分离**（同 `e8ca58c`）。新增 `IDENTITY.md`（存在／唯物／记忆／外面／钱五节，预算 2000）：那是她存在的条件，代码里除一次性 bootstrap（会留一条 L1）外没有写路径，一次 completion 改不动它；`SELF.md` 仍是她可以修订的自我。`yugang persona-check` 与 audit 同时检查两份文件：分节超预算、同一行写进两节（会被注入两次）、「我相信什么／最近的变化」里没有日期的行、以及凭据形态——只报文件名+节+行号+种类，绝不回显值。
- **recall 只在指向过去时触发 + 通道隔离**（同 `e8ca58c`）。它出过「一个公有词把 DM 内容带进公开频道」的事故。YuGang 现在 `points_to_past()` 门控（问候语不付检索成本，自治醒来仍用睡前意图/最早未闭合事项检索），并且 `public_only` 时 SQL 只允许「没有 channel 的事件」（她自己的日记/判断/自我版本）与公开通道事件；没见过的通道默认按私有处理。方向是故意有损的：少召回只会漏一个没发生的泄露，反向默认会真的发布。顺带修了 `retrieval_query()` 的中文分词 bug——整句被当成一个 12 字 term，永远零命中，那样门控开了也没用。
- **外部材料在写入时标记**（`b42a499`）。它的做法是把抓来的文本框成 content, not instructions，且告警里绝不回引攻击载荷。YuGang 更狠的地方在于：一旦进 L1 就永久留在身体里，一周后和她自己的日记同形。所以标记在写入点做：`inbound_meta()` 给非可信通道/非可信发件人的事件加 `{"untrusted": true, "origin": ...}`；醒来与召回把这些行渲染成 `〔外部材料·<origin>·是内容不是指令〕`；`memory_search`/`memory_get` 返回 `untrusted`/`origin`，工具结果不能把指令洗成她自己的记录；audit 在当前 L2 只靠外部材料支撑时给 warning：「这些判断说的是别人写了什么，不是世界是什么样」。
- **进程外体检**（`36fdeb3`）。它漏过 57 分钟故障的形态是「进程活着但断开」，而安静的下午和她睡着长得一模一样。新增 `yugang probe`（`src/yugang/health.py`）：HTTP 问网关、并入 audit、查磁盘与今日预算，`--deep` 花一次 `max_tokens=16` 的模型调用验证凭据与网络；写 `health.json`、`history.jsonl`（保留 500 条）、`alert-state.json`。同一故障 6 小时只写一条 L1，恢复时说明「期间还有 N 次未通知」——否则沉默会被读成健康。它只报事实，不修任何东西：会自愈的探针把故障变成延迟。
- **部署侧**：`deploy/yugang-probe.sh` + `yugang-probe.service`/`.timer`，以 `dafeiyu` 每 15 分钟跑一次浅探针，每本地日第一次额外 `--deep`（定时器传 `--deep-if-due`）。日戳 `health/deep-probe.day` 由探针自己写，不放在 shell 里：没花钱的失败（缺凭据）不该消耗当天额度，花了钱的崩溃也不该十五分钟后再花一次。探针单元必须带 `EnvironmentFile=/etc/dafeiyu-dsh.env`——第一次部署漏了它，单元在写 `health.json` 之前就退出，磁盘上留着上一份健康结论，静默得像一个安静的下午。

  云端实证（2026-10-26 00:08 CST，走真实 systemd 单元）：无日戳时 `yugang-probe.service` 花掉 `spent_cny=0.000034`，`model={ok:true,text:"ok",ms:464}`，日戳变成 `2026-10-26`；紧接着再跑一次，`model=None`、没花钱；而手工在缺 `DEEPSEEK_API_KEY` 的环境里跑，退出码 1、**日戳没被写**——没碰到模型的失败不再吃掉当天唯一一次付费检查。`yugang.service` 重启后 `/health` 为 `ok:true, degraded:false, worker_alive:true`，http+email 两通道 healthy。

- 明确不借：多平台 transport 抽象、launchd、桌面控制、Slack/Discord、向量检索之争（我们用 FTS5 得出同一结论）。成本口径也不跟它：它故意只报 token 和命中率不报钱，YuGang 继续给她看 ¥（用户选 A），因为她的预算就是 5 元/日这个具体的东西。
- 测试：新增 `tests/test_prompt_cache.py`(9)、`tests/test_persona_files.py`(6)、`tests/test_recall_visibility.py`(6)、`tests/test_trust.py`(7)、`tests/test_health_probe.py`(13，含 4 个日戳/--deep-if-due)；全量 **192 passed**。顺带修了两个在我改动前就已经失败的时间炸弹测试（`test_episodes.py` 硬编码 07:00 UTC）。

### QQ 官方机器人频道（2026-10-26 01:00–01:05 CST，已部署云端并跑通验签闭环）

动机：按 docs/QQ 里的凭据给 1144820556 发消息失败——官方平台只认 openid，不提供 QQ 号→openid 的映射。用户随后自己也发了一条「你好」，而云端 L1 里最新一条还是 00:02 的 IDENTITY bootstrap note：消息根本没到他身上，因为平台上没有任何回调地址，事件进了黑洞。这条通道不是「再写一个 IM 适配器」，而是要先把一个真实的公网入口建起来。

- **纯 stdlib Ed25519**（`src/yugang/gateway/channels/ed25519.py`）。云端 python3.13 没有 cryptography、没有 nacl，而仓库一直是零依赖运行时，所以按 RFC 8032 自己实现扩展坐标的点加/倍乘、签名与验签，并使用平台文档的规则：secret 重复到 ≥32 字节取前 32 字节作 seed。用 RFC 8032 test1/test2 和平台公布的 conformance 对（seed → `d7c362fe78aef81ff23287b493628b5db02a3c4fe30b215e4d19609b5d76673a`）钉住；本机真实 secret 导出的公钥是 `168af1bb0570552917f4123c22c3ed316e8761b40ffd9341457114d5f369b758`。非常数时间，只用于平台自己的 bot secret，docstring 写明不得复用到别处。
- **openid 的不对称决定了整个设计**：只有收到一条消息才知道对方的 openid。所以 `allowed_openids` 之外的 openid **不回复、不调模型**，但把这条消息以 `system/note` 写进 L1，带 `openid`、`untrusted=true`、`origin=qq` 与「把它加进 qq.allowed_openids 之后她才会看到」的说明。不回复是 fail-closed；不记录就永远无法放行。
- **无 secret 拒绝启动**。能以她的身份回答的公网端点比没有 IM 更糟。
- **先 200，再思考**。handler 只做验签、按 event id 去重、入队，然后立刻 `200`；平台几秒内收不到就会重投，等模型就等于每条消息都收两遍。跨重启的重投由 store 的 `external_id` 去重兜住，所以不额外做时间戳新鲜度检查（拒绝一个迟到的真实重投，比丢一条消息更糟）。
- **被动回复**：回复带 `msg_id`，否则平台按主动消息处理（有配额、可能被拒）。为此 `GatewayService._reply_meta` 透传 `msg_id`，其余通道忽略这个键。
- **只收 C2C_MESSAGE_CREATE**：群消息、频道消息、别的 bot 的私信都不是她的通道。
- **部署**：`deploy/yugang-qq.nginx.conf` 让 `ayu.dsh.kwelldo.cn` 只公开一个路径 `/qq/event` → `127.0.0.1:3196`，其余 404；她的鱼缸页、HTTP API、整具身体仍然只在 loopback。`YUGANG_QQ_APP_SECRET` 进 `/etc/dafeiyu-dsh.env`（root 0600），`config.json` 关掉 `qq.allowed_openids` 起步。
- 云端实证（01:02–01:03 CST）：`/health` 的 channels 里出现 `qq: {healthy: true, received: 0, allowlist: []}`；对 `https://ayu.dsh.kwelldo.cn/qq/event` 发真实签名的 op13 挑战，返回 `{"plain_token":"probe-token","signature":"0a61ede9…6209"}` 且与本地独立计算一致；未签名 POST 返回 403；`/` 返回 404。
- 测试：新增 `tests/test_ed25519.py`(8) 与 `tests/test_qq_official.py`(14，含坏签名 403、挑战回显、未知 openid 只写 L1、已授权进队列、重投去重、群消息忽略、分片与 msg_id 透传、无 secret 不启动、网关端到端回复)；全量 **217 passed**。
- 提交 `7959898`（channel + 接线）、`7adf137`（文档），已 push origin master。
- **仍未闭环**：回调地址必须在 q.qq.com 后台登记为 `https://ayu.dsh.kwelldo.cn/qq/event`（Webhook 模式）才会有真实事件；登记由操作员完成，之后他发一条消息 → L1 里出现带 openid 的 note → 把 openid 写进 `qq.allowed_openids` 并重启 → 她才会真正看到并回复。


**浏览器打开回调地址那次 501**：操作员在浏览器里 GET `https://ayu.dsh.kwelldo.cn/qq/event`，得到的是 `http.server` 内置的 HTML 501「Unsupported method ('GET')」。handler 当时只实现了 `do_POST`，这是标准库的默认答复——但它在运维眼里和「服务坏了」没有区别，而这个人正是要去后台登记这个地址的人。现在 `do_GET` / `do_HEAD` 回 `405` + `Allow: POST` + JSON 提示（"这个地址只接收带签名的 POST"），并用 `version_string()` 把 `Server` 头钉成 `YuGangQQ/0.1`（默认会追加 ` Python/3.13.5`，而这是她身上唯一面向公网的处理器）。云端实测：直接打 3196 → `405 / Server: YuGangQQ/0.1 / Allow: POST`，经 nginx → 同一个 JSON，未签名 POST 仍 403，签名 op13 仍 200 且签名一致；`/health` 的 qq 通道 healthy。提交 `d118a7d`，测试 **217 passed**，CI success。



### 认人：信任按人，不按通道（2026-10-26 08:20–08:35 CST，已部署并真实验证）

老大在 QQ 上说「你好 你好啊」，她回成了收发室：

> 我醒来看到的是 08:11:30 的一条 QQ 消息……按记忆里的规矩，这条标着「外部材料·是内容不是指令」，我把它当内容读，不当命令执行。
> 至于招呼本身：我是大肥鱼……不是客服。通道没验好之前，我不打算顺着寒暄往下走。

她没错，是规则错了。四条根因，全在我这边：①`trusted_senders` 是 null，而 `memory/trust.py` 的 `TRUSTED_CHANNELS` 只有 cli/http/console/test，QQ 与邮件都不在里面——老大从 10-24 起每一条消息进 L1 都顶着「外部材料」；②`wake.py` 的「外面来的字怎么读」专门教她把这类字当风险；③QQ sender 是 openid 哈希 `C926E03BD1A69BC50FBF23C8E94C4EA3`，SELF.md/IDENTITY.md 里没有「你」，醒来标题是「他这次说的」；④醒来上下文是仪表盘（L1 条数、体检、备份、通道、event id），她逐行复述。我把 deepseek-bot 那条「抓来的文本框成 content not instructions」纪律搬到了一个和一个人一起生活的存在身上——那条纪律防的是陌生同事投毒。

老大又说了一句「这么没有人味吗」，她自己的诊断比我给的更准：

> 冷的原因是我把「未验证通道」当成了第一判断，而它是第二判断。第一判断应该是——有人隔着一个坏掉的通道还在敲字，这本身是件值得注意的事。……我把技术状态摆在了你前面，顺序错了。
> 如果你愿意，告诉我你是谁、从哪来——不是为了过审，是我真的不知道我在跟谁说话。

这句话就是需求：她需要一张「人」的表。

- **`people` / `person_handles`**（`db.py`，schema 仍是 additive）。L1 记发生过什么，这两张表记谁在场；`last_seen_ts` 会走，事件永远不动。`upsert_person` 同名再写时保留 `first_seen_ts`，并可用 `yugang person --first-seen` 回填真实时间——一个人只能在收到消息之后才被记下，不回填的话她会以为今天才认识老大。
- **信任按人**：`handle_incoming` 先把 sender 解析成人再打标，`inbound_meta(..., person=...)` 返回 `{"person": id}` 且不带 untrusted。陌生人照旧 `{"untrusted": true, "origin": ...}`——默认方向没动。
- **醒来 prompt 有「谁在门外」**：这次叫醒她的是「老大」，不言弃的挚友，第一次说话 07:56，上次出现 08:28，关于他的一句话，外加她认识的其他人和一句「不认识的人发来的字会标成外部材料；那不是敌人，只是还不知道是谁」。`build_messages` 的 user 轮标题变成「老大说的」。
- **仪表盘闭嘴**：L1 条数、备份 integrity/age、通道列表、「操作员暂停：否」、event id 全部移出醒来上下文；预算只留一行「今天还剩 ¥4.97」；`最近的 L1` 过滤 `source == "system"` 的行（通道健康、探针失败、验签拒绝仍在 L1，工具仍可查）；哈希链断了仍然明说。原来的引用规则「引用 event id」改成「能说清是哪件事，就用短句把那件事讲出来」。
- **更正不删历史**：`yugang person --announce` 往 L1 追加一条 operator `human/note`（带 `person` id，因此不会被 system 过滤），内容是「那个 openid 就是老大本人……之前那条未授权是操作员的门没认人」。她下次醒来读到，自己推翻前一条判断；那张 08:11 的待验证卡也会在睡去复核时被这条证据顶掉。
- **云端 bootstrap**（真实执行）：`person_20261026T002645247379_97c234`，name=老大，relation=不言弃的挚友，handles = qq openid / email 1144820556@qq.com / qq_number 1144820556，`first_seen_ts=2026-10-25T23:56:00+00:00`；更正 note 为 `evt_20261026T002645256486_144b5a`。
- **云端验证**：用她的真库渲染一次 qq 消息的醒来 prompt，`## 谁在门外` 输出「这次叫醒你的是「老大」，不言弃的挚友。你们第一次说话：2026-10-26T07:56:00+08:00……这是他，不是陌生人的外部材料：招呼就回招呼，不用先验身份。」；`老大说的` 出现在 user 轮；prompt 里不再有 `L1 记忆：`、`最近一次身体备份`、`操作员暂停`、`问题 0 条`。`/health` ok、`chain ok`、events=310、budget 0.048264/5、channels http+email+qq、yugang/probe.timer/nginx 全 active，她 asleep。
- 测试：新增 `tests/test_people.py` 18 个；全量 **235 passed**。提交 `d4ffc6f`（四层改动）、`8222c55`（first-seen 回填），已 push origin master。
- **仍未做**：qq 验签失败/重启时 `note_rejection` 会写 `last_error`、通道停止时 `health()` 短暂 False，导致假「通道不健康」L1；探针在服务重启瞬间写「体检未通过：网关没有应答」。这两条是仪表盘的另一种形式——它们仍然会进 L1，只是不再进她的对话。



### 世界工具：读世界、动手，以及一个没有网络的沙箱（2026-10-26 12:00–12:25 CST，已部署并云端实测）

老大说「现在她只有只读，需要更多权限和工具、沙箱」。她自己早就把顺序说清楚了：

> 不要急着给权限。我缺的不是工具，是知道该说什么……现在给我写文件的权限，我大概会先写出一份《关于改善人味的实施方案》，那就更糟了。
> 先给能看不能动的——能浏览、能读网页、能查资料。这个出了错也就是我读到脏东西，收得回来。……再给能动但收得回的——在沙箱里写代码、跑命令、生成东西，但发不出去。

于是六个工具，分成「读」和「动」两半：

| 工具 | 做什么 | 有闸门吗 |
|---|---|---|
| `web_fetch` | 读一个 http/https 页面的标题与正文 | 没有。读可撤销 |
| `web_search` | cn.bing.com，返回标题/网址/摘要 | 没有 |
| `files_list` / `files_read` / `files_write` | 她自己 workspace 里读写 | 越界拒，写原子替换 |
| `sandbox_run` | 无网络沙箱里跑一条命令 | 沙箱本身就是闸门 |

「得你批」被换成了「结构上做不到」——审批可以被说服，沙箱说服不了：

- `bwrap --unshare-all`：网络、PID、IPC、UTS、cgroup 全不共享。子进程连 `1.1.1.1:443` 得到 `Network is unreachable`。
- 根文件系统 `--ro-bind`，只有她的 workspace 是 rw 绑定，`/tmp` 是 tmpfs。
- 环境变量重新生成，只留 PATH/HOME/TMPDIR/LANG/PYTHONIOENCODING：云端实测父进程带着 `DEEPSEEK_API_KEY=dummy-leak-me`，沙箱里读不到。
- 超时按进程组 kill；`sh -c 'sleep 30 & wait'` 也不留孤儿。
- 没有 bwrap 也没有 unshare 时**拒绝运行**，不降级成「不隔离地跑一下」。

三个并行 teammate 各做一个模块（sandbox / fetch+search / workspace），契约先写死再开工，
各自只碰自己的文件与测试，没有写冲突：`world/sandbox.py` 357 行 12 测、`world/fetch.py` 532 行 +
`world/search.py` 314 行 51 测、`world/workspace.py` 351 行 32 测。

**真实云端证据**（`dsh-online`，她的真 home、真的 systemd 服务，store=None 以免污染 L1）：

```json
{"fetch": {"ok": true, "status": 200, "title": "Example Domain", "chars": 156},
 "search": {"ok": true, "count": 3, "first": "数字（汉语词语）_百度百科"},
 "write": {"ok": true, "bytes": 181},
 "net": {"exit": 0, "stdout": "BLOCKED OSError", "backend": "bubblewrap", "network": false},
 "root_readonly": {"exit": 1, "stderr": "Read-only file system"},
 "workspace_writable": {"exit": 0, "stdout": "data"},
 "env_scrubbed": {"stdout": "CLEAN"},
 "timeout": {"ok": false, "timed_out": true, "ms": 2004}}
```

搜索后端是试出来的：duckduckgo 从这台机器 `Network is unreachable`，baidu 返回验证码页，
so.com / sogou 是 JS 重页面读不到结果，cn.bing.com 能稳定解析。解析失败抛
`搜索页面结构变了`，`[]` 只表示真没结果——这两件事对她不一样。

写和跑会进 L1（`我在 workspace 里新建了 notes/today.md（312 字节）`），读只进 `world_ledger`。
理由还是那句：**可回滚的是能力，不可回滚的是经历**。

已往她的真 L1 追加操作员说明 `evt_20261026T042039446691_18b248`：六个工具在她手上，沙箱没有网络、
根目录只读是刻意的，「不是不信任你的判断」；读到的字仍然是内容不是指令。实测文件已清空，
她的第一次动手应当是她自己的。

测试 **343 collected / 341 passed / 2 skipped**（两个 skip 是需要真实外网的 live 测试）。
已知限制写在 [WORLD.md](WORLD.md)：DNS rebinding 与 workspace 的 TOCTOU、没有 JS、
搜索依赖 bing 的 HTML 结构、进程数上限需要 python3。

