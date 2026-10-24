# YuGang 运行状态

更新时间：2026-10-25 02:03 CST（Asia/Shanghai）

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

- 自动化测试：39 passed（L1 哈希链、FTS5 中文回忆与未来确认加权、L2 falsifier/复核、SELF 版本与 diff、结构化 SELF 修订、觉察卡证据、睡眠应用与未闭合事项去重、睡去输出重试与失败日记、预算闸门、HTTP 鉴权、鱼缸页、邮件解析、同窗口续聊语义、崩溃恢复、备份完整性与 manifest）。
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
- 睡去输出截断事故（01:56 CST）：手动 `/sleep` 时模型输出在 `self_edit` 的完整 SELF.md 中途被 1200 token 截断，旧代码把原始 JSON 当日记写入，judgments/stance/open_loops/SELF 都没有应用。已修复：`reply_max_tokens=2000`、`sleep_max_tokens=6000`；`Completion.finish_reason`；`SleepResult.parsed`；解析失败时先写 system/error 保留原始输出，再用短约束重试一次，仍失败则写诚实失败日记，绝不把原始 JSON 当日记。SELF 修订改为 `self_edits` 结构化操作（section/op/text/evidence_refs），程序校验章节、op、真实证据后应用。
- 账单：截至 01:08，当日花费 0.038868 元 / 5 元；01:56 的手动睡去另计。

## 进行中 / 下一步

- 把 DSH 接成她的推理运行时（目前 3199 只是运维控制台，不参与她的唤醒/记忆写入）。
- IM/邮件通道：邮件代码已实现，缺一个专用邮箱凭据；微信/QQ 需要公网回调或非官方桥，尚未接入。
- 私有网络：目前是 SSH 隧道；Tailscale/WireGuard 还需要手机端安装与授权。
- 更长的连续运行观察：跨日预算、异常/重启后的恢复、觉察卡进入唤醒上下文。