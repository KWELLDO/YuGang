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
    # Where to send her self-initiated output (autonomous wakes, awareness
    # cards). Empty falls back to allow_senders, i.e. the operator's address.
    notify_to: list[str] = field(default_factory=list)
    subject_tag: str = ""
    poll_seconds: int = 60


@dataclass
class OneBotConfig:
    """OneBot v11 bridge (QQ via NapCat, or any compatible implementation).

    Disabled by default: the bridge is only useful once a human has logged a
    dedicated QQ account into NapCat. The webhook is loopback-only by default
    and fails closed unless an allowed user id is configured.
    """

    enabled: bool = False
    api_base: str = "http://127.0.0.1:3000"
    access_token_env: str = "YUGANG_ONEBOT_TOKEN"
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 3197
    webhook_secret_env: str = "YUGANG_ONEBOT_WEBHOOK_SECRET"
    allowed_user_ids: list[str] = field(default_factory=list)
    request_timeout_seconds: int = 30
    max_message_chars: int = 1500


@dataclass
class QQConfig:
    """QQ official bot platform (bot.q.qq.com), webhook mode.

    Not NapCat: the platform signs every callback with Ed25519 using the bot
    secret, and it addresses people by openid, never by QQ number. Disabled by
    default; the webhook refuses to start without a secret, so a misconfiguration
    cannot leave an unauthenticated public endpoint on the internet.
    """

    enabled: bool = False
    app_id: str = ""
    app_secret_env: str = "YUGANG_QQ_APP_SECRET"
    api_base: str = "https://api.sgroup.qq.com"
    token_url: str = "https://bots.qq.com/app/getAppAccessToken"
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 3196
    # OpenIDs, not QQ numbers: the platform does not publish a mapping.
    allowed_openids: list[str] = field(default_factory=list)
    request_timeout_seconds: int = 30
    max_message_chars: int = 1000


@dataclass
class HttpConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 3198
    token_env: str = "YUGANG_HTTP_TOKEN"


@dataclass
class WorldConfig:
    """What she may do to the world outside her memory.

    Reading is reversible, acting is not, so the two halves are independent
    switches. The sandbox has no network at all: that is what makes running a
    command something she does not have to ask permission for, because a process
    that cannot reach out cannot publish, post or leak.
    """

    enabled: bool = True
    fetch_enabled: bool = True
    search_enabled: bool = True
    files_enabled: bool = True
    sandbox_enabled: bool = True

    fetch_timeout_seconds: float = 20.0
    fetch_max_bytes: int = 2_000_000
    fetch_max_chars: int = 20_000
    search_limit: int = 5
    search_timeout_seconds: float = 15.0

    file_max_bytes: int = 1_000_000
    file_max_chars: int = 20_000

    sandbox_timeout_seconds: float = 30.0
    sandbox_memory_mb: int = 1024
    sandbox_cpu_seconds: int = 60
    sandbox_max_output_chars: int = 6000
    sandbox_max_file_mb: int = 16
    sandbox_backend: str = "auto"  # auto | bubblewrap | unshare


@dataclass
class SelfChangeConfig:
    """Her lane for changing herself: a writable mirror, tests, and proposals.

    She may write her own copy and run its tests. Only the root-side applier can
    touch the live tree, and it always leaves a rollback point behind. That
    asymmetry is the whole point: she can try things, she cannot skip review.
    """

    enabled: bool = True
    source_root: str = ""  # 空 = <home>/../yugang，即 live 副本
    venv_python: str = "/opt/yugang-venv/bin/python"
    max_file_bytes: int = 200_000
    test_timeout_seconds: float = 600.0


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
    # A message from these senders counts as the operator's own words even when
    # it arrives on a channel strangers can also reach.
    trusted_senders: list[str] = field(default_factory=lambda: ["kwelldo"])

    # Read-only memory tools during a waking conversation.
    memory_tools_enabled: bool = True
    max_tool_rounds: int = 3

    # Budget (CNY per local day).
    daily_budget_cny: float = 5.0
    user_reserve_cny: float = 2.0
    budget_warn_ratio: float = 0.8

    # Wake window.
    wake_idle_seconds: int = 900
    max_wake_seconds: int = 5400
    recall_limit: int = 6
    recent_event_limit: int = 16

    # Self-set wake alarms. Disabled until she has enough lived days.
    self_alarms_enabled: bool = True
    self_alarms_after_days: int = 7
    alarm_min_minutes: int = 30
    alarm_max_minutes: int = 1560
    alarm_max_per_day: int = 6

    http: HttpConfig = field(default_factory=HttpConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    qq: QQConfig = field(default_factory=QQConfig)
    world: WorldConfig = field(default_factory=WorldConfig)
    self_change: SelfChangeConfig = field(default_factory=SelfChangeConfig)

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
            elif key == "onebot" and isinstance(value, dict):
                cfg.onebot = OneBotConfig(**value)
            elif key == "qq" and isinstance(value, dict):
                cfg.qq = QQConfig(**value)
            elif key == "world" and isinstance(value, dict):
                cfg.world = WorldConfig(**value)
            elif key == "self_change" and isinstance(value, dict):
                cfg.self_change = SelfChangeConfig(**value)
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

    @property
    def pause_file(self) -> str:
        """Operator switch: presence means no model calls, no autonomous wake."""
        return str(Path(self.home) / "PAUSED")

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

    def onebot_token(self) -> str | None:
        return os.environ.get(self.onebot.access_token_env)

    def onebot_webhook_secret(self) -> str | None:
        return os.environ.get(self.onebot.webhook_secret_env)

    def qq_app_secret(self) -> str | None:
        return os.environ.get(self.qq.app_secret_env)

    def redacted(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key_env"] = self.api_key_env
        return data