# YuGang 运行状态

更新时间：2026-10-25 01:10 CST（Asia/Shanghai）

## 已部署

- 云端主机：Debian，独立 Unix 用户 `dafeiyu`（与既有租户平台隔离）。
- 代码：`/home/dafeiyu/yugang`
- 身体/记忆：`/home/dafeiyu/.yugang`（SQLite L1/L2/L3、SELF.md、日志）
- 服务：`yugang.service`，`Restart=always`，仅监听 `127.0.0.1:3198`。
- 密钥：`/etc/dafeiyu-dsh.env`（root:root 0600），不进入仓库。
- 公网端口：未开放；当前通过 SSH 访问，客户端见 `scripts/yugang-remote.sh`。

## 已验证

- 自动化测试：28 passed（L1 哈希链、FTS5 中文回忆与未来确认加权、L2 falsifier/复核、SELF 版本与 diff、觉察卡证据、睡眠应用与未闭合事项去重、预算闸门、HTTP 鉴权、邮件解析、同窗口续聊语义）。
- 第一次云上醒来：能读 SELF.md 与 L1，SELF 仍是 v0，只有出生材料。
- 服务重启后的连续性：第二条消息能复述第一次对话；醒来上下文把它标为「中断后的」，不是「睡了一觉」；L1 的 wake 事件记录 `interrupted=true` 和真实中断秒数。
- 第一次自然睡去（2026-10-25 00:50:10 CST）：写了第一人称日记；从 L1 提炼 2 条带真实 evidence_refs 的 L2 判断；写入 L3 立场；新增一条未闭合事项；留下下次意图；本轮未改 SELF.md（`self_committed=false`）。
- 第一次自然睡去后的醒来（00:51:58 CST）：wake 事件 `interrupted=false`，`gap_seconds=107.56`；她读到了睡前日记与下次意图，并准确区分这次是睡眠后而非中断后。
- 同一次清醒内的第二条消息：上下文标记为续聊，L1 没有新增 wake；她正确回答「同一次清醒里，没有又睡一觉」。
- 第二次自然睡去（01:07:15 CST）：新增 3 条带真实证据的 L2 判断、1 条新未闭合事项、1 条新的下次意图；SELF.md 仍未改（self_committed=false，当时云端跑的仍是旧 prompt，只允许“推翻”才改）。
- 第二次睡去暴露的问题：同一条「检验 given 主张」被重复写成两条未闭合事项。已在本机修复为按标题去重，并加回归测试；尚未部署。
- 账单：截至 01:08，当日花费 0.038868 元 / 5 元。

## 进行中 / 下一步

- 独立 DSH 交互层（当前是 YuGang 自己的 HTTP 网关，不依赖 DSH 进程）。
- IM/邮件通道：邮件代码已实现，缺一个专用邮箱凭据；微信/QQ 需要公网回调或非官方桥，尚未接入。
- 开源仓库的首次 commit / GitHub remote。
- 把本机更新的代码（未来确认加权、SELF 证据成长、未闭合事项去重）部署到云端；云端当前仍是 commit 6b36f86。
- 更长的连续运行观察：跨日预算、异常/重启后的恢复、觉察卡进入唤醒上下文。
