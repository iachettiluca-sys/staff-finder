"""
gmail_scraper.py — Scraper de Gmail vía IMAP para encontrar mails con CVs adjuntos.
"""
from __future__ import annotations
import imaplib, email, os
from email.header import decode_header
from email.utils import parseaddr
from bs4 import BeautifulSoup

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

CV_KEYWORDS = ["cv", "curriculum", "candidat", "postul", "aplico", "applying", "application"]
ATTACHMENT_EXTS = (".pdf", ".doc", ".docx")


def _decode_str(value) -> str:
    if value is None:
        return ""
    parts = decode_header(value)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def _get_body_text(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            elif ct == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    body += BeautifulSoup(html, "html.parser").get_text(separator="\n")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            ct = msg.get_content_type()
            text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            body = BeautifulSoup(text, "html.parser").get_text(separator="\n") if ct == "text/html" else text
    return body.strip()


def _get_attachments(msg) -> list[dict]:
    attachments = []
    for part in msg.walk():
        cd = str(part.get("Content-Disposition", ""))
        filename = part.get_filename()
        if not filename:
            continue
        filename = _decode_str(filename)
        if not any(filename.lower().endswith(ext) for ext in ATTACHMENT_EXTS):
            continue
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append({"filename": filename, "bytes": payload})
    return attachments


def _is_cv_email(subject: str, body: str, has_attachment: bool) -> bool:
    text = (subject + " " + body[:500]).lower()
    has_keyword = any(kw in text for kw in CV_KEYWORDS)
    return has_attachment and has_keyword or (has_keyword and not has_attachment)


def _detect_couple(body: str, keywords: list[str]) -> bool:
    body_lower = body.lower()
    return any(kw.lower() in body_lower for kw in keywords)


def _detect_position(subject: str, body: str) -> str:
    text = (subject + " " + body[:1000]).lower()
    if "chef" in text or "cocin" in text:
        return "Chef"
    if "hostess" in text or "anfitriona" in text:
        return "Hostess"
    if "host" in text or "anfitrión" in text or "anfitrion" in text:
        return "Host"
    return "unknown"


def scrape_gmail(since_date: str, couple_keywords: list[str],
                 processed_ids: set[str]) -> list[dict]:
    """
    Conecta a Gmail IMAP y retorna lista de mails con CVs no procesados.
    """
    user = os.environ["GMAIL_USER"]
    app_pass = os.environ["GMAIL_APP_PASS"]

    results = []
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        mail.login(user, app_pass)
        mail.select("INBOX")

        # SINCE espera formato DD-Mon-YYYY
        import datetime
        dt = datetime.date.fromisoformat(since_date)
        since_str = dt.strftime("%d-%b-%Y")

        _, msg_nums = mail.search(None, f'SINCE "{since_str}"')
        if not msg_nums[0]:
            print("[gmail] No se encontraron mails en el rango.")
            return []

        ids = msg_nums[0].split()
        print(f"[gmail] {len(ids)} mails encontrados desde {since_str}")

        for num in ids:
            _, data = mail.fetch(num, "(RFC822 UID)")
            # Get UID
            _, uid_data = mail.fetch(num, "(UID)")
            uid_str = uid_data[0].decode()
            import re
            uid_match = re.search(r"UID (\d+)", uid_str)
            message_id = uid_match.group(1) if uid_match else num.decode()

            if message_id in processed_ids:
                continue

            raw = data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _decode_str(msg.get("Subject", ""))
            from_raw = msg.get("From", "")
            sender_name, sender_email = parseaddr(from_raw)
            sender_name = _decode_str(sender_name) or sender_email

            body = _get_body_text(msg)
            attachments = _get_attachments(msg)

            if not _is_cv_email(subject, body, bool(attachments)):
                continue

            is_couple = _detect_couple(body, couple_keywords)
            position = _detect_position(subject, body)

            results.append({
                "message_id": message_id,
                "sender_name": sender_name,
                "sender_email": sender_email,
                "subject": subject,
                "body": body,
                "is_couple": is_couple,
                "position": position,
                "attachments": attachments,
            })
            print(f"[gmail] CV encontrado: {sender_name} ({position})"
                  f"{' — PAREJA' if is_couple else ''}")

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return results
