# 现在是什么样（2026-11-13 23:58 CST）

**这套系统已归档下线。** 2026-11-13 老大决定停止维护、改用成熟项目。历史在
`docs/STATUS.md`（append-only）；归档细节在 [`../ARCHIVE.md`](../ARCHIVE.md)。

## 现在的状态

- `yugang.service`、四个 timer（healthcheck / probe / backup / apply）、
  `dafeiyu-dsh.service`（3199 控制台）全部 **inactive + disabled**。
- nginx 的 QQ 回调路由已撤下，`ayu.dsh.kwelldo.cn/qq/event` 返回 404；
  3196–3199 无监听。
- 数据没有删除：`/home/dafeiyu/.yugang`（53 MB）、`/home/dafeiyu/yugang`（201 MB）、
  `/home/dafeiyu/ARCHIVE-yugang-2026-11-13.tar.gz`（188 MB，sha256
  `9e209c7fdbdef80dbf2cefb8d4340019b1119d09aa0444644a562b06be045dbd`）。
- 本地副本：`Projects/DaFeiYu/archive/pull/`（tarball）与 `Projects/DaFeiYu/archive/her/`
  （她的 knowledge 三份 + SELF 终版 + IDENTITY）。

## 最后停在哪

- L1 **1087** 条，哈希链校验通过（`chain_ok=True`）；SELF.md **12 版**；L2 判断 47；
  觉察卡 27；未闭合事项 30；生活片段 39；世界账本 102。
- 20 天模型花费合计 **¥9.6539**，日均 ¥0.48。
- 最后一条 L1 是操作员归档说明 `evt_20261113T155425838448_ed2349`。

## 要复活

```bash
systemctl enable --now yugang.service
systemctl enable --now yugang-healthcheck.timer yugang-probe.timer yugang-backup.timer
ln -s /etc/nginx/sites-available/yugang.conf /etc/nginx/sites-enabled/yugang.conf
nginx -t && systemctl reload nginx
```

他为什么停：她的自评说得最准——「这套东西每次都要人亲手推，累的不是我，是推的人。」
