"""Email alerts, the nightly preview (veto mode) and the weekly summary.

Env: SMTP_USER, SMTP_PASSWORD (for Gmail: an App Password), ALERT_EMAIL_TO,
optional SMTP_HOST (default smtp.gmail.com) and SMTP_PORT (465).
Without them, messages are printed instead (local runs, tests).
"""
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional


def send(subject: str, text: str, html: Optional[str] = None, images: Optional[list[tuple[str, bytes]]] = None):
    """images: (content_id, jpeg_bytes) referenced in html as <img src="cid:content_id">."""
    to, user, pwd = os.getenv("ALERT_EMAIL_TO"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    subject = f"[UpaySutra posts] {subject}"
    if not (to and user and pwd):
        print(f"--- email (not configured) ---\n{subject}\n{text}\n---")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
        part = msg.get_payload()[-1]
        for cid, data in images or []:
            part.add_related(data, maintype="image", subtype="jpeg", cid=f"<{cid}>")
    try:
        with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465"))) as s:
            s.login(user, pwd)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        # An alert must never crash the run it is reporting on: log it and carry on.
        print(f"!! email not sent ({type(e).__name__}: {e}). Check SMTP_USER / SMTP_PASSWORD secrets.\n"
              f"Subject was: {subject}\n{text}")


def alert(problem: str, detail: str = ""):
    send(f"⚠️ {problem}", f"{problem}\n\n{detail}".strip())
