# SELFCHANGE — 她改自己的能力：提案 → 验证 → 合入 → 可回滚

状态：设计已定（2026-10-26，老大选 C 分级合入 + 失败自动回退并携带错误与环境信息）。

## 0. 一句话

她能读自己的源码、写自己的镜像、跑自己的测试、提一份提案；合入永远是**一条有回滚点的原子动作**，
失败就带着错误消息与环境信息回退，并把这件事写进她自己的 L1。

## 1. 目录与属主

| 路径 | 属主/权限 | 谁写 |
|---|---|---|
| `/home/dafeiyu/yugang`（live，服务从这里跑） | **root:root，目录 0555、文件 0444** | 只有 applier |
| `/home/dafeiyu/.yugang/self/` （她的 git 镜像） | dafeiyu，0700 | 她（self_* 工具） |
| `/home/dafeiyu/.yugang/proposals/<id>/` | dafeiyu 0755；`APPROVED` 只有 root 能建 | 她（meta/patch），root（APPROVED） |
| `/home/dafeiyu/.yugang/apply-results/` | dafeiyu 0755，文件 root 0644 | applier；服务读后改名 `.done` |
| `/var/lib/yugang/candidates/` | root 0700 | applier 临时副本 |
| `/var/lib/yugang/rollback/` | root 0700 | applier 快照 tar |
| `/opt/yugang-venv/` | root；pytest 只在这里装 | 安装脚本 |

镜像内容（显式包含清单，不要把 live 里的 audio/ hyperframes/ reference/ 拷进去）：
`src/`、`tests/`、`docs/`、`deploy/`、`pyproject.toml`、`README.md`、`LICENSE`、`.github/`、`.gitignore`。

## 2. 提案格式

`proposals/<id>/meta.json`：

```json
{"id":"p_20261026T113045_a1b2c3","created_ts":"2026-10-26T03:30:45+00:00","author":"her",
 "base_commit":"<sha>","commit":"<sha>","summary":"一句话说清改了什么",
 "reason":"为什么改（她自己的话）","files":["docs/STATUS.md"],
 "tier":"auto","tests":{"ok":true,"passed":341,"failed":0,"summary":"341 passed, 2 skipped"}}
```

`patch.diff`：`git diff <base_commit> <commit>`（`--binary` 不需要，只收文本）。

id 生成：`p_<YYYYMMDDTHHMMSS>_<6 hex>`，UTC。

## 3. tier：确定性规则，applier 复核，不信任 meta

```
AUTO_PREFIXES = ("docs/", "README.md")
auto   : 所有 changed path 都在 AUTO_PREFIXES 里
review : 其余一切（src/、tests/、deploy/、CI、pyproject）
```

- applier **重新算一遍**，与 meta 不一致就按更严格的那档走（review）。
- 提示词现在住在 `src/**/*.py` 里，所以提示词的改动这一轮是 **review**；下一步把静态提示词抽到
  `src/yugang/prompts/` 并加入 AUTO_PREFIXES，那时提示词才能自动合。

## 4. 合入：`yugang apply <id>`（必须 root 执行）

顺序固定，每一步失败都走第 8 步：

1. **verify**：meta 可解析；`patch.diff` 存在且非空；meta.files 与 patch 里出现的路径一致；
   tier 复算；`git apply --check` 到候选副本（候选副本 = live 的拷贝 + `git init` 的临时树，
   或者直接用 live 的 tar 解开）。
2. **test**：在候选副本里跑 `/opt/yugang-venv/bin/python -m pytest -x -q -p no:cacheprovider`，
   通过 `yugang.world.sandbox.run(command, workdir=<候选副本>, limits=SandboxLimits(timeout_seconds=600))`
   执行——**root 也照样进 bwrap**：无网络、根只读、只有候选副本可写。
   超时来自 `self_change.test_timeout_seconds`（默认 600s）；unit 的 `TimeoutStartSec` 必须大于
   「测试超时 + 45s 重启等待 + 45s 体检」。
   **她自己的 `source_test` 不算最终验证**：那是在她的镜像根里跑的（镜像自带 pyproject，
   testpaths=tests），绿的是副本；合入前这一步必须在候选树上重跑一遍。
3. **approve**：`auto` → 通过；`review` → 需要 `proposals/<id>/APPROVED`（root 建）或命令行 `--yes`。
4. **snapshot**：`tar -C /home/dafeiyu -czf /var/lib/yugang/rollback/<base_commit>.tar.gz yugang`。
5. **swap**：把候选副本同步进 live（`rsync -a --delete --checksum --exclude .git`，保留 root:root
   与只读位）。**`--checksum` 不能省**：rsync 默认的 size+mtime 快检会把「大小相同、mtime 同秒」
   的改动当成没变而跳过——测试里候选树与 live 恰好同秒，这条是测试抓出来的，不加就是静默不换装。
   没有 rsync 时退到逐项覆盖，语义相同（见 `apply.py` 的 `_sync_without_rsync`）。
