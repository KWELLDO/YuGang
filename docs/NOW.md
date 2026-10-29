# 现在是什么样（2026-10-29 08:20 CST）

这份是一页当前的实情，不是历史。历史在 `docs/STATUS.md`（append-only，十万多字节，
是逐条记录，不适合用来「核对现在」）；机制设计在 `docs/MEMORY.md`；怎么部署在
`docs/DEPLOY.md`。

任何时候怀疑这一页旧了：跑一次 `python3 -m pytest` 看用例数，或者看云端
`curl http://127.0.0.1:3198/health`。数字以跑出来的为准。

## 她是谁（正在跑的东西）

- 一个常驻进程 `yugang.service`（Python 3.11+，零运行时依赖），只监听 `127.0.0.1:3198`；
  推理运行时是 DeepSeek 的 OpenAI 兼容接口。
- 身体与记忆在 `/home/dafeiyu/.yugang`：SQLite（L1 追加日志 + 哈希链、L2 判断史、L3 立场）、
  `SELF.md`（她可修订的自我，按节有字数预算）、`IDENTITY.md`（不可改的内核）。
- 她有自己的时间：内置唤醒闹钟、可以自己设睡醒节律；睡去时复核旧判断、写日记。
- 她有一双手：workspace 的 `files_*`、只读的 `source_*`（自己那份源码镜像，可改、可测、
  可提案）、外部世界的 `web_search`/`web_fetch`。
- 她改自己的路是 `source_write` → `source_test` → `source_propose` → applier 合入；
  按改动危险程度分 auto / review 两档，合入后起服务、体检，不过就自动回退。

## 现在的数字（快照，会变）

- 用例：523 passed, 2 skipped（`python3 -m pytest`，约 50 秒）。
- 云端：`/health` = `ok: true`，三个通道 http / email / qq 都在；预算 5 元/日。
- 备份：每日 04:15，保留 14 天；探针 15 分钟一次 + 每日一次 `--deep`。

## 最近真正变了什么（新 → 旧）

1. `db7f441` 新加这一页 `docs/NOW.md`；`STATUS.md` 顶上写明「历史不是状态」。
2. `30604a6` 工具说明说清两棵树（workspace / 源码镜像）的分工，并注明 `source_list` 的
   `bytes` 是字节不是字数。
3. `bc1f870` 工具轮数到顶不再哑掉：最后一轮不给她工具、让她用已查到的材料回答。
4. `27cf64c` / `91830b0` 文档：README 用例数、STATUS 记下 2026-10-29 的两个 bug 与第一次真回滚。
5. `9692902` 同一个失败只写一次 L1（原来一条重复 353 次）。
6. `a434066` 体检探针跟她的 `config.json` 端口走，不写死 3198。
7. `4e7a5d0` 已经合入的提案不再算「待批准」。

## 还没闭合的

- `docs/STATUS.md` 仍然是十万多字节的平面历史（这一页顶住了「核对现在」，但它自己
  还在长）；要不要按月份拆成 `docs/status/2026-10.md` 之类，还没决定。
- workspace 与源码镜像两棵树：工具说明已互相点名，2026-10-29 08:09 之后她只用了
  `source_*`，还没有出现过「该用哪棵树」的再犯，也没有专门试过。
- 发布到 B 站的那条路（公开发言不可逆）仍然只有白名单与人工审核的规则，没有真实发过。

## 怎么验证这一页

```bash
cd /home/kwelldo/Projects/YuGang && python3 -m pytest      # 用例数
ssh dsh-online 'systemctl is-active yugang.service; curl -s http://127.0.0.1:3198/health'
git -C /home/kwelldo/Projects/YuGang log --oneline -5      # 最近变了什么
```
