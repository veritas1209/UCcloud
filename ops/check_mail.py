"""Send a configuration test only to the configured sender; never display credentials."""
import smtplib
import ssl
from email.message import EmailMessage
from backend import recovery

if not recovery.configured():
    raise SystemExit("SMTP credential missing or reset origin invalid")
message = EmailMessage()
message["From"] = f"UCcloud <{recovery.FROM}>"
message["To"] = recovery.FROM
message["Subject"] = "UCcloud 메일 발송 확인"
message.set_content("UCcloud에서 메일을 정상적으로 보냈습니다. 비밀번호 재설정 메일 발송 설정 확인용입니다.")
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as smtp:
        smtp.login(recovery.FROM, recovery.smtp_password())
        smtp.send_message(message)
except Exception as error:
    raise SystemExit(f"Mail test failed ({type(error).__name__}); credential not displayed") from None
print("Gmail accepted the test message to the sender account")
