"""Email sending module for LLM Benchmark Studio.

Uses Python stdlib smtplib — zero new dependencies.

Configuration via environment variables:
  SMTP_HOST      SMTP server hostname (default: localhost)
  SMTP_PORT      SMTP server port (default: 587)
  SMTP_USER      SMTP username (optional)
  SMTP_PASSWORD  SMTP password (optional)
  SMTP_FROM      Sender address (default: noreply@benchmark.local)
  APP_BASE_URL   App base URL for building links (default: http://localhost:8501)

Dev mode: if SMTP_HOST is not set (or is 'localhost' with no SMTP_USER), the
reset URL is logged instead of emailed. This allows local development without
an SMTP server.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "noreply@benchmark.local")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8501").rstrip("/")


def _is_smtp_configured() -> bool:
    """Return True if SMTP is configured for real email sending."""
    return bool(SMTP_HOST and SMTP_HOST != "localhost")


def send_password_reset_email(to_email: str, raw_token: str) -> bool:
    """Send a password reset email.

    Returns True on success. In dev mode (no SMTP configured), logs the reset
    URL and returns True so the caller treats it as a success.

    Args:
        to_email: Recipient email address.
        raw_token: The raw (un-hashed) reset token.

    Returns:
        True if sent (or logged in dev mode), False on SMTP error.
    """
    reset_url = f"{APP_BASE_URL}/reset-password?token={raw_token}"

    if not _is_smtp_configured():
        logger.info(
            "DEV MODE — password reset link for %s: %s",
            to_email, reset_url,
        )
        return True

    subject = "Reset your LLM Benchmark Studio password"
    body_text = (
        f"You requested a password reset for your LLM Benchmark Studio account.\n\n"
        f"Click the link below to reset your password (valid for 1 hour):\n\n"
        f"{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email.\n"
    )
    body_html = f"""\
<html>
  <body>
    <p>You requested a password reset for your <strong>LLM Benchmark Studio</strong> account.</p>
    <p>
      <a href="{reset_url}">Reset your password</a>
      (valid for 1 hour)
    </p>
    <p>If you didn't request this, you can safely ignore this email.</p>
  </body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            if SMTP_PORT == 587:
                server.starttls()
                server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        logger.info("Password reset email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send password reset email to %s", to_email)
        return False
