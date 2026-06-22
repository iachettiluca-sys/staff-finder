#!/usr/bin/env python3
"""
find_couples.py — Lista mails desde una fecha que tienen 2+ CVs adjuntos con nombres distintos.
Solo lee, no importa nada.
"""
import sys, imaplib, email, re, datetime, os
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")

from email.header import decode_header
from email.utils import parseaddr
from pdf_extractor import extract_attachment_text
from name_extractor import extract_name_and_position

SINCE = "2026-06-01"
ATTACHMENT_EXTS = (".pdf", ".doc", ".docx")


def decode_str(value) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def get_attachments(msg) -> list[dict]:
    atts = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        filename = decode_str(filename)
        if not any(filename.lower().endswith(ext) for ext in ATTACHMENT_EXTS):
            continue
        payload = part.get_payload(decode=True)
        if payload:
            atts.append({"filename": filename, "bytes": payload})
    return atts


def main():
    user     = os.environ.get("GMAIL_USER")
    app_pass = os.environ.get("GMAIL_APP_PASS")
    if not user or not app_pass:
        print("ERROR: GMAIL_USER o GMAIL_APP_PASS no configurados en .env")
        sys.exit(1)

    dt = datetime.date.fromisoformat(SINCE)
    since_str = dt.strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(user, app_pass)
    mail.select("INBOX")

    _, msg_nums = mail.search(None, f'SINCE "{since_str}"')
    ids = msg_nums[0].split() if msg_nums[0] else []
    print(f"{len(ids)} mails desde {SINCE}\n")

    couples_found = []

    for num in ids:
        _, data = mail.fetch(num, "(BODY.PEEK[])")
        if not data or not isinstance(data[0], tuple):
            continue
        raw = data[0][1]
        msg = email.message_from_bytes(raw)

        atts = get_attachments(msg)
        if len(atts) < 2:
            continue

        subject = decode_str(msg.get("Subject", ""))
        sender_name, sender_email = parseaddr(msg.get("From", ""))
        sender_name = decode_str(sender_name) or sender_email

        # Ignorar mails propios
        if sender_email.lower() == user.lower():
            continue

        # Extraer nombre de cada adjunto
        names = []
        for att in atts[:3]:
            cv_text = extract_attachment_text(att["filename"], att["bytes"])
            info = extract_name_and_position(cv_text, "", Path(att["filename"]).stem)
            names.append(info["full_name"])

        # Filtrar si todos los nombres son iguales (CV + carta)
        unique_names = list(dict.fromkeys(n.strip().lower() for n in names))
        if len(unique_names) <= 1:
            continue

        couples_found.append({
            "sender": sender_name,
            "email": sender_email,
            "subject": subject,
            "names": names,
            "files": [a["filename"] for a in atts[:3]],
        })

    mail.logout()

    if not couples_found:
        print("No se encontraron mails con múltiples CVs de personas distintas.")
        return

    print(f"=== {len(couples_found)} mail(s) con posibles parejas ===\n")
    for i, c in enumerate(couples_found, 1):
        print(f"{i}. De: {c['sender']} <{c['email']}>")
        print(f"   Asunto: {c['subject']}")
        print(f"   Archivos: {', '.join(c['files'])}")
        print(f"   Nombres detectados: {' + '.join(c['names'])}")
        print()


if __name__ == "__main__":
    main()
