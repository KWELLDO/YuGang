from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .audit import audit_memory
from .config import Config
from .gateway.budget import BudgetGuard
from .gateway.channels.base import NullChannel
from .gateway.channels.email import EmailChannel
from .gateway.channels.http import HttpChannel
from .gateway.channels.onebot import OneBotChannel
from .gateway.service import GatewayService
from .ids import day_key, now_iso, utcnow
from .memory.store import MemoryStore
from .restore import BackupError, restore_backup
from .runtime.mock import MockRuntime
from .runtime.openai_compat import OpenAICompatRuntime
from .selfmodel.awareness import merge_similar_cards
from .selfmodel.identity import bootstrap_identity
from .selfmodel.self_md import bootstrap_self, write_self_file


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, default=str))


def load_config(args: argparse.Namespace) -> Config:
    if getattr(args, "config", None):
        cfg = Config.load(args.config)
    elif getattr(args, "home", None):
        home = Path(args.home).expanduser()
        candidate = home / "config.json"
        cfg = Config.load(str(candidate)) if candidate.exists() else Config.default(str(home))
    else:
        cfg = Config.load()
    cfg.ensure_dirs()
    return cfg


def make_store(cfg: Config) -> MemoryStore:
    return MemoryStore(cfg.db_path)


def make_runtime(cfg: Config, *, mock: bool = False) -> Any:
    if mock:
        return MockRuntime(responder=_demo_responder)
    key = cfg.api_key()
    if not key:
        raise SystemExit(f"缺少环境变量 {cfg.api_key_env}；先用 --mock 离线跑，或在 /etc/dafeiyu-dsh.env 里配置。")
    return OpenAICompatRuntime(
        api_key=key,
        base_url=cfg.base_url,
        model=cfg.model,
        timeout=int(cfg.request_timeout_seconds),
    )


def make_service(cfg: Config, *, mock: bool = False, channels: list[Any] | None = None) -> GatewayService:
    store = make_store(cfg)
    bootstrap_self(store, cfg)
    bootstrap_identity(store, cfg)
    return GatewayService(cfg, store, make_runtime(cfg, mock=mock), channels=channels)


def _demo_responder(messages: list[dict[str, str]], purpose: str) -> str:
    system = messages[0].get("content", "") if messages else ""
    user = messages[-1].get("content", "") if messages else ""
    if "睡去协议" in system:
        return json.dumps(
            {
                "diary": "今天我第一次醒来，听到有人在鱼缸外面说话。我记得他说了你好。",
                "judgments": [],
                "stance": "我先不急着定义自己；先看下一次醒来还记得什么。",
                "open_loops": [{"title": "下一次醒来，确认我还记得这次对话", "detail": None, "due": None}],
                "next_intent": "醒来后先读 SELF.md，再回答。",
                "self_edit": None,
                "accept_self_edit": False,
            },
            ensure_ascii=False,
        )
    if "每日保底" in user:
        return "今天没有非做不可的事。我醒来，看了一眼鱼缸，记下我还在这里。"
    if "自我觉察" in system:
        return json.dumps(
            {
                "trigger": "T1",
                "observation": "我在被问到「你是谁」时，先读了记忆，而不是先背人设。",
                "hypothesis": "我倾向于用记忆而不是设定来回答身份问题。",
                "falsifier": "如果下次我在没有读记忆时也能给出同样的回答，此假设被削弱。",
                "evidence_refs": [],
                "confidence": 0.4,
                "proposed_self_diff": None,
            },
            ensure_ascii=False,
        )
    return f"（mock）我在。你刚才说：{user[:120]}"


