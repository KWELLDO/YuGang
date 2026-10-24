from __future__ import annotations

import email
import imaplib
import os
import re
import smtplib
import threading
import time
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import parseaddr
from typing import Any, Callable

from .base import IncomingMessage


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def extract_text(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace").strip()
        for part in message.walk():
            if part.get_content_type() == "text/html" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="replace")
                return re.sub(r"<[^>]+>", " ", html).strip()
        return ""
    payload = message.get_payload(decode=True) or b""
    charset = message.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace").strip()


def parse_email_bytes(raw: bytes) -> dict[str, str]:
    message = email.message_from_bytes(raw)
    sender = parseaddr(_decode_header(message.get("From")))[1]
    subject = _decode_header(message.get("Subject"))
    message_id = (message.get("Message-ID") or "").strip()
    body = extract_text(message)
    return {"sender": sender, "subject": subject, "body": body, "message_id": message_id}


class EmailChannel:
    name = "email"

    def __init__(self, config: Any):
        self.config = config
        self.on_message: Callable[[IncomingMessage], Any] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Accept either the full Config or the EmailConfig sub-dataclass.
        if hasattr(config, "email_password"):
            self.password = config.email_password()
        else:
            self.password = os.environ.get(getattr(config, "password_env", "YUGANG_EMAIL_PASSWORD"))

    def start(self, on_message: Callable[[IncomingMessage], Any]) -> None:
        self.on_message = on_message
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="yugang-email", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        interval = max(15, int(getattr(self.config, "poll_seconds", 60)))
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass
            self._stop.wait(interval)

    def poll_once(self) -> int:
        if not self.password:
            return 0
        imap = imaplib.IMAP4_SSL(self.config.imap_host, int(self.config.imap_port))
        try:
            imap.login(self.config.username, self.password)
            imap.select(self.config.folder or "INBOX")
            status, data = imap.search(None, "UNSEEN")
            if status != "OK":
                return 0
            count = 0
            for num in data[0].split()[:20]:
                status, fetched = imap.fetch(num, "(RFC822)")
                if status != "OK" or not fetched:
                    continue
                raw = fetched[0][1]
                parsed = parse_email_bytes(raw)
                if not parsed["body"]:
                    imap.store(num, "+FLAGS", "\\Seen")
                    continue
                allowed = self.config.allow_senders
                if allowed and parsed["sender"] not in allowed:
                    imap.store(num, "+FLAGS", "\\Seen")
                    continue
                incoming = IncomingMessage(
                    channel="email",
                    content=parsed["body"],
                    sender=parsed["sender"],
                    external_id=parsed["message_id"] or f"imap-{num.decode()}",
                    meta={"subject": parsed["subject"]},
                )
                if self.on_message is not None:
                    self.on_message(incoming)
                imap.store(num, "+FLAGS", "\\Seen")
                count += 1
            return count
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def send(self, recipient: str | None, content: str, *, meta: dict[str, Any] | None = None) -> bool:
        if not recipient or not self.password:
            return False
        message = EmailMessage()
        message["From"] = self.config.username
        message["To"] = recipient
        subject = (meta or {}).get("subject") or "来自大肥鱼"
        message["Subject"] = subject
        message.set_content(content)
        try:
            if int(self.config.smtp_port) == 465:
                with smtplib.SMTP_SSL(self.config.smtp_host, int(self.config.smtp_port), timeout=30) as smtp:
                    smtp.login(self.config.username, self.password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.config.smtp_host, int(self.config.smtp_port), timeout=30) as smtp:
                    smtp.starttls()
                    smtp.login(self.config.username, self.password)
                    smtp.send_message(message)
            return True
        except Exception:
            return False
