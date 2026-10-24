from __future__ import annotations

import email
import imaplib
import os
import re
import smtplib
import threading
from datetime import datetime, timezone
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


def parse_email_bytes(raw: bytes) -> dict[str, str | bool]:
    message = email.message_from_bytes(raw)
    sender = parseaddr(_decode_header(message.get("From")))[1]
    subject = _decode_header(message.get("Subject"))
    message_id = (message.get("Message-ID") or "").strip()
    body = extract_text(message)
    auto_submitted = str(message.get("Auto-Submitted") or "").strip().lower()
    auto_reply = bool(message.get("X-YuGang-Reply")) or (
        bool(auto_submitted) and auto_submitted != "no"
    )
    return {
        "sender": sender,
        "subject": subject,
        "body": body,
        "message_id": message_id,
        "auto_reply": auto_reply,
    }


class EmailChannel:
    name = "email"

    def __init__(self, config: Any):
        self.config = config
        self.on_message: Callable[[IncomingMessage], Any] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._health_lock = threading.Lock()
        self._started = False
        self._last_poll_at: str | None = None
        self._last_success_at: str | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0
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
        self._started = True

    def stop(self) -> None:
        self._stop.set()
        self._started = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        interval = max(15, int(getattr(self.config, "poll_seconds", 60)))
        while not self._stop.is_set():
            self._poll_and_record()
            self._stop.wait(interval)

    def _poll_and_record(self) -> int:
        """Poll once and keep an honest, operator-visible health record.

        A dead IMAP login used to be swallowed here: the process stayed
        healthy-looking while no message could ever arrive. The poller now
        records the failure and the gateway surfaces it in /health.
        """
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        try:
            count = self.poll_once()
        except Exception as exc:
            with self._health_lock:
                self._last_poll_at = stamp
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._consecutive_failures += 1
            return 0
        with self._health_lock:
            self._last_poll_at = stamp
            self._last_success_at = stamp
            self._last_error = None
            self._consecutive_failures = 0
        return count

    def health(self) -> dict[str, Any]:
        with self._health_lock:
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            if not self._started:
                healthy = None if self._last_poll_at is None else self._consecutive_failures == 0
                last_error = self._last_error
            elif not thread_alive:
                healthy = False
                last_error = self._last_error or "email poller thread is not alive"
            elif self._last_poll_at is None:
                healthy = None
                last_error = self._last_error
            else:
                healthy = self._consecutive_failures == 0
                last_error = self._last_error
            return {
                "name": self.name,
                "healthy": healthy,
                "thread_alive": thread_alive,
                "last_poll_at": self._last_poll_at,
                "last_success_at": self._last_success_at,
                "consecutive_failures": self._consecutive_failures,
                "last_error": last_error,
            }

    def _unseen_numbers(self, imap: Any) -> list[bytes]:
        senders = [
            str(sender).strip()
            for sender in (getattr(self.config, "allow_senders", None) or [])
            if str(sender).strip()
        ]
        # Do not use server-side SUBJECT search for the tag: as soon as the
        # subject contains a non-ASCII character (which is normal here), the
        # whole header is RFC 2047 encoded and the literal tag no longer
        # appears in the raw bytes. Search by sender, fetch headers, decode,
        # then apply the tag filter locally.
        base: list[str] = ["UNSEEN"]
        if not senders:
            status, data = imap.search(None, *base)
            return list(data[0].split()) if status == "OK" and data and data[0] else []
        numbers: set[bytes] = set()
        for sender in senders:
            status, data = imap.search(None, *base, "FROM", f'"{sender}"')
            if status == "OK" and data and data[0]:
                numbers.update(data[0].split())
        return sorted(numbers, key=lambda value: int(value))

    def poll_once(self) -> int:
        if not self.password:
            return 0
        imap = imaplib.IMAP4_SSL(self.config.imap_host, int(self.config.imap_port))
        try:
            imap.login(self.config.username, self.password)
            imap.select(self.config.folder or "INBOX")
            allowed = {
                str(sender).strip().lower()
                for sender in (getattr(self.config, "allow_senders", None) or [])
                if str(sender).strip()
            }
            count = 0
            for num in self._unseen_numbers(imap)[:20]:
                # Fetch the header first. This mailbox also contains a human's
                # personal mail; we do not want to pull every old body on every
                # poll just to discover that it has no tag.
                status, fetched = imap.fetch(num, "(BODY.PEEK[HEADER])")
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                header = parse_email_bytes(fetched[0][1])
                sender = str(header["sender"] or "")
                # Never touch a stranger's unread flag. Only mail from an
                # allowed sender, or our own auto-reply, may be marked seen.
                if header["auto_reply"]:
                    imap.store(num, "+FLAGS", "\\Seen")
                    continue
                tag = str(getattr(self.config, "subject_tag", "") or "").strip()
                if tag and tag not in str(header["subject"] or ""):
                    # This mailbox is also a human inbox. A tag is the only
                    # thing that turns a message into a message for her.
                    continue
                if allowed and sender.lower() not in allowed:
                    continue
                status, fetched = imap.fetch(num, "(RFC822)")
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                parsed = parse_email_bytes(fetched[0][1])
                if not parsed["body"]:
                    imap.store(num, "+FLAGS", "\\Seen")
                    continue
                incoming = IncomingMessage(
                    channel="email",
                    content=str(parsed["body"]),
                    sender=sender,
                    external_id=str(parsed["message_id"] or f"imap-{num.decode()}"),
                    meta={"subject": str(parsed["subject"] or "")},
                )
                try:
                    if self.on_message is not None:
                        self.on_message(incoming)
                except Exception:
                    # Leave it unread so the next poll can retry it.
                    continue
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
        subject = str((meta or {}).get("subject") or "来自大肥鱼").strip()
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        message["Subject"] = subject
        # RFC 3834 plus a private marker: the poller must never answer its own
        # reply and create a mail loop when the sender and mailbox are the same.
        message["Auto-Submitted"] = "auto-replied"
        message["X-YuGang-Reply"] = "1"
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
