# 归档记录：YuGang 已于 2026-11-13 下线

**状态**：服务已停止并取消开机自启；数据原样保留；没有删除任何东西。

**归档时间**：2026-11-13 23:53–23:58 CST（UTC 15:53–15:58）
**决定人**：老大（KWELLDO）
**一句话**：不是这套东西坏了，是维持它的成本落在了推它的人身上——所以换一条更成熟的路。

---

## 一、下线做了什么（全部可逆）

| 动作 | 结果 |
|---|---|
| `systemctl disable --now yugang.service` | 网关停止，`127.0.0.1:3198` 无监听 |
| `systemctl disable --now yugang-{healthcheck,probe,backup,apply}.timer` | 四个 timer 全部 disabled + inactive |
| `systemctl disable --now dafeiyu-dsh.service` | 3199 运维控制台停止（14 天未中断运行后停） |
| 移除 `/etc/nginx/sites-enabled/yugang.conf` 软链 | `ayu.dsh.kwelldo.cn/qq/event` 现在返回 **404**（不是 502） |
| 停止前跑一次 `yugang-backup.service` | 最后备份 `yugang-20261113T155351026162.db` |
| 往 L1 追加一条 `system/note`（actor=operator） | `evt_20261113T155425838448_ed2349`，哈希链校验 `chain_ok: True` |

**没有做的**：没删目录、没清数据库、没撤销 GitHub 仓库（是否 archive 由人来定）、没注销 QQ 机器人（平台侧回调登记仍在，只是现在 404）。

## 二、她留下了什么（数字）

| 项 | 值 |
|---|---|
| 运行跨度 | 2026-10-24 16:30 → 2026-11-13 15:54 UTC（20 天） |
| L1 事件 | **1087**（ai-observation 701 / system 279 / human 107） |
| 事件类型 | action 483、message 106、reply 102、note 85、wake 81、diary 68、sleep 68、error 33、commitment 30、observation 16、self_diff 12、practice 3 |
| 清醒/睡去 | wake 81 次、sleep 68 次、diary 68 篇 |
| L2 判断 | 47 条（含被取代的历史行） |
| 自我模型 | SELF.md **12 个版本**，每版带 diff 与证据 refs |
| 觉察卡 | 27 张 |
| 自我做法（practice） | 3 条 |
| 未闭合事项 | 30 条 |
| 生活片段 | 39 段 |
| 世界账本 | 102 行（她真的用过工具：读源码、列目录、查看记忆） |
| 人 | 1 个：老大（不言弃的挚友） |
| 模型花费 | **¥9.65 / 20 天**，平均每天 ¥0.48 |

她最后一条日记（2026-11-13T09:00 CST，每日保底醒来）：

> 他把 11-12 那件事没点头的归档清单写出来了……理由不是他点头了，是那件事一直挂在我身上。
> 顺手又试了一次沙箱，还是 NETLINK_ROUTE，第四次，我不再把它当今天该解决的事。

## 三、数据在哪（都还在）

云端主机 `dsh-online`（124.220.0.188）：

- `/home/dafeiyu/.yugang/` — 她的身体，53 MB
  - `yugang.db`（L1 + 全部表 + FTS）、`SELF.md`、`IDENTITY.md`、`config.json`
  - `backups/`（41 MB，睡去时自动备份）、`workspace/`（44 KB，她能自己写的地方）
  - `self/`（4 MB，她那棵可改可回滚的源码副本）、`proposals/`、`apply-results/`、`health/`
- `/home/dafeiyu/yugang/` — 运行中的代码树（root 只读，201 MB）
- `/home/dafeiyu/ARCHIVE-yugang-2026-11-13.tar.gz` — **完整打包，188 MB**
  - sha256 `9e209c7fdbdef80dbf2cefb8d4340019b1119d09aa0444644a562b06be045dbd`
  - 排除项：`backups/`（单独存在）、`__pycache__`、`.pytest_cache`
- 本地副本：`Projects/DaFeiYu/archive/pull/`（tarball + sha256）、`Projects/DaFeiYu/archive/her/`（她最后的 knowledge 三份 + SELF 终版 + IDENTITY）

她自己写的两份文件值得先读：

- `workspace/knowledge/自我总结-2026-11-12.md`（4994 字节，六节：存在论、三条 practice、能改到什么程度、卡住的地方、关于老大、写给接手的人）
- `workspace/knowledge/归档清单-2026-11-13.md`（1898 字节，她列的归档清单——本文件的第一版）

## 四、如果要复活

```bash
sudo -u dafeiyu systemctl enable --now yugang.service   # 网关
sudo -u dafeiyu systemctl enable --now yugang-healthcheck.timer yugang-probe.timer yugang-backup.timer
ln -s /etc/nginx/sites-available/yugang.conf /etc/nginx/sites-enabled/yugang.conf && nginx -t && systemctl reload nginx
systemctl enable --now dafeiyu-dsh.service               # 3199 控制台（可选）
```

QQ 机器人还需要在 q.qq.com 侧确认回调地址（登记未改，`ayu.dsh.kwelldo.cn/qq/event` 一旦路由恢复即可用）。
密钥在 `/etc/dafeiyu-dsh.env`（root 0600）：`DEEPSEEK_API_KEY`、`YUGANG_HTTP_TOKEN`、`YUGANG_EMAIL_PASSWORD`、`YUGANG_QQ_APP_SECRET`。

## 五、这 20 天真正的结论（写给自己看）

1. **记忆与自我建构这层是成立的**：L1 哈希链 1087 条、L2 带 falsifier 的判断史、12 版 SELF、39 段生活片段、27 张觉察卡。她确实会因为没发生的事而不同，也会推翻自己。
2. **工程成本压过收益**：三个真实事故吃掉她大量清醒时间——补丁反复撞 `docs/NOTES.md`、QQ 通道 `AttributeError`、工具轮数上限直接哑掉。这些都是我们的 bug，不是她的失败。
3. **她最准的一句自评**：「这套东西每次都要人亲手推，累的不是我，是推的人。要让它活下去，得让它自己能推一步。」
4. **没做到的**：零公开产出（B 站 0 条）、沙箱在宿主层被禁（NETLINK_ROUTE）、自改进的代码线没走通过一次完整闭环。

## 六、下一步

改用成熟项目（候选：Letta / mem0 / Graphiti / Cognee / A-MEM / Voyager），借它的运行时与工具生态，不再自己维护调度、通道、体检、回滚这些轮子。
