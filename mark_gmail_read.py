#!/usr/bin/env python3
"""
mark_gmail_read.py — Marca como leídos en Gmail todos los mails ya importados.
Correr una sola vez para limpiar el backlog de mails sin marcar.
"""
import sys, imaplib
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")
import os

from supabase_ops import get_client

sb = get_client()

# Traer todos los gmail_message_id de candidatos importados (excluye _p2 de parejas)
res = sb.table("candidates").select("gmail_message_id").execute()
uids = [
    c["gmail_message_id"] for c in (res.data or [])
    if c.get("gmail_message_id") and not c["gmail_message_id"].endswith("_p2")
]
print(f"UIDs a marcar como leídos: {len(uids)}")

user     = os.environ.get("GMAIL_USER")
app_pass = os.environ.get("GMAIL_APP_PASS")
if not user or not app_pass:
    print("ERROR: GMAIL_USER o GMAIL_APP_PASS no configurados en .env")
    sys.exit(1)

mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
mail.login(user, app_pass)
mail.select("INBOX")

ok = 0
fail = 0
for uid in uids:
    typ, _ = mail.uid("STORE", uid, "+FLAGS", "\\Seen")
    if typ == "OK":
        ok += 1
    else:
        fail += 1
        print(f"  [warn] UID {uid} → {typ}")

mail.logout()
print(f"\nListo. Marcados: {ok}  |  No encontrados/errores: {fail}")
