"""Optional SMTP mail. Without HSA_SMTP_HOST nothing is sent (admins can issue reset links instead)."""
import logging
import smtplib
from email.message import EmailMessage

from .config import get_settings

log = logging.getLogger(__name__)


def send_mail(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if not s.smtp_host or not s.smtp_from:
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as ex:
        log.error("mail to %s failed: %s", to, ex)
        return False
