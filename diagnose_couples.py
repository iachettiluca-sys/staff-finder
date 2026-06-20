#!/usr/bin/env python3
"""
Muestra todos los mails con 2+ adjuntos CV desde gmail_since.
Para cada adjunto: nombre de archivo + primeros 300 chars de texto extraído.
No modifica nada en la DB.
"""
from __future__ import annotations
import sys, imaplib, email, re, datetime
from pathlib import Path
from email.header import decode_header
from email.utils import parseaddr

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")

import os, yaml
from pdf_extractor import extract_attachment_text
from name_extractor import extract_name_and_position

cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
since_date = cfg["search"]["gmail_since"]

user     = os.environ["GMAIL_USER"]
app_pass = os.environ["GMAIL_APP_PASS"]

ATTACHMENT_EXTS = (".pdf", ".doc", ".docx")
CV_KEYWORDS = ["cv", "curriculum", "candidat", "postul", "aplico", "applying", "application"]

def dec(v):
    if not v: return ""
    parts = decode_header(v)
    return "".join(p.decode(e or "utf-8", errors="replace") if isinstance(p, bytes) else str(p) for p, e in parts)

def get_attachments(msg):
    atts = []
    for part in msg.walk():
        fn = part.get_filename()
        if not fn: continue
        fn = dec(fn)
        if any(fn.lower().endswith(x) for x in ATTACHMENT_EXTS):
            data = part.get_payload(decode=True)
            if data:
                atts.append({"filename": fn, "bytes": data})
    return atts

mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
mail.login(user, app_pass)
mail.select("INBOX")

dt = datetime.date.fromisoformat(since_date)
_, nums = mail.search(None, f'SINCE "{dt.strftime("%d-%b-%Y")}"')
ids = nums[0].split()
print(f"{len(ids)} mails desde {since_date}\n")

found = 0
for num in ids:
    _, data = mail.fetch(num, "(RFC822)")
    msg = email.message_from_bytes(data[0][1])

    subj = dec(msg.get("Subject", ""))
    from_raw = msg.get("From", "")
    sender_name, sender_email = parseaddr(from_raw)
    sender_name = dec(sender_name) or sender_email

    # Skip self-emails
    if sender_email.lower() == user.lower():
        continue

    atts = get_attachments(msg)
    if len(atts) < 2:
        continue

    # Check if it looks like a CV email at all
    body_preview = subj.lower() + " "
    text_check = any(kw in body_preview for kw in CV_KEYWORDS)

    found += 1
    print(f"{'='*60}")
    print(f"De:      {sender_name} <{sender_email}>")
    print(f"Asunto:  {subj}")
    print(f"Adjuntos ({len(atts)}):")

    names_found = []
    for i, att in enumerate(atts[:3]):
        cv_text = extract_attachment_text(att["filename"], att["bytes"])
        info = extract_name_and_position(cv_text, "", att["filename"].rsplit(".", 1)[0])
        names_found.append(info["full_name"])
        snippet = (cv_text or "(sin texto — PDF escaneado)").strip()[:250].replace("\n", " ")
        print(f"\n  [{i+1}] {att['filename']}")
        print(f"       Nombre extraído: {info['full_name']} | Puesto: {info['position']}")
        print(f"       Texto: {snippet}")

    diferentes = len(set(n.lower() for n in names_found)) > 1
    print(f"\n  -> Nombres: {names_found}")
    print(f"  -> ¿Son distintos? {'SI — PAREJA' if diferentes else 'NO (mismo nombre)'}")

mail.logout()
print(f"\n{'='*60}")
print(f"Total mails con 2+ adjuntos CV: {found}")