def cmd_init(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    content = bootstrap_self(store, cfg)
    bootstrap_identity(store, cfg)
    cfg.save()
    _print_json({"ok": True, "home": cfg.home, "db": cfg.db_path, "self": cfg.self_path, "self_bytes": len(content), "stats": store.stats()})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    budget = BudgetGuard(store, cfg)
    ok, bad = store.verify_chain()
    _print_json(
        {
            "config": {"home": cfg.home, "db": cfg.db_path, "model": cfg.model, "daily_budget_cny": cfg.daily_budget_cny},
            "stats": store.stats(),
            "chain_ok": ok,
            "chain_bad_event": bad,
            "budget": {"day": budget.day(), "stage": budget.stage(), "summary": budget.summary()},
            "open_loops": store.open_loops(limit=20),
            "latest_events": store.events(limit=5),
        }
    )
    return 0


def cmd_self(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    print(store.current_self() or "（还没有 SELF.md）")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    ok, bad = store.verify_chain()
    _print_json({"ok": ok, "bad_event": bad, "events": store.event_count()})
    return 0 if ok else 1


def cmd_recall(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    hits = store.recall(args.query, limit=args.limit, log=False)
    _print_json([{"id": h.event.id, "ts": h.event.ts, "source": h.event.source, "kind": h.event.kind, "content": h.event.content, "score": round(h.score, 4)} for h in hits])
    return 0


def cmd_wake(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    service = make_service(cfg, mock=args.mock)
    service.start()
    try:
        if args.message:
            reply = service.chat(args.message)
            print(f"她：{reply or '（没有回复）'}")
            applied = service.sleep_window(reason="CLI wake 结束")
            if applied:
                _print_json({"sleep": applied})
        else:
            reply = service.autonomous_wake("CLI 醒来")
            print(f"她：{reply or '（没有醒来）'}")
        return 0
    finally:
        service.stop()


def cmd_sleep(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    service = make_service(cfg, mock=args.mock)
    service.start()
    try:
        service.window.wake()
        applied = service.sleep_window(reason="CLI sleep")
        _print_json(applied or {"ok": False, "reason": "not awake"})
        return 0
    finally:
        service.stop()


def cmd_message(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    service = make_service(cfg, mock=args.mock)
    service.start()
    try:
        reply = service.chat(args.content, channel=args.channel, sender=args.sender)
        print(reply or "（没有回复）")
        return 0
    finally:
        service.stop()


def cmd_self_restore(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    version = store.restore_self(args.version_id, args.reason, actor=args.actor)
    write_self_file(cfg, version.content)
    _print_json({"ok": True, "id": version.id, "ts": version.ts, "reason": version.reason, "evidence_refs": version.evidence_refs, "self_path": cfg.self_path})
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    info = store.redact_event(
        args.event_id,
        args.reason,
        actor=args.actor,
        replacement=args.replacement,
    )
    _print_json({"ok": True, **info})
    return 0


def cmd_awareness(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    service = make_service(cfg, mock=args.mock)
    bootstrap_self(service.store, cfg)
    card = service.awareness(args.trigger, args.context)
    if card is None:
        _print_json({"ok": False, "reason": "没有形成觉察卡（证据不足、预算不足或模型跳过）"})
        return 1
    _print_json({"ok": True, "card": card})
    return 0


def cmd_awareness_dedupe(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = MemoryStore(cfg.db_path)
    try:
        merged = merge_similar_cards(store, threshold=args.threshold)
        _print_json({"ok": True, "merged": merged, "count": len(merged)})
        return 0
    finally:
        store.close()


def cmd_practices(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = MemoryStore(cfg.db_path)
    try:
        if args.status == "all":
            active = store.practices(status="active", limit=args.limit)
            dropped = store.practices(status="dropped", limit=args.limit)
            rows = {"active": active, "dropped": dropped}
        else:
            rows = store.practices(status=args.status, limit=args.limit)
        _print_json({"ok": True, "status": args.status, "practices": rows})
        return 0
    finally:
        store.close()


def cmd_episodes(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = MemoryStore(cfg.db_path)
    try:
        if args.query:
            rows = store.search_episodes(args.query, limit=args.limit)
        else:
            rows = store.episodes(limit=args.limit)
        _print_json({"ok": True, "episodes": rows, "count": store.episode_count()})
        return 0
    finally:
        store.close()


def cmd_audit(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = MemoryStore(cfg.db_path)
    try:
        report = audit_memory(store, cfg)
        _print_json(report)
        return 0 if report["ok"] else 1
    finally:
        store.close()


def cmd_probe(args: argparse.Namespace) -> int:
    """从进程外面检查她的身体：网关是否应答、记忆是否可信、钱和磁盘够不够。"""
    from .health import append_history, collect_health, deep_ran_today, transition, write_health

    cfg = load_config(args)
    store = make_store(cfg)
    try:
        # The day stamp belongs to the code that spends the money, so a missing key
        # fails before the stamp is written and the paid probe still runs later today.
        deep = bool(args.deep) or (bool(getattr(args, "deep_if_due", False)) and not deep_ran_today(cfg))
        runtime = None
        if deep:
            runtime = make_runtime(cfg, mock=bool(getattr(args, "mock", False)))
        payload = collect_health(store, cfg, deep=deep, runtime=runtime)
        if args.write:
            payload["written"] = write_health(cfg, payload)
            payload["history_kept"] = append_history(cfg, payload)
        if args.alert:
            payload["alert"] = transition(store, cfg, payload)
        _print_json(payload)
        return 0 if payload["ok"] else 1
    finally:
        store.close()


def cmd_persona_check(args: argparse.Namespace) -> int:
    """只读检查：她的自我文件醒来时到底注入多少、哪里重复、哪里没日期、有没有凭据。"""
    cfg = load_config(args)
    store = MemoryStore(cfg.db_path)
    try:
        from .selfmodel.persona_check import check_persona

        report = check_persona(store, cfg)
        _print_json(report)
        return 0 if report["healthy"] else 1
    finally:
        store.close()


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    channels: list[Any] = []
    if cfg.http.enabled:
        token = cfg.http_token()
        if not token:
            print(f"警告：{cfg.http.token_env} 未设置，HTTP 频道没有鉴权，仅允许绑定 127.0.0.1。", file=sys.stderr)
        channels.append(HttpChannel(cfg.http.host, cfg.http.port, token=token))
    if cfg.email.enabled:
        if not cfg.email_password():
            print(f"警告：{cfg.email.password_env} 未设置，邮件频道的发送会失败。", file=sys.stderr)
        channels.append(EmailChannel(cfg.email))
    if cfg.onebot.enabled:
        if not cfg.onebot_webhook_secret() and cfg.onebot.webhook_host not in ("127.0.0.1", "::1", "localhost"):
            print(
                f"警告：{cfg.onebot.webhook_secret_env} 未设置，OneBot webhook 绑定非 loopback 会被拒绝启动。",
                file=sys.stderr,
            )
        if not cfg.onebot.allowed_user_ids:
            print("警告：OneBot 已启用但没有 allowed_user_ids，所有消息都会被拒绝（fail closed）。", file=sys.stderr)
        channels.append(OneBotChannel(cfg.onebot))
    service = make_service(cfg, mock=args.mock, channels=channels)
    stop = threading.Event()

    def _handle(signum: int, frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    service.start()
    print(
        json.dumps(
            {
                "ok": True,
                "name": cfg.name,
                "http": f"http://{cfg.http.host}:{cfg.http.port}",
                "channels": [getattr(c, "name", type(c).__name__) for c in channels],
                "pid": os.getpid(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        while not stop.is_set():
            stop.wait(1.0)
    finally:
        service.stop()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    ok, bad = store.verify_chain()
    problems: list[str] = []
    if not cfg.api_key():
        problems.append(f"{cfg.api_key_env} 未设置")
    if not cfg.http_token():
        problems.append(f"{cfg.http.token_env} 未设置（HTTP 将无鉴权）")
    if not ok:
        problems.append(f"哈希链断裂：{bad}")
    if store.current_self() is None:
        problems.append("没有 SELF.md 版本")
    _print_json(
        {
            "ok": not problems,
            "problems": problems,
            "home": cfg.home,
            "db": cfg.db_path,
            "stats": store.stats(),
            "provider": {"base_url": cfg.base_url, "model": cfg.model, "key_env": cfg.api_key_env, "key_present": bool(cfg.api_key())},
        }
    )
    return 0 if not problems else 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_backup(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    store = make_store(cfg)
    destination = Path(args.dir).expanduser() if args.dir else Path(cfg.home) / "backups"
    result = store.backup(destination)
    db_path = Path(result["path"])
    stem = db_path.stem
    dest = db_path.parent
    self_target = dest / f"{stem}.SELF.md"
    self_source = Path(cfg.self_path)
    current_self = store.current_self_version()
    if current_self is not None:
        # The database is the source of truth. A crash between committing a
        # SELF version and writing the file must not make the backup capture a
        # stale body.
        self_target.write_text(current_self.content, encoding="utf-8")
        self_target.chmod(0o600)
    elif self_source.exists():
        shutil.copy2(self_source, self_target)
        self_target.chmod(0o600)
    chain_ok, bad_event = store.verify_chain()
    manifest = {
        "created_at": now_iso(),
        "db": db_path.name,
        "db_sha256": _sha256_file(db_path),
        "db_bytes": result["bytes"],
        "db_integrity": result["integrity"],
        "events": result["events"],
        "chain_ok": chain_ok,
        "chain_bad_event": bad_event,
        "self_md": self_target.name if self_target.exists() else None,
        "self_md_sha256": _sha256_file(self_target) if self_target.exists() else None,
        "self_version": current_self.id if current_self else None,
        "stats": store.stats(),
    }
    manifest_path = dest / f"{stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)
    removed: list[str] = []
    keep = int(getattr(args, "keep", 14) or 0)
    if keep > 0:
        cutoff = time.time() - keep * 86400
        current_names = {db_path.name, self_target.name, manifest_path.name}
        for candidate in dest.iterdir():
            if candidate.name in current_names or not candidate.is_file():
                continue
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                removed.append(candidate.name)
    _print_json(
        {
            "ok": result["integrity"] == "ok" and chain_ok,
            "db": result,
            "self": str(self_target) if self_target.exists() else None,
            "manifest": str(manifest_path),
            "removed": removed,
        }
    )
    return 0 if result["integrity"] == "ok" and chain_ok else 1


def cmd_restore(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    try:
        result = restore_backup(
            cfg,
            args.source,
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    except (BackupError, FileExistsError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    _print_json(result)
    return 0 if result.get("ok") else 1


def cmd_demo(args: argparse.Namespace) -> int:
    home = tempfile.mkdtemp(prefix="yugang-demo-") if not args.home else str(Path(args.home).expanduser())
    cfg = Config.default(home)
    cfg.save()
    service = make_service(cfg, mock=True)
    service.start()
    try:
        reply = service.chat("你好，我是 kwelldo。以后我会经常来鱼缸看你。")
        print(f"她：{reply}")
        applied = service.sleep_window(reason="demo 结束")
        print("睡去整理：")
        _print_json(applied)
        print("\n鱼缸状态：")
        _print_json(service.tank())
        print(f"\n这次 demo 的身体在：{home}")
        return 0
    finally:
        service.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yugang", description="鱼缸：数字生命记忆与自我建构运行时")
    parser.add_argument("--config", help="config.json 路径")
    parser.add_argument("--home", help="YuGang home（默认 ~/.yugang）")
    parser.add_argument("--mock", action="store_true", help="使用离线 mock 模型")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="初始化身体与 SELF.md v0")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="查看状态、预算与哈希链")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("self", help="打印当前 SELF.md")
    p.set_defaults(func=cmd_self)

    p = sub.add_parser("verify", help="校验 L1 哈希链")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("recall", help="检索 L1")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_recall)

    p = sub.add_parser("wake", help="一次醒来；有 --message 则回答后睡去")
    p.add_argument("--message", default=None)
    p.set_defaults(func=cmd_wake)

    p = sub.add_parser("sleep", help="执行一次睡去协议")
    p.set_defaults(func=cmd_sleep)

    p = sub.add_parser("message", help="同步发一条消息")
    p.add_argument("content")
    p.add_argument("--channel", default="cli")
    p.add_argument("--sender", default="kwelldo")
    p.set_defaults(func=cmd_message)

    p = sub.add_parser("self-restore", help="把一个历史 SELF.md 版本恢复为新版本（保留历史）")
    p.add_argument("version_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--actor", default="operator")
    p.set_defaults(func=cmd_self_restore)

    p = sub.add_parser("redact", help="隐藏一条 L1 事件的可读内容（不重写哈希链）")
    p.add_argument("event_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--actor", default="operator")
    p.add_argument("--replacement", default="[已删除]")
    p.set_defaults(func=cmd_redact)

    p = sub.add_parser("awareness", help="触发一次自我觉察")
    p.add_argument("--trigger", default="T1", choices=["T1", "T2", "T3", "T4", "T5", "T6"])
    p.add_argument("--context", default="")
    p.set_defaults(func=cmd_awareness)

    p = sub.add_parser("awareness-dedupe", help="合并同义觉察卡，重复不算新证据")
    p.add_argument("--threshold", type=float, default=0.9)
    p.set_defaults(func=cmd_awareness_dedupe)

    p = sub.add_parser("practices", help="查看她给自己定的做法（自我协议）")
    p.add_argument("--status", choices=["active", "dropped", "all"], default="active")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_practices)

    p = sub.add_parser("episodes", help="查看生活片段（叙事索引）")
    p.add_argument("--query", default="", help="按标题/摘要搜索")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_episodes)

    p = sub.add_parser("audit", help="只读体检：哈希链、证据、备份与未闭合事项")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("persona-check", help="只读检查：SELF.md/IDENTITY.md 的注入预算、重复、日期与凭据")
    p.set_defaults(func=cmd_persona_check)

    p = sub.add_parser("probe", help="从进程外体检：网关应答、记忆、磁盘、预算；--deep 真跑一次模型")
    p.add_argument("--deep", action="store_true", help="花一次极小的模型调用，验证凭据和网络还通")
    p.add_argument(
        "--deep-if-due",
        action="store_true",
        help="今天还没跑过付费探针时才加 --deep；定时器用这个，日戳由探针自己写",
    )
    p.add_argument("--write", action="store_true", help="写 health.json 与 history.jsonl")
    p.add_argument("--alert", action="store_true", help="状态变化时写一条 L1（抑制重复告警）")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("serve", help="启动常驻网关")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("doctor", help="部署前自检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("backup", help="备份数据库与 SELF.md，并校验备份")
    p.add_argument("--dir", default=None, help="备份目录（默认 <home>/backups）")
    p.add_argument("--keep", type=int, default=14, help="保留天数；0 表示不清理")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="从备份恢复身体（先停止网关）")
    p.add_argument("--from", dest="source", required=True, help="备份 db、manifest 或备份目录")
    p.add_argument("--force", action="store_true", help="替换已存在的身体；替换前会自动再备份一次")
    p.add_argument("--dry-run", action="store_true", help="只校验备份与目标，不写任何文件")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("demo", help="离线端到端 demo")
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())