"""Real customer message delivery: SMTP e-mail and an SMS gateway webhook.

Enabled with AMA_DELIVERY=live. Messages are queued as "pending-delivery" inside the order transaction and sent
here, outside it, so a slow provider never holds the state lock. Failures are retried up to MAX_ATTEMPTS.
"""

import logging
import os
import smtplib
from email.message import EmailMessage

import httpx

from app.auth import CODE_SECONDS, INVITE_SECONDS
from app.domain import now, tracking_token

log = logging.getLogger("ama.delivery")
MAX_ATTEMPTS = 3


def configuration_problems():
    problems = []
    acs = os.getenv("ACS_CONNECTION_STRING") and os.getenv("ACS_EMAIL_SENDER")
    smtp = os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM")
    if not acs and not smtp:
        problems.append("ACS_CONNECTION_STRING + ACS_EMAIL_SENDER, or SMTP_HOST + SMTP_FROM, are required for e-mail")
    if os.getenv("AMA_SMS_WEBHOOK_URL") and not os.getenv("AMA_SMS_WEBHOOK_URL").startswith("https://"):
        problems.append("AMA_SMS_WEBHOOK_URL must be https")
    return problems


def _origin():
    return (os.getenv("AMA_PUBLIC_ORIGIN") or os.getenv("DEMO_PUBLIC_ORIGIN") or "").rstrip("/")


def compose(order, item):
    language = order.get("language", "en")
    text = item["tr"] if language == "tr" else item["en"]
    origin = _origin()
    link = f"{origin}/track#order={order['id']}&token={tracking_token(order['id'])}" if origin else ""
    return f"TürkBizTel order {order['id']}", text, link


