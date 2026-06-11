"""Email templates + provider-agnostic sender for Inspira.

This package ships the HTML + plain-text copy for every transactional email
Inspira sends (welcome, password reset, account deletion, budget warning)
along with a thin ``EmailSender`` abstraction that lets the rest of the app
trigger sends without caring which provider is wired up.

Today the default sender is ``NoopEmailSender`` — it renders the template,
logs it at INFO level, and returns. No network call is made. When the
product picks a provider (Resend / Postmark / Loops), add a concrete
sender class to :mod:`planning_studio_service.mail.sender` and branch on
it inside :func:`get_email_sender`. Callers do not change.

Typical use (once the sender is wired up into ``auth.py`` / ``api.py``)::

    from planning_studio_service.mail import get_email_sender

    sender = get_email_sender()
    sender.send(
        to_email=user["email"],
        template_id="welcome",
        context={"display_name": user["display_name"], "app_url": "https://app.example.com"},
    )
"""
from __future__ import annotations

from .sender import (
    EmailDeliveryError,
    EmailSender,
    NoopEmailSender,
    get_email_sender,
)
from .templates import Template, registry, render, resolve_from_identity

__all__ = [
    "EmailDeliveryError",
    "EmailSender",
    "NoopEmailSender",
    "Template",
    "get_email_sender",
    "registry",
    "render",
    "resolve_from_identity",
]
