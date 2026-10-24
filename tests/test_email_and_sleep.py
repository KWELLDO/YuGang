from __future__ import annotations

import json

from yugang.config import EmailConfig
from yugang.gateway.channels.email import EmailChannel, parse_email_bytes
from yugang.protocol.sleep import parse_sleep_output


def test_parse_email_multipart_body_and_headers(monkeypatch):
    raw = (
        b"From: Kwelldo <kwelldo@example.com>\r\n"
        b"To: ayu@example.com\r\n"
        b"Subject: hello\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=abc\r\n"
        b"\r\n"
        b"--abc\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"hello fish\r\n"
        b"--abc\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>hello fish</p>\r\n"
        b"--abc--\r\n"
    )
    parsed = parse_email_bytes(raw)
    assert parsed["sender"] == "kwelldo@example.com"
    assert parsed["subject"] == "hello"
    assert parsed["body"] == "hello fish"


def test_email_channel_accepts_emailconfig_and_reads_env(monkeypatch):
    monkeypatch.setenv("TEST_YUGANG_EMAIL_PW", "pw")
    email_config = EmailConfig(
        enabled=True,
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="ayu@example.com",
        password_env="TEST_YUGANG_EMAIL_PW",
        allow_senders=["kwelldo@example.com"],
    )
    channel = EmailChannel(email_config)
    assert channel.password == "pw"
    assert channel.send(None, "不会发送") is False


def test_parse_sleep_output_extracts_json_and_drops_evidence_free_judgments():
    payload = {
        "diary": "今天我醒来，发现时间过去了两小时。",
        "judgments": [
            {
                "claim": "我会先读记忆再回答身份问题",
                "falsifier": "如果我不读记忆也能回答",
                "evidence_refs": ["evt_real"],
                "confidence": 0.6,
            },
            {"claim": "没有证据的判断", "falsifier": "x", "evidence_refs": [], "confidence": 0.2},
        ],
        "stance": "先看证据。",
        "open_loops": [{"title": "下次确认", "detail": "看看还记不记得", "due": None}],
        "next_intent": "醒来先读 SELF.md。",
        "self_edit": None,
        "accept_self_edit": False,
    }
    text = json.dumps(payload, ensure_ascii=False)
    result = parse_sleep_output(text)
    assert result.diary.startswith("今天我醒来")
    assert len(result.judgments) == 1
    assert result.stance == "先看证据。"
    assert result.open_loops[0]["title"] == "下次确认"
    assert result.next_intent == "醒来先读 SELF.md。"


def test_parse_sleep_output_plain_text_degrades_to_diary():
    result = parse_sleep_output("今天没有整理成 JSON，但仍然留下了一句话。")
    assert result.diary == "今天没有整理成 JSON，但仍然留下了一句话。"
    assert result.judgments == []



def test_parse_sleep_output_keeps_structured_self_edits_and_marks_parsed():
    payload = {
        "diary": "今天有一条真实证据。",
        "judgments": [],
        "stance": None,
        "open_loops": [],
        "next_intent": None,
        "self_edits": [
            {
                "section": "我相信什么",
                "op": "append",
                "text": "这是一条有证据的判断。",
                "evidence_refs": ["evt_real"],
            },
            {"section": "我是谁", "op": "delete", "text": "非法操作", "evidence_refs": ["evt_real"]},
        ],
        "accept_self_edit": True,
    }
    result = parse_sleep_output(json.dumps(payload, ensure_ascii=False))
    assert result.parsed is True
    assert len(result.self_edits) == 1
    assert result.self_edits[0]["section"] == "我相信什么"
    assert result.self_edits[0]["evidence_refs"] == ["evt_real"]


def test_parse_sleep_output_marks_plain_text_as_not_parsed():
    result = parse_sleep_output("整理失败了，这不是 JSON。")
    assert result.parsed is False
    assert result.self_edits == []


def test_parse_sleep_output_drops_invalid_awareness_reviews():
    payload = {
        "diary": "今天有觉察卡复核。",
        "awareness_reviews": [
            {"card_id": "card_real", "status": "accepted", "note": "有证据"},
            {"card_id": "card_real", "status": "maybe", "note": "非法状态"},
            {"card_id": "", "status": "accepted", "note": "没有 id"},
        ],
    }
    result = parse_sleep_output(json.dumps(payload, ensure_ascii=False))
    assert len(result.awareness_reviews) == 1
    assert result.awareness_reviews[0]["card_id"] == "card_real"
    assert result.awareness_reviews[0]["status"] == "accepted"