def send_plain_email(to, subject, body):
    """Send one plain-text message through Azure Communication Services, or SMTP when ACS is not configured."""
    if os.getenv("ACS_CONNECTION_STRING") and os.getenv("ACS_EMAIL_SENDER"):
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(os.environ["ACS_CONNECTION_STRING"])
        poller = client.begin_send({
            "senderAddress": os.environ["ACS_EMAIL_SENDER"],
            "recipients": {"to": [{"address": to}]},
            "content": {"subject": subject, "plainText": body},
        })
        result = poller.result(timeout=60)
        if str(result.get("status", "")).lower() != "succeeded":
            raise RuntimeError("EmailSendFailed")
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = to
    message.set_content(body)
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=15) as smtp:
        if os.getenv("SMTP_STARTTLS", "1") != "0":
            smtp.starttls()
        if os.getenv("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
        smtp.send_message(message)


def send_email(contact, order, item):
    subject, text, link = compose(order, item)
    footer = f"\n\nTrack your order: {link}" if link else ""
    send_plain_email(contact["email"], subject, f"Hello {contact['contactName']},\n\n{text}{footer}\n")


ACCOUNT_MAIL = {
    "verify": {
        "subject": "Your TürkBizTel verification code",
        "en": "Your TürkBizTel verification code is {code}.\nIt expires in {minutes} minutes. If you did not create "
              "a TürkBizTel account you can ignore this email.",
        "tr": "TürkBizTel doğrulama kodunuz: {code}\nKod {minutes} dakika içinde geçerliliğini yitirir. TürkBizTel "
              "hesabı oluşturmadıysanız bu e-postayı yok sayabilirsiniz.",
        "path": "/account#profile",
    },
    "reset": {
        "subject": "Your TürkBizTel password reset code",
        "en": "Your TürkBizTel password reset code is {code}.\nIt expires in {minutes} minutes. If you did not ask "
              "to reset your password, ignore this email: your password has not changed.",
        "tr": "TürkBizTel parola sıfırlama kodunuz: {code}\nKod {minutes} dakika içinde geçerliliğini yitirir. "
              "Parolanızı sıfırlamayı siz istemediyseniz bu e-postayı yok sayın: parolanız değişmedi.",
        "path": "/login#forgot",
    },
}


def account_message(purpose, name, code, language):
    """Subject and bilingual body for an account e-mail; the customer's own language comes first."""
    template = ACCOUNT_MAIL[purpose]
    minutes = CODE_SECONDS // 60
    english = f"Hello {name},\n\n" + template["en"].format(code=code, minutes=minutes)
    turkish = f"Merhaba {name},\n\n" + template["tr"].format(code=code, minutes=minutes)
    parts = [turkish, english] if language == "tr" else [english, turkish]
    origin = _origin()
    link = f"\n\n{origin}{template['path']}" if origin else ""
    return template["subject"], "\n\n---\n\n".join(parts) + link + "\n"


def send_account_email(email, name, purpose, code, language="en"):
    send_plain_email(email, *account_message(purpose, name, code, language))


def bilingual(language, english, turkish, link=""):
    parts = [turkish, english] if language == "tr" else [english, turkish]
    return "\n\n---\n\n".join(parts) + (f"\n\n{link}" if link else "") + "\n"


def invite_message(name, partner, link, language):
    days = INVITE_SECONDS // 86400
    english = (f"Hello {name},\n\n{partner} now has a TürkBizTel wholesale partner account. Use the link below to "
               "choose your password and open your partner portal. There you can follow every delivery and its "
               "expected date, check payments, update delivery details and raise concerns with our team.\n"
               f"The link works once and expires in {days} days.")
    turkish = (f"Merhaba {name},\n\n{partner} için bir TürkBizTel iş ortağı hesabı oluşturuldu. Aşağıdaki "
               "bağlantıyla parolanızı belirleyip iş ortağı portalınızı açabilirsiniz. Portalda her teslimatı ve "
               "tahmini teslim tarihini izleyebilir, ödemeleri görebilir, teslimat bilgilerini güncelleyebilir ve "
               "ekibimize talep iletebilirsiniz.\n"
               f"Bağlantı tek kullanımlıktır ve {days} gün içinde geçerliliğini yitirir.")
    return "Your TürkBizTel partner portal invitation", bilingual(language, english, turkish, link)


def send_invite_email(email, name, partner, link, language="en"):
    send_plain_email(email, *invite_message(name, partner, link, language))


def invite_link(token):
    """The absolute invitation link, or "" when the public origin is not configured."""
    origin = _origin()
    return f"{origin}/login#invite={token}" if origin else ""


def concern_message(name, subject, text, link, language):
    english = (f"Hello {name},\n\nOur service team replied to your concern “{subject}”:\n\n{text}\n\n"
               "You can read the whole conversation and answer in your partner portal.")
    turkish = (f"Merhaba {name},\n\n“{subject}” başlıklı talebinize ekibimiz yanıt verdi:\n\n{text}\n\n"
               "Tüm yazışmayı iş ortağı portalınızda okuyabilir ve yanıtlayabilirsiniz.")
    return f"TürkBizTel replied to your concern: {subject}", bilingual(language, english, turkish, link)


def send_concern_email(email, name, subject, text, concern_id, language="en"):
    origin = _origin()
    link = f"{origin}/partner#/concerns/{concern_id}" if origin else ""
    send_plain_email(email, *concern_message(name, subject, text, link, language))


def send_sms(contact, order, item):
    url = os.getenv("AMA_SMS_WEBHOOK_URL", "")
    if not url.startswith("https://"):
        raise RuntimeError("AMA_SMS_WEBHOOK_URL is not configured")
    _, text, link = compose(order, item)
    headers = {"Authorization": f"Bearer {os.environ['AMA_SMS_WEBHOOK_TOKEN']}"} if os.getenv(
        "AMA_SMS_WEBHOOK_TOKEN") else {}
    response = httpx.post(url, headers=headers, timeout=15, json={
        "to": contact["phone"], "text": f"{text} {link}".strip(), "orderId": order["id"]})
    response.raise_for_status()


def deliver_outbox(store, sender=None):
    """Send every pending message once; record the outcome atomically. Returns the number sent."""
    snapshot = store.read()
    work = []
    for order in snapshot["orders"].values():
        for item in order.get("outbox", []):
            if item["status"] == "pending-delivery" and order.get("contact"):
                work.append((order, item))
    outcomes = {}
    for order, item in work:
        contact = order["contact"]
        try:
            if sender:
                sender(contact, order, item)
            elif contact["preferredChannel"] == "email":
                send_email(contact, order, item)
            else:
                send_sms(contact, order, item)
            outcomes[item["id"]] = (True, None)
        except Exception as exc:  # provider errors must never stop the supervisor
            log.warning("Delivery of %s for %s failed: %s", item["id"], order["id"], type(exc).__name__)
            outcomes[item["id"]] = (False, type(exc).__name__)
    if not outcomes:
        return 0

    def record(state):
        timestamp = now()
        copies = [item for order in state["orders"].values() for item in order.get("outbox", [])]
        copies += state.get("outbox", [])
        for item in copies:
            if item["id"] not in outcomes or item["status"] != "pending-delivery":
                continue
            ok, error = outcomes[item["id"]]
            item["attempts"] = item.get("attempts", 0) + 1
            if ok:
                item.update(status="sent", sentAt=timestamp, deliveredExternally=True)
            elif item["attempts"] >= MAX_ATTEMPTS:
                item.update(status="failed", lastError=error)
            else:
                item["lastError"] = error

    store.transact(record)
    return sum(1 for ok, _ in outcomes.values() if ok)
