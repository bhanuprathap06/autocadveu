"""Operational paths used by hosted deployments: HTTPS email delivery, operator email verification, S3 addressing."""

import io
import json
import urllib.error

import pytest


@pytest.fixture()
def settings(monkeypatch):
    from autoforge.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", None)
    monkeypatch.setattr(s, "resend_api_key", None)
    return s


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _queue(to: str) -> None:
    from autoforge.core import mail
    from autoforge.db.session import session_scope

    with session_scope() as db:
        mail.enqueue(db, to, "Verify your AutoForge email", "Open https://example.test/verify?token=abc", "verify_email")


def _outbox(to: str):
    from sqlalchemy import select

    from autoforge.db import models as M
    from autoforge.db.session import session_scope

    with session_scope() as db:
        r = db.scalar(select(M.EmailOutbox).where(M.EmailOutbox.to_email == to))
        return r.sent_at, r.attempts, r.last_error


def test_mail_stays_in_outbox_without_provider(db_clean, settings):
    from autoforge.core import mail
    from autoforge.db.session import session_scope

    _queue("nobody@example.com")
    with session_scope() as db:
        assert mail.deliver_pending(db) == 0
    assert _outbox("nobody@example.com") == (None, 0, None)


def test_resend_delivery(db_clean, settings, monkeypatch):
    from autoforge.core import mail
    from autoforge.db.session import session_scope

    sent = []

    def fake_urlopen(req, timeout, context):
        sent.append((req.full_url, req.get_method(), dict(req.header_items()), json.loads(req.data)))
        return _Resp()

    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "smtp_from", "AutoForge <onboarding@resend.dev>")
    monkeypatch.setattr(mail.urllib.request, "urlopen", fake_urlopen)
    _queue("owner@example.com")
    with session_scope() as db:
        assert mail.deliver_pending(db) == 1
    url, method, headers, body = sent[0]
    assert (url, method) == ("https://api.resend.com/emails", "POST")
    assert headers["Authorization"] == "Bearer re_test_key"
    assert body == {"from": "AutoForge <onboarding@resend.dev>", "to": ["owner@example.com"],
                    "subject": "Verify your AutoForge email", "text": "Open https://example.test/verify?token=abc"}
    sent_at, attempts, err = _outbox("owner@example.com")
    assert sent_at is not None and attempts == 1 and err is None


def test_resend_rejection_is_recorded_and_retried(db_clean, settings, monkeypatch):
    from autoforge.core import mail
    from autoforge.db.session import session_scope

    def reject(req, timeout, context):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                     io.BytesIO(b'{"message":"You can only send testing emails to your own email address"}'))

    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(mail.urllib.request, "urlopen", reject)
    _queue("other@example.com")
    with session_scope() as db:
        assert mail.deliver_pending(db) == 0
    sent_at, attempts, err = _outbox("other@example.com")
    assert sent_at is None and attempts == 1
    assert "HTTP 403" in err and "own email address" in err


def test_verify_user_cli(client):
    from autoforge import cli

    client.register(email="ops@example.com")
    assert not client.get("/auth/me").json()["user"]["email_verified"]
    cli.verify_user("OPS@example.com")
    assert client.get("/auth/me").json()["user"]["email_verified"]
    with pytest.raises(SystemExit):
        cli.verify_user("missing@example.com")


@pytest.mark.parametrize("style,endpoint,expected", [
    (None, "http://objects:8333", "path"),
    (None, None, "auto"),
    ("virtual", "https://storage.example.test", "virtual"),
])
def test_s3_addressing_style(settings, monkeypatch, style, endpoint, expected):
    from autoforge.core.storage import S3Storage

    monkeypatch.setattr(settings, "s3_addressing_style", style)
    monkeypatch.setattr(settings, "s3_endpoint_url", endpoint)
    monkeypatch.setattr(settings, "s3_access_key_id", "k")
    monkeypatch.setattr(settings, "s3_secret_access_key", "s")
    assert S3Storage().client.meta.config.s3["addressing_style"] == expected


def test_listen_socket_accepts_ipv4():
    import socket

    from autoforge.serve import listen_socket

    srv = listen_socket(0)
    try:
        port = srv.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            conn, _ = srv.accept()
            conn.close()
        if srv.family == socket.AF_INET6:  # dual-stack: IPv6 too, and IPv4 was not excluded
            assert srv.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 0
            with socket.create_connection(("::1", port), timeout=2):
                srv.accept()[0].close()
    finally:
        srv.close()
