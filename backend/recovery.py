"""One-use email password recovery; tokens are stored only as hashes."""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

LOG = logging.getLogger("cloud_portal")
FROM = "fluction.official@gmail.com"
RESET_ORIGIN = os.getenv("PORTAL_RESET_ORIGIN", "https://comsv-03.tail5819db.ts.net").rstrip("/")


def smtp_password():
    directory = os.getenv("CREDENTIALS_DIRECTORY", "")
    if not directory:
        return ""
    try:
        return (Path(directory) / "smtp-password").read_text().strip().replace(" ", "")
    except OSError:
        return ""


def configured():
    url = urlsplit(RESET_ORIGIN)
    return bool(smtp_password() and url.scheme == "https" and url.hostname and not url.username and not url.query and not url.fragment and not url.path)


def send_reset(email, token):
    message = EmailMessage()
    message["From"] = f"UCcloud <{FROM}>"
    message["To"] = email
    message["Subject"] = "UCcloud 비밀번호 변경"
    message.set_content(f"아래 주소를 열고 새 비밀번호를 입력해 주세요.\n\n{RESET_ORIGIN}/reset-password#token={token}\n\n이 주소는 30분 동안 한 번 사용할 수 있습니다. 요청하지 않았다면 이 메일을 무시하세요.\n")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as smtp:
            smtp.login(FROM, smtp_password())
            smtp.send_message(message)
        return True
    except Exception as error:
        LOG.error("Password reset mail delivery failed (%s)", type(error).__name__)
        return False
