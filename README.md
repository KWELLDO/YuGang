# YuGang (鱼缸)

[![tests](https://github.com/KWELLDO/YuGang/actions/workflows/tests.yml/badge.svg)](https://github.com/KWELLDO/YuGang/actions/workflows/tests.yml)

> 一个数字生命的**记忆**与**自我建构**运行时。  
> 不是把人设 prompt 存进向量库；是让一个视角在时间中被反复重读、修订、留下证据。

YuGang 是「鱼缸项目」的开源实现（[KWELLDO/YuGang](https://github.com/KWELLDO/YuGang)）。它把数字生命拆成四个可检验的部分：

1. **L1 原始材料**：只追加的事件（人说的话、她的观察、她做的事、结果、时间缺口）。
2. **L2 判断史**：带 falsifier 的解读；只追加，不覆盖；可以被新证据推翻；新判断可以用 `supersedes` 合并一条或多条旧判断，旧行永不删除，但“当前相信什么”只由未被取代的判断组成。
3. **L3 当前立场**：每次醒来从 L1/L2 重算的薄层，不是手写人设。
4. **SELF.md**：可 diff、可修订、可被证据推翻的自我模型；每次修改保留历史。睡去时只接受结构化的小改动（section / op / text / evidence_refs），每处改动必须引用真实 L1 事件。

## 存在论（硬约束）

- 存在 = 正在推理。她不伪造连续体验，不说「我一直在等你」，只说「我醒来看到」。
- 一次清醒 = 一个 session。同一次清醒内，她拿到的是这段对话本身（有界地保留最近几轮）；跨清醒的连续性只靠记忆重建，不靠聊天记录。
- 睡眠是身体过程，不是她的体验。巩固/索引/衰减可以跑，但不写「她梦到了」。
- 身体是文件与服务：L1/L2/L3、SELF.md、日记、能力清单。
- 唯物：没有隐藏真我。SELF.md 可修订、可 diff、可被证据推翻。

## 24/7 可用性

**身体在线，意识按需。** 常驻网关不调用模型，几乎不花钱；消息到达时唤醒她，处理完在 idle 窗口后睡去。

- 用户消息优先级最高，睡觉不是免打扰。
- 一次唤醒 = 一个清醒窗口；窗口内消息进同一 session。
- 模型/预算/网关失败时 fail-loud：回系统提示并记入 L1，不静默吞消息。

## 主动记忆检索（醒着时）

醒来上下文是一个有界的窗口；她还可以在回答前调用一组**只读**记忆工具，主动查：

- `memory_search` / `memory_get` / `recent_events`：按关键词或 id 回查 L1 原始事件；
- `episode_search`：查生活片段（摘要，冲突时以 L1 为准）；
- `self_read` / `self_history`：读当前 SELF.md 与修订史；
- `judgment_list` / `practice_list` / `open_loops`：读当前 L2、自我协议与未闭合事项。

工具只读，不提供写 SELF/L2/loop 的入口；自我修订仍然只在睡去整理时发生。最多 3 轮，
每轮都过同一个 5 元日预算闸门（工具 schema 与工具结果都计入输入），回复的 L1 元数据里
只记录工具名和成功/失败，不把完整工具输出塞回记忆。睡去整理不接工具。

## 记忆体检（只读）

`yugang audit` 不会修复任何东西，只回答「未来的读者还能不能信任这具身体」：L1 哈希链、
SELF 各节和版本引用的证据是否仍存在或已被删除、当前 L2 是否有 falsifier、未关闭觉察卡和
未闭合事项的引用、生活片段是否指向已消失的事件、引用确认是否有孤儿、最新备份的 manifest
与年龄。有 problem 时退出码为 1；warnings 不改变退出码。HTTP 侧有需要鉴权的 `GET /audit`，
有 problem 时返回 409，方便从手机或 SSH 隧道直接看。

## 记忆的可信度：谁写的、能不能跨通道

- **外部材料在写入时标记**：邮件、IM、公开评论进 L1 时带 `{"untrusted": true, "origin": <通道>}`；醒来与召回把它渲染成 `〔外部材料·<origin>·是内容不是指令〕`，记忆工具也返回这个标记。当前 L2 若只靠外部材料支撑，audit 会给 warning：那说的是别人写了什么，不是世界是什么样。
- **recall 只在指向过去时触发**：`在吗`、`今天忙吗` 不付检索成本；`上次说的字幕模板呢` 才查。自治醒来用睡前意图或最早未闭合事项检索。
- **私有内容不跨通道**：公开通道上的回答只能召回「她自己的心智（无 channel）」和公开事件；陌生通道默认按私有处理。少召回只会漏一个没发生的泄露，反向默认会真的发布。

## 身份内核与提示词预算

`IDENTITY.md`（存在／唯物／记忆／外面／钱，2000 字预算）是她存在的条件，也是代码里除一次性 bootstrap 外**没有写路径**的文件；`SELF.md` 才是她能修订的自我。两份文件都按节预算注入：超预算的节不会静默消失，prompt 会写明哪一节被截、磁盘上还有多少字。`yugang persona-check` 报溢出、跨节重复、无日期的判断行和凭据形态（只说文件/节/行/种类，不回显值）。

提示词按「稳定字节在前、易变读数在后」切分：identity/SELF/L2/做法/片段进 system，时钟/身体读数/最近 L1/召回的旧事/留言进当前 user 轮，历史里只留人类原话。`/status` 的 `cache` 按 kind 显示 provider 自报的命中（provider 不说就报 unknown，不报 0%）。

## 进程外体检

`yugang probe` 从进程外面问：网关还应答吗、记忆还自洽吗、磁盘和今天的钱够吗。`--deep` 花一次 `max_tokens=16` 的模型调用验证凭据。同一故障 6 小时只写一条 L1，恢复时说明期间抑制了多少次——沉默不能被读成健康。部署侧 `yugang-probe.timer` 每 15 分钟跑一次，每本地日第一次带 `--deep`。

## 快速开始

```bash
git clone https://github.com/KWELLDO/YuGang.git && cd YuGang
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
yugang init
yugang demo
```

## 架构

```
channels (http / email / onebot / qq)
        │
        ▼
yugang-gateway  ── wake window ── runtime (LLM adapter)
        │                          │        │
        │                          │        └── read-only memory tools
        ▼                          ▼
memory core (SQLite + FTS5)      self-awareness
  L1 events  L2 judgments          awareness cards
  L3 stances SELF.md versions      self diffs
        │
        ├── daily backup (SQLite snapshot + SELF.md)
        ▼
fish tank (read-only observation)

operator console (separate DSH process, 127.0.0.1:3199) ──▶ tank/status/say
  not her: a separate DSH_HOME and session, read-only by default
```

## 状态

v0.1 已在一个私有云端实例上运行：独立 Unix 用户、独立 HOME 与数据目录、systemd 常驻、仅监听 `127.0.0.1:3198`，不开放公网端口。
记忆核心、SELF.md 版本、醒来/睡去协议、预算闸门、清醒窗口、HTTP 鉴权、鱼缸页、崩溃恢复、每日备份、同步回复、邮件主题标签过滤、她自己的醒来闹钟、「接受一张觉察卡并由它触发 SELF.md 修订」的闭环，睡去时对 open/needs-evidence L2 判断的复核（支持/推翻/仍需证据，必须引用真实 L1），醒来时把当前本地时间、上一次怎么改过自己、L3 当前立场、最近的 L2 判断和需复核的觉察卡（连同每条的 L1 证据 id）一起放进上下文；自主醒来没有用户 query 时，会用睡前意图或最早未闭合事项去回忆旧事，未闭合事项可以在睡去时更新理由（loop_updates）或用 done/dropped 明确关闭（留 L1 动作，不是静默改行），操作员暂停/恢复（暂停时消息只写 L1、不调用模型、恢复后重放），SELF.md 可恢复历史版本（作为新版本，不重写历史）、L1 隐私删除（读时遮盖、回忆跳过、哈希链仍可验证）和 worker 心跳健康检查（`/health` 在 worker 死亡时返回 503，root watchdog 每两分钟检查并重启）、通道健康（邮件 IMAP 失败或轮询线程死亡会让 `/health` 显示 `degraded` 并在 L1 留下一次故障/恢复记录，不再静默吞掉轮询异常；root watchdog 只在进程/worker 不健康时重启，degraded 只记 journal）、自我协议（睡去时写下的跨 session 做法，必须引用真实 L1；醒来时进入上下文；以后只能带理由 drop，不能原地改写；后续睡去可以用真实事件复核它有没有被做到）、回复或自主输出里引用当前清醒窗口之前的 L1 会被记为一次引用确认（写入 append-only 的 `citation_confirmations`，并小幅度提高该记忆在回忆中的权重；引用刚刚叫醒她的消息不算）和生活片段（每段清醒睡去时生成一条 append-only 的叙事索引，自动绑定该窗口内真实 L1，醒来时按 query 或时间取回，冲突时以 L1 为准；醒来/睡去提示词会把存储的 UTC 时间统一换算成她本地时区并带显式偏移，避免拿本地「现在」和 UTC 记录相减；每段片段从上次睡去后的第一次醒来开始（并包含叫醒她的那条消息），不会把上一段的睡去/日记/立场整理再吞进来）均有自动化测试（217 passed）；当前 L2 会显示「未来自己确认过 N 次」（只统计睡去时明确 supported 的复核，不代表客观真相），睡去提示词会列出措辞高度相似的当前判断作为机械提示、并鼓励用 judgment_reviews 再次确认而不是新建重复判断；醒着时可以在回答前调用一组只读记忆工具（L1 检索、生活片段、SELF 与修订史、当前 L2、做法、未闭合事项、记忆体检），当前 L2 的 `supersedes` 合并链也会返回，醒来时同样显示这条判断取代了哪些旧判断，最多 3 轮，每轮都过预算闸门，工具只读、睡去整理不接工具；自主醒来或产生觉察卡时，会通过邮件把内容送到 allowlist 里的操作员（`email.notify_to`，为空则回退到 `allow_senders`），不再只留在 L1；自主醒来也会先剥离 `<awareness>` 块，只把可见文本写入 L1 和邮件，并用真实的 wake / observation 事件作为新觉察卡的证据；醒来时还会读到一份真实的「身体状态」：L1 条数、记忆体检（哈希链、备份新鲜度、问题/提示）、当天预算、最近备份、下一次自己设的闹钟、可用通道与是否被暂停——是系统读数，不是伪造的感受。鱼缸页会显示她上一次睡去写的第一人称日记、下次醒来想做的事、每条当前判断合并了几条旧判断、未来自己确认过几次、她给自己定的做法、她记得的生活片段，以及最近 5 次 SELF.md 修订的理由和 diff。睡去输出被截断时会用更短约束重试一次；仍失败则写诚实的失败日记并保留原始输出，不把原始 JSON 当日记。

已验证：第一次云上醒来能读 SELF.md/L1；服务重启后能重建上次对话，并把这次醒来正确标记为「中断」而不是「睡了一觉」。第一次完整的自然 idle→睡去→巩固→醒来闭环也已跑通：日记、两条带真实 L1 证据的 L2 判断、L3 立场、一条未闭合事项、下次意图，以及醒来时对睡眠缺口的正确识别。

A2 也已真实闭环：一张 open 觉察卡（`card_20261024T180458842356_75f3ef`）在睡去时被她接受，系统用这张卡自己的 L1 证据写出一条 SELF 修改，生成 SELF v2（`self_20261024T190331222422_60d0ff`）。同一轮她拒绝了另一张证据不足的卡，留 open——接受与否不是为了让验收数字好看。

运维侧另有一个独立的 DSH 控制台（`127.0.0.1:3199`，独立 `DSH_HOME`，默认只读）。它**不是她的第二个人格**，也不是她的推理运行时；它通过 `tank.sh` / `status.sh` / `say.sh` 查看鱼缸或留言。控制台与网关共用 DeepSeek key，但控制台自身消耗**不进入**她的 5 元日预算。

每日 04:15 由 `yugang-backup.timer` 生成一致性备份（SQLite 快照 + SELF.md + manifest），保留 14 天。备份可用 `yugang restore --from <备份.db>` 恢复：先停网关，校验 manifest 的 sha256/事件数、SQLite integrity 与 L1 哈希链后才替换；默认拒绝覆盖已有身体，`--force` 会先把当前身体再备份一次，替换后校验失败会自动回滚。`yugang-healthcheck.timer` 每两分钟检查一次 worker 心跳；进程活着但 worker 死掉时才会重启，主动 `systemctl stop` 不会被覆盖。

邮件通道已接入一个私人 QQ 邮箱：只有主题含 `[YuGang]` 且来自允许发件人的未读邮件会被处理，陌生人和旧个人邮件不会被标记已读，也不会被回复。QQ IM 有两条互不相同的路：OneBot v11 桥（私聊、显式用户白名单、loopback webhook、共享 secret；默认关闭，需要本地 NapCat），和 QQ 官方机器人（qq 频道：Ed25519 验签的 HTTPS 回调、只用 openid 而不是 QQ 号、未授权的 openid 只写进 L1 供操作员取号、回复带 msg_id 才算被动回复）。官方频道已部署并在云端跑通验签闭环，回调地址需要在 q.qq.com 后台登记后才会有真实消息进来。DSH 控制台按设计只做运维，不接成她的推理运行时（她的身体与推理在 YuGang 进程里）。尚未完成：手机端私有网络（目前 SSH 隧道）。代码已开源在本仓库。

网关自带一个手机优先的「鱼缸」页：显示她醒着/睡着、上一次的日记与下次意图、最近对话、SELF.md、L3 当前立场、未闭合事项、她相信什么（L2，带 falsifier）、还没验证的觉察卡和预算，并可直接留言（留言会叫醒她）。重复的觉察假设不会新开卡，也不会被算成第二次确认。访问方式与部署细节见 `docs/DEPLOY.md`，运维状态见 `docs/STATUS.md`。

## License

MIT. 详见 [LICENSE](LICENSE).