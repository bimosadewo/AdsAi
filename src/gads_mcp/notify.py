"""Pengiriman laporan/alert ke klien: WhatsApp (Fonnte / webhook umum) dan email (SMTP).

Default ADSAI_WA_PROVIDER=console: pesan hanya ditampilkan, tidak dikirim ke mana pun.
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Callable

Transport = Callable[[urllib.request.Request], tuple[int, str]]


def _default_transport(req: urllib.request.Request) -> tuple[int, str]:
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (URL dari konfigurasi admin)
        return resp.status, resp.read().decode("utf-8", "replace")


def _fill(template: Any, values: dict[str, str]) -> Any:
    """Ganti {phone}/{message} di dalam struktur JSON tanpa merusak escaping."""
    if isinstance(template, str):
        out = template
        for k, v in values.items():
            out = out.replace("{" + k + "}", v)
        return out
    if isinstance(template, list):
        return [_fill(x, values) for x in template]
    if isinstance(template, dict):
        return {k: _fill(v, values) for k, v in template.items()}
    return template


@dataclass
class SendResult:
    channel: str
    to: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kanal": self.channel, "ke": self.to, "berhasil": self.ok, "detail": self.detail[:300]}


class WhatsApp:
    def __init__(self, env: dict[str, str] | None = None, transport: Transport | None = None):
        env = dict(os.environ if env is None else env)
        self.provider = (env.get("ADSAI_WA_PROVIDER") or "console").lower()
        self.env = env
        self.transport = transport or _default_transport
        if self.provider not in ("console", "fonnte", "webhook"):
            raise RuntimeError(f"ADSAI_WA_PROVIDER '{self.provider}' tidak dikenal (console | fonnte | webhook).")

    def _request(self, phone: str, message: str) -> urllib.request.Request:
        if self.provider == "fonnte":
            token = self.env.get("ADSAI_FONNTE_TOKEN")
            if not token:
                raise RuntimeError("ADSAI_FONNTE_TOKEN belum diisi.")
            data = urllib.parse.urlencode({"target": phone, "message": message}).encode()
            return urllib.request.Request("https://api.fonnte.com/send", data=data, method="POST",
                                          headers={"Authorization": token})
        url = self.env.get("ADSAI_WA_WEBHOOK_URL")
        if not url:
            raise RuntimeError("ADSAI_WA_WEBHOOK_URL belum diisi.")
        headers = json.loads(self.env.get("ADSAI_WA_WEBHOOK_HEADERS") or "{}")
        body_tpl = json.loads(self.env.get("ADSAI_WA_WEBHOOK_BODY") or '{"phone": "{phone}", "message": "{message}"}')
        body = json.dumps(_fill(body_tpl, {"phone": phone, "message": message})).encode()
        headers.setdefault("Content-Type", "application/json")
        return urllib.request.Request(url, data=body, method="POST", headers=headers)

    def send(self, phone: str, message: str) -> SendResult:
        if self.provider == "console":
            print(f"\n===== [WA console → {phone}] =====\n{message}\n=====")
            return SendResult("whatsapp:console", phone, True, "Ditampilkan saja (mode console, tidak dikirim).")
        try:
            status, text = self.transport(self._request(phone, message))
        except Exception as ex:  # jaringan / HTTP error
            return SendResult(f"whatsapp:{self.provider}", phone, False, str(ex))
        ok = 200 <= status < 300 and '"status":false' not in text.replace(" ", "")
        return SendResult(f"whatsapp:{self.provider}", phone, ok, f"HTTP {status}: {text}")


class Email:
    def __init__(self, env: dict[str, str] | None = None, smtp_factory: Callable[..., Any] | None = None):
        env = dict(os.environ if env is None else env)
        self.host = env.get("ADSAI_SMTP_HOST")
        self.port = int(env.get("ADSAI_SMTP_PORT") or 587)
        self.user = env.get("ADSAI_SMTP_USER")
        self.password = env.get("ADSAI_SMTP_PASSWORD")
        self.sender = env.get("ADSAI_SMTP_FROM") or self.user
        self.smtp_factory = smtp_factory or smtplib.SMTP

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    def send(self, to: str, subject: str, body: str) -> SendResult:
        if not self.configured:
            return SendResult("email", to, False, "SMTP belum dikonfigurasi (ADSAI_SMTP_HOST/ADSAI_SMTP_FROM).")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body.replace("*", ""))
        try:
            with self.smtp_factory(self.host, self.port, timeout=30) as smtp:
                smtp.starttls()
                if self.user:
                    smtp.login(self.user, self.password or "")
                smtp.send_message(msg)
        except Exception as ex:
            return SendResult("email", to, False, str(ex))
        return SendResult("email", to, True, "Terkirim.")


def deliver(client: Any, subject: str, message: str, whatsapp: WhatsApp | None = None,
            email: Email | None = None) -> list[SendResult]:
    """Kirim ke semua kontak klien (WA + email yang terkonfigurasi)."""
    results: list[SendResult] = []
    wa = whatsapp or WhatsApp()
    for phone in client.whatsapp:
        results.append(wa.send(phone, message))
    mail = email or Email()
    if mail.configured:
        for addr in client.email:
            results.append(mail.send(addr, subject, message))
    if not results:
        results.append(SendResult("-", "-", False, "Klien tidak punya kontak WhatsApp/email di clients.yaml."))
    return results