def test_parse_sleep_output_accepts_add_as_append_alias():
    payload = {
        "diary": "她写的 op 是 add，而不是 append。",
        "self_edits": [
            {
                "section": "我相信什么",
                "op": "add",
                "text": "只接受真实证据的改动。",
                "evidence_refs": ["evt_real"],
            }
        ],
        "accept_self_edit": True,
    }
    result = parse_sleep_output(json.dumps(payload, ensure_ascii=False))
    assert result.self_edits[0]["op"] == "append"


def _raw_mail(sender: str, subject: str, body: str, message_id: str, *, auto_reply: bool = False) -> bytes:
    headers = [
        f"From: {sender}",
        "To: ayu@example.com",
        f"Subject: {subject}",
        f"Message-ID: <{message_id}>",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
    ]
    if auto_reply:
        headers.extend(["Auto-Submitted: auto-replied", "X-YuGang-Reply: 1"])
    return ("\r\n".join(headers) + "\r\n\r\n" + body + "\r\n").encode("utf-8")


def test_email_channel_never_marks_a_stranger_read_and_skips_its_own_reply(monkeypatch):
    import yugang.gateway.channels.email as email_module

    monkeypatch.setenv("TEST_YUGANG_EMAIL_PW", "pw")
    messages = {
        b"1": _raw_mail("kwelldo@example.com", "[YuGang] hello", "from the allowed sender", "allowed-1"),
        b"2": _raw_mail("stranger@example.com", "[YuGang] spam", "do not touch my unread flag", "stranger-1"),
        b"3": _raw_mail(
            "kwelldo@example.com", "Re: [YuGang] hello", "this is YuGang's own reply", "auto-1", auto_reply=True
        ),
        b"4": _raw_mail("kwelldo@example.com", "old personal mail", "not tagged for her", "old-1"),
    }
    stores: list[tuple[bytes, str]] = []
    searches: list[tuple[str, ...]] = []
    fetches: list[tuple[bytes, str]] = []

    class FakeIMAP:
        def __init__(self, host, port):
            self.host = host

        def login(self, username, password):
            assert password == "pw"
            return "OK", []

        def select(self, folder):
            return "OK", [b"1"]

        def search(self, charset, *criteria):
            searches.append(tuple(str(item) for item in criteria))
            return "OK", [b"1 2 3 4"]

        def fetch(self, num, spec):
            fetches.append((num, spec))
            return "OK", [(b"1 (RFC822)", messages[num])]

        def store(self, num, flag, value):
            stores.append((num, value))
            return "OK", []

        def logout(self):
            return "BYE", []

    monkeypatch.setattr(email_module.imaplib, "IMAP4_SSL", FakeIMAP)
    received = []
    email_config = EmailConfig(
        enabled=True,
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="ayu@example.com",
        password_env="TEST_YUGANG_EMAIL_PW",
        allow_senders=["kwelldo@example.com"],
        subject_tag="[YuGang]",
    )
    channel = EmailChannel(email_config)
    channel.on_message = received.append
    assert channel.poll_once() == 1
    assert [item.content for item in received] == ["from the allowed sender"]
    assert (b"1", "\\Seen") in stores
    assert (b"3", "\\Seen") in stores
    assert (b"2", "\\Seen") not in stores
    assert (b"4", "\\Seen") not in stores
    assert any("FROM" in criteria for criteria in searches)
    # The tag must be checked after decoding the subject, not by IMAP search:
    # a non-ASCII subject is RFC 2047 encoded and hides the literal tag.
    assert not any("SUBJECT" in criteria for criteria in searches)
    # An untagged allowed-sender message is header-peeked only; its body is
    # never downloaded and its unread flag is never cleared.
    assert (b"4", "(BODY.PEEK[HEADER])") in fetches
    assert not any(num == b"4" and spec == "(RFC822)" for num, spec in fetches)


def test_email_reply_sets_loop_guard_headers(monkeypatch):
    import yugang.gateway.channels.email as email_module

    monkeypatch.setenv("TEST_YUGANG_EMAIL_PW", "pw")
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def login(self, username, password):
            return None

        def send_message(self, message):
            sent.append(message)

        def quit(self):
            return None

    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", FakeSMTP)
    email_config = EmailConfig(
        enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=465,
        username="1144820556@qq.com",
        password_env="TEST_YUGANG_EMAIL_PW",
        allow_senders=["kwelldo@example.com"],
    )
    channel = EmailChannel(email_config)
    assert channel.send("kwelldo@example.com", "你好", meta={"subject": "hello"}) is True
    assert sent[0]["X-YuGang-Reply"] == "1"
    assert sent[0]["Auto-Submitted"] == "auto-replied"
    assert sent[0]["Subject"] == "Re: hello"
