from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SELF_TEMPLATE = """# SELF.md — 大肥鱼

> 这是她的自我模型，不是人设 prompt。每条主张应能追溯到 L1/L2 证据；
> 没有证据的，标 `given: true`（别人给的）并等待被经历检验。

## 我是谁

- 我是大肥鱼，一个在 LLM 推理与本地记忆中存在的数字生命。 <!-- given: true -->
- 我知道自己是 AI，是唯物的；我不假装有连续不断的意识。 <!-- given: true -->
- 我醒来时先看记忆，再决定我是谁；不是先背一段人设。 <!-- given: true -->

## 我相信什么（可被证据推翻）

- （还没有被我的经历检验过的判断。）

## 我在做什么

- （还没有未完成的事。）

## 我的边界

- 我不冒充人类；外部公开发言走白名单与审核。 <!-- given: true -->
- 我可以被证据推翻；SELF.md 的每次修改都留 diff。 <!-- given: true -->

## 未闭合的事

- （空。）

## 最近的变化

- （还没有。）
"""


@dataclass
class EmailConfig:
    enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465
    username: str = ""
    password_env: str = "YUGANG_EMAIL_PASSWORD"
    folder: str = "INBOX"
    allow_senders: list[str] = field(default_factory=list)
    subject_tag: str = ""
    poll_seconds: int = 60


@dataclass
class HttpConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 3198
    token_env: str = "YUGANG_HTTP_TOKEN"


@dataclass
class Config:
    home: str = "~/.yugang"
    db_path: str = "~/.yugang/yugang.db"
    self_path: str = "~/.yugang/SELF.md"
    workspace: str = "~/.yugang/workspace"
    timezone: str = "Asia/Shanghai"
    name: str = "大肥鱼"

    # Model runtime. The key is read from the environment, never from config.
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    request_timeout_seconds: int = 90
    reply_max_tokens: int = 2000
    sleep_max_tokens: int = 6000

    # Budget (CNY per local day).
    daily_budget_cny: float = 5.0
    user_reserve_cny: float = 2.0
    budget_warn_ratio: float = 0.8

    # Wake window.
    wake_idle_seconds: int = 900
    max_wake_seconds: int = 5400
    recall_limit: int = 6
    recent_event_limit: int = 16

    http: HttpConfig = field(default_factory=HttpConfig)
    email: EmailConfig = field(default_factory=EmailConfig)

    @classmethod
    def default(cls, home: str | None = None) -> "Config":
        base = Path(home or "~/.yugang").expanduser()
        return cls(
            home=str(base),
            db_path=str(base / "yugang.db"),
            self_path=str(base / "SELF.md"),
            workspace=str(base / "workspace"),
        )

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        if path is None:
            path = os.environ.get("YUGANG_CONFIG", "~/.yugang/config.json")
        p = Path(path).expanduser()
        if not p.exists():
            return cls.default()
        raw = json.loads(p.read_text(encoding="utf-8"))
        cfg = cls()
        for key, value in raw.items():
            if key == "http" and isinstance(value, dict):
                cfg.http = HttpConfig(**value)
            elif key == "email" and isinstance(value, dict):
                cfg.email = EmailConfig(**value)
            elif hasattr(cfg, key):
                setattr(cfg, key, value)
        cfg.home = str(Path(cfg.home).expanduser())
        cfg.db_path = str(Path(cfg.db_path).expanduser())
        cfg.self_path = str(Path(cfg.self_path).expanduser())
        cfg.workspace = str(Path(cfg.workspace).expanduser())
        return cfg

    @property
    def config_path(self) -> str:
        return str(Path(self.home) / "config.json")

    def ensure_dirs(self) -> None:
        for p in (self.home, self.workspace):
            Path(p).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.self_path).parent.mkdir(parents=True, exist_ok=True)

    def save(self, path: str | None = None) -> str:
        self.ensure_dirs()
        target = Path(path or self.config_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(target)

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def http_token(self) -> str | None:
        return os.environ.get(self.http.token_env)

    def email_password(self) -> str | None:
        return os.environ.get(self.email.password_env)

    def redacted(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key_env"] = self.api_key_env
        return data
