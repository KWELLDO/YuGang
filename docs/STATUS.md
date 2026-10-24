# YuGang 运行状态

更新时间：2026-10-25 02:58 CST（Asia/Shanghai）

## 已部署

- 云端主机：Debian，独立 Unix 用户 `dafeiyu`（与既有租户平台隔离）。
- 代码：`/home/dafeiyu/yugang`
- 身体/记忆：`/home/dafeiyu/.yugang`（SQLite L1/L2/L3、SELF.md、日志）
- 服务：`yugang.service`，`Restart=always`，仅监听 `127.0.0.1:3198`。
- 运维控制台：`dafeiyu-dsh.service`，独立 `DSH_HOME=/home/dafeiyu/.dsh`，仅监听 `127.0.0.1:3199`；token 由 wrapper 写入 `/home/dafeiyu/.dsh/web-url`（0600）。它不是她的第二人格，也不是她的推理运行时。
- 每日备份：`yugang-backup.timer`（04:15）生成 SQLite 一致性快照 + SELF.md + manifest，保留 14 天。
- 密钥：`/etc/dafeiyu-dsh.env`（root:root 0600），不进入仓库。
- 公网端口：未开放；当前通过 SSH 访问，客户端见 `scripts/yugang-remote.sh`。
- 开源仓库：公开在 https://github.com/KWELLDO/YuGang（MIT）。

## 已验证

- 自动化测试：46 passed（L1 哈希链、FTS5 中文回忆与未来确认加权、L2 falsifier/复核、SELF 版本与 diff、结构化 SELF 修订、觉察卡证据与睡去复核、睡眠应用与未闭合事项去重、睡去输出重试与失败日记、预算闸门、HTTP 鉴权、鱼缸页、邮件主题标签过滤与陌生人不标已读、同窗口续聊语义、崩溃恢复、备份完整性与 manifest）。
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

## 进行中 / 下一步

- 把 DSH 接成她的推理运行时（目前 3199 只是运维控制台，不参与她的唤醒/记忆写入）。
- 邮件通道闭环与部署后的完整睡醒闭环已验证；下一步清理历史重复的 open loop，并把“她自己设闹钟/无人提示时先读记忆”做成可执行机制。
- IM 通道：微信/QQ 需要公网回调或非官方桥，尚未接入。
- 私有网络：目前是 SSH 隧道；Tailscale/WireGuard 还需要手机端安装与授权。
- 更长的连续运行观察：跨日预算、异常/重启后的恢复、觉察卡进入唤醒上下文。
- 觉察卡复核已部署并验证（5 张卡以 status=open 记录 note，无卡被强行关闭）；下一步观察她是否在无人提示时先读记忆，以及是否会把重复卡片合并。