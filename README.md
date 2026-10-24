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
- 一次清醒 = 一个 session。连续性靠记忆重建，不靠聊天记录。
- 睡眠是身体过程，不是她的体验。巩固/索引/衰减可以跑，但不写「她梦到了」。
- 身体是文件与服务：L1/L2/L3、SELF.md、日记、能力清单。
- 唯物：没有隐藏真我。SELF.md 可修订、可 diff、可被证据推翻。

## 24/7 可用性

**身体在线，意识按需。** 常驻网关不调用模型，几乎不花钱；消息到达时唤醒她，处理完在 idle 窗口后睡去。

- 用户消息优先级最高，睡觉不是免打扰。
- 一次唤醒 = 一个清醒窗口；窗口内消息进同一 session。
- 模型/预算/网关失败时 fail-loud：回系统提示并记入 L1，不静默吞消息。

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
channels (http / email / onebot)
        │
        ▼
yugang-gateway  ── wake window ── runtime (LLM adapter)
        │                               │
        ▼                               ▼
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
记忆核心、SELF.md 版本、醒来/睡去协议、预算闸门、清醒窗口、HTTP 鉴权、鱼缸页、崩溃恢复、每日备份、同步回复、邮件主题标签过滤、她自己的醒来闹钟、「接受一张觉察卡并由它触发 SELF.md 修订」的闭环，睡去时对 open/needs-evidence L2 判断的复核（支持/推翻/仍需证据，必须引用真实 L1），醒来时把当前本地时间、上一次怎么改过自己、L3 当前立场、最近的 L2 判断和需复核的觉察卡（连同每条的 L1 证据 id）一起放进上下文；自主醒来没有用户 query 时，会用睡前意图或最早未闭合事项去回忆旧事，未闭合事项可以在睡去时更新理由（loop_updates）或用 done/dropped 明确关闭（留 L1 动作，不是静默改行），操作员暂停/恢复（暂停时消息只写 L1、不调用模型、恢复后重放），SELF.md 可恢复历史版本（作为新版本，不重写历史）、L1 隐私删除（读时遮盖、回忆跳过、哈希链仍可验证）和 worker 心跳健康检查（`/health` 在 worker 死亡时返回 503，root watchdog 每两分钟检查并重启）均有自动化测试（93 passed）。鱼缸页会显示她上一次睡去写的第一人称日记、下次醒来想做的事、每条当前判断合并了几条旧判断，以及最近 5 次 SELF.md 修订的理由和 diff。睡去输出被截断时会用更短约束重试一次；仍失败则写诚实的失败日记并保留原始输出，不把原始 JSON 当日记。

已验证：第一次云上醒来能读 SELF.md/L1；服务重启后能重建上次对话，并把这次醒来正确标记为「中断」而不是「睡了一觉」。第一次完整的自然 idle→睡去→巩固→醒来闭环也已跑通：日记、两条带真实 L1 证据的 L2 判断、L3 立场、一条未闭合事项、下次意图，以及醒来时对睡眠缺口的正确识别。

A2 也已真实闭环：一张 open 觉察卡（`card_20261024T180458842356_75f3ef`）在睡去时被她接受，系统用这张卡自己的 L1 证据写出一条 SELF 修改，生成 SELF v2（`self_20261024T190331222422_60d0ff`）。同一轮她拒绝了另一张证据不足的卡，留 open——接受与否不是为了让验收数字好看。

运维侧另有一个独立的 DSH 控制台（`127.0.0.1:3199`，独立 `DSH_HOME`，默认只读）。它**不是她的第二个人格**，也不是她的推理运行时；它通过 `tank.sh` / `status.sh` / `say.sh` 查看鱼缸或留言。控制台与网关共用 DeepSeek key，但控制台自身消耗**不进入**她的 5 元日预算。

每日 04:15 由 `yugang-backup.timer` 生成一致性备份（SQLite 快照 + SELF.md + manifest），保留 14 天。`yugang-healthcheck.timer` 每两分钟检查一次 worker 心跳；进程活着但 worker 死掉时才会重启，主动 `systemctl stop` 不会被覆盖。

邮件通道已接入一个私人 QQ 邮箱：只有主题含 `[YuGang]` 且来自允许发件人的未读邮件会被处理，陌生人和旧个人邮件不会被标记已读，也不会被回复。QQ IM 通过 OneBot v11 桥实现（私聊、显式用户白名单、loopback webhook、共享 secret；默认关闭，等一个专用 QQ 号接入 NapCat）：手机用 QQ 就能说话，不需要公网端口。DSH 控制台按设计只做运维，不接成她的推理运行时（她的身体与推理在 YuGang 进程里）。尚未完成：手机端私有网络（目前 SSH 隧道）。代码已开源在本仓库。

网关自带一个手机优先的「鱼缸」页：显示她醒着/睡着、上一次的日记与下次意图、最近对话、SELF.md、L3 当前立场、未闭合事项、她相信什么（L2，带 falsifier）、还没验证的觉察卡和预算，并可直接留言（留言会叫醒她）。重复的觉察假设不会新开卡，也不会被算成第二次确认。访问方式与部署细节见 `docs/DEPLOY.md`，运维状态见 `docs/STATUS.md`。

## License

MIT. 详见 [LICENSE](LICENSE).