6. **restart**：`systemctl restart yugang.service`，等 30s。
7. **health**：从 `/etc/dafeiyu-dsh.env` 读 `YUGANG_HTTP_TOKEN`，轮询
   `http://127.0.0.1:3198/health` 最多 45s，要求 `ok=true` 且 `worker_alive=true`。
8. **失败 → 回退**：解回 snapshot tar、`systemctl restart`、再验健康（回退也要验），
   然后写结果。回退本身失败 → 结果里 `rolled_back=false, stage="rollback"`，并让 systemd 保持
   失败状态（不掩盖）。

### 结果 `apply-results/<id>.json`

```json
{"id":"...","ts":"...","ok":false,"stage":"test","applied_commit":null,
 "rolled_back":true,"error":"FAILED tests/test_x.py::test_y - AssertionError: ...",
 "env":{"python":"3.13.5","unit_active":"active","health_ok":true,
        "base_commit":"<sha>","commit":"<sha>","failed_tests":["tests/test_x.py::test_y"],
        "pytest_summary":"1 failed, 340 passed","disk_free_mb":28123,
        "journal_tail":"...","duration_s":42.1}}
```

`error` 截断到 4000 字，保留 `FAILED` 行与最后一段 traceback；`journal_tail` 最多 40 行。

## 5. 她的工具（`src/yugang/selfwork/`）

| 工具 | 参数 | 行为 |
|---|---|---|
| `source_list` | `path=""`, `max=200` | 列镜像里的文件（只镜像内，越界拒绝） |
| `source_read` | `path`, `max_chars=20000` | 读镜像里的文件 |
| `source_write` | `path`, `content`, `message` | 写镜像 + `git commit`；返回短 sha 与改动文件 |
| `source_log` | `limit=10` | 她自己的提交历史（sha、时间、message） |
| `source_test` | `timeout_seconds=600` | 沙箱里对镜像跑 pytest，返回 `{ok,passed,failed,skipped,summary,failed_tests}` |
| `source_propose` | `summary`, `reason` | 从 `origin/main`? 不：从**上次提案的 base**（没有则镜像首个 commit）到 HEAD 出 patch，算 tier，写提案目录，返回 `{id,tier,files,base_commit,commit}` |

写工具规则：只写 `self/` 内；只收 UTF-8 文本；单文件 ≤ 200KB；内容为空允许（等于清空）；
所有动都进 L1（`ai-observation/action`，文案「我改了自己的 <path>：<message>」）——复用
`WorldToolbox` 已有的行动记账习惯。

## 6. 跑测试的沙箱是哪一只，要能被看见

`/status` 的 `self_change.test_backend` 报 `{name, path, weak}`：`name="bubblewrap"` 之外
（比如机器上没装 bwrap、退化到 `unshare`）就 `weak=true`。`/status` 的 `self_change.tools`
列出她手上六个 `source_*`，`mirror` 报镜像状态（没建镜像时给 `ok=false` 而不是让 `/status` 挂）。

原因很直接：缺 bwrap 时测试仍可能绿，但「绿」的含金量不一样，而这在结果 json 里看不出来。

## 7. 服务侧：把结果读进她的 L1

服务主循环里加一步 `ingest_apply_results(store, home)`：扫 `apply-results/*.json`（未 .done），
按 `ok` 写两种事件，然后改名 `.done`：

- 成功：`ai-observation/action`「我改了自己：<summary>。测试 <pytest_summary>，合入 <commit 短 sha>。」
  meta = `{proposal, commit, tier, files}`
- 失败：`ai-observation/action`「我的改动没过：在 <stage> 这一步失败。<error 第一段>。已经回退到
  <base 短 sha>，什么都没变。」meta = `{proposal, stage, rolled_back, env}`（env 里只留
  python/unit_active/health_ok/failed_tests/journal_tail）

## 8. 安装（root，一次性，幂等）

`deploy/self-lane.sh`：
1. `python3 -m venv /opt/yugang-venv && /opt/yugang-venv/bin/pip install pytest`（已有就跳过）
2. `sudo -u dafeiyu python3 -m yugang.selfwork.mirror --init`（用 live 的包含清单建镜像并首个 commit）
3. `chown -R root:root /home/dafeiyu/yugang && chmod -R a-w`（目录 0555/文件 0444）
4. 建 `/var/lib/yugang/{candidates,rollback}`、`~/.yugang/{proposals,apply-results}`
5. `systemctl restart yugang.service`（新代码要能读镜像）

## 9. 不做（明确）

- 不给她 `git push`、不给她 MCP 安装权、不给她 `systemctl`。
- 不做自动 review 档的自动合入（只有 auto 档 + 测试全绿才自动）。
- 不把 live 交给她写：live 归 root，这是“审”能成立的前提。
