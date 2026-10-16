"""
Sending email: only ever a password-reset link.

Optional. Most installations run on one computer, or behind a private network,
with no mail server to hand -- and there the person who forgot a password is
usually the person who runs RadiCal, who resets it with
`python -m app.reset_password`. So nothing here is required to start, and the
sign-in page offers "email me a link" only when this is configured.

Configured from the environment, read on each call like the signing key:

    RADICAL_PUBLIC_URL   the address people open RadiCal at, which the link
                         in the email points to
    RADICAL_SMTP_HOST    the mail server
    RADICAL_SMTP_PORT    587 by default (465 with RADICAL_SMTP_SECURITY=ssl)
    RADICAL_SMTP_SECURITY  starttls (default), ssl, or none
    RADICAL_SMTP_USER / RADICAL_SMTP_PASSWORD   if the server wants a login
    RADICAL_SMTP_FROM    the sender; RADICAL_SMTP_USER if unset

RADICAL_PUBLIC_URL is required, not inferred from the request. The request's
own Host or Origin header is whatever the sender says it is, and building the
link from it lets anyone ask for a reset email to a real user whose link points
at their own server -- one click, and the token is theirs.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

_SECURITY = ('starttls', 'ssl', 'none')


@dataclass(frozen=True)
class MailSettings:
    public_url: str
    host: str
    port: int
    security: str
    user: Optional[str]
    password: Optional[str]
    sender: str


def settings() -> Optional[MailSettings]:
    """The configuration, or None when email is not set up."""
    public_url = os.getenv('RADICAL_PUBLIC_URL', '').strip().rstrip('/')
    host = os.getenv('RADICAL_SMTP_HOST', '').strip()
    user = os.getenv('RADICAL_SMTP_USER', '').strip() or None
    sender = os.getenv('RADICAL_SMTP_FROM', '').strip() or user
    if not (public_url and host and sender):
        return None
    security = os.getenv('RADICAL_SMTP_SECURITY', 'starttls').strip().lower()
    if security not in _SECURITY:
        security = 'starttls'
    default_port = 465 if security == 'ssl' else 587
    try:
        port = int(os.getenv('RADICAL_SMTP_PORT', '') or default_port)
    except ValueError:
        port = default_port
    return MailSettings(
        public_url=public_url, host=host, port=port, security=security, user=user,
        password=os.getenv('RADICAL_SMTP_PASSWORD') or None, sender=sender)


def is_configured() -> bool:
    return settings() is not None


def reset_link(config: MailSettings, token: str) -> str:
    # In the fragment, not the query: a fragment never reaches a server, so the
    # token stays out of access logs and Referer headers.
    return f'{config.public_url}/reset-password#token={token}'


def send(config: MailSettings, to: str, subject: str, body: str) -> None:
    """Send one plain-text message. Raises on failure; the caller decides what that means."""
    message = EmailMessage()
    message['From'] = config.sender
    message['To'] = to
    message['Subject'] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    if config.security == 'ssl':
        server = smtplib.SMTP_SSL(config.host, config.port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=30)
    with server:
        if config.security == 'starttls':
            server.starttls(context=context)
        if config.user and config.password:
            server.login(config.user, config.password)
        server.send_message(message)


def send_reset_link(config: MailSettings, to: str, token: str) -> None:
    link = reset_link(config, token)
    send(config, to, 'Reset your RadiCal password', (
        'Someone, hopefully you, asked to reset the password for this RadiCal account.\n\n'
        f'To choose a new password, open this link within the next hour:\n\n{link}\n\n'
        'The link works once. If you did not ask for this, ignore this email: '
        'your password has not changed.\n'
    ))
