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

