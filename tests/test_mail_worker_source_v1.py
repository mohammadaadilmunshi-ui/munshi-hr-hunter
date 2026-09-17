from __future__ import annotations

from pathlib import Path


def test_mail_worker_has_no_personal_forwarding_fallback():
    root = Path(__file__).resolve().parent.parent
    source = (
        root / "infra" / "cloudflare" / "munshi-mail-router.js"
    ).read_text(encoding="utf-8")

    assert "message.forward(" not in source
    assert "mohammadaadilmunshi@gmail.com" not in source
    assert "MUNSHI_MAIL_ENDPOINT" in source
    assert "MUNSHI_MAIL_SIGNING_SECRET" in source
    assert "X-Munshi-Signature" in source
    assert "@mail.munshi.systems" in source
