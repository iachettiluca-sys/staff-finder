#!/usr/bin/env python3
"""
Importa correctamente la pareja Julieta Parada + Juan Martín Chavez.
Descarga ambos CVs del mail original, corrige el registro de Julieta
y crea el de Juan Martín, luego los linka como pareja.
"""
from __future__ import annotations
import sys, imaplib, email, re, datetime
from pathlib import Path
from email.header import decode_header
from email.utils import parseaddr

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")

import os, yaml, json
from pdf_extractor import extract_attachment_text
from cv_matcher import match_cv
from supabase_ops import get_client, upload_pdf, link_couple

sb = get_client()
cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
search_cfg = cfg["search"]
positions_cfg = cfg["positions"]

pos_chef = next(p for p in positions_cfg if p["title"] == "Chef")
pos_host = next(p for p in positions_cfg if p["title"] == "Host")

ATTACHMENT_EXTS = (".pdf", ".doc", ".docx")

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

# ── Conectar a Gmail y buscar el mail de Julieta ──────────────────────────
user     = os.environ["GMAIL_USER"]
app_pass = os.environ["GMAIL_APP_PASS"]

mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
mail.login(user, app_pass)
mail.select("INBOX")

dt = datetime.date.fromisoformat(search_cfg["gmail_since"])
_, nums = mail.search(None, f'SINCE "{dt.strftime("%d-%b-%Y")}" FROM "juliparada1@gmail.com"')

julieta_cv_text = ""
juan_cv_text    = ""
julieta_bytes   = b""
juan_bytes      = b""
julieta_fn      = ""
juan_fn         = ""
message_id      = ""

for num in (nums[0].split() or []):
    _, data = mail.fetch(num, "(RFC822 UID)")
    _, uid_data = mail.fetch(num, "(UID)")
    uid_str = uid_data[0].decode()
    uid_match = re.search(r"UID (\d+)", uid_str)
    message_id = uid_match.group(1) if uid_match else num.decode()

    msg = email.message_from_bytes(data[0][1])
    atts = get_attachments(msg)
    if len(atts) < 2:
        continue

    for att in atts:
        text = extract_attachment_text(att["filename"], att["bytes"])
        fn_low = att["filename"].lower()
        if "julieta" in fn_low or "parada" in fn_low:
            julieta_cv_text = text
            julieta_bytes   = att["bytes"]
            julieta_fn      = att["filename"]
        elif "juan" in fn_low or "chavez" in fn_low or "chavez" in fn_low or "martin" in fn_low:
            juan_cv_text = text
            juan_bytes   = att["bytes"]
            juan_fn      = att["filename"]

    if julieta_cv_text or juan_cv_text:
        print(f"Mail encontrado: UID {message_id}")
        print(f"  Julieta CV: {julieta_fn} ({len(julieta_cv_text)}c)")
        print(f"  Juan CV:    {juan_fn} ({len(juan_cv_text)}c)")
        break

mail.logout()

if not julieta_cv_text and not juan_cv_text:
    print("ERROR: No se encontraron los CVs en Gmail.")
    sys.exit(1)

# ── Buscar search_id ─────────────────────────────────────────────────────
from supabase_ops import get_or_create_search, ensure_positions
search_id = get_or_create_search(search_cfg["name"], search_cfg["company"])
positions  = ensure_positions(search_id, positions_cfg)
pos_chef_db = next(p for p in positions if p["title"] == "Chef")
pos_host_db = next(p for p in positions if p["title"] == "Host")

# ── Score individual y como pareja ───────────────────────────────────────
print("\nEvaluando como pareja...")
match_julieta = match_cv(
    cv_text=julieta_cv_text, bio="",
    candidate_name="Julieta Parada",
    position_title="Host",
    position_requirements=pos_host_db["requirements"],
    is_couple=True,
    partner_name="Juan Martín Chavez",
    partner_cv_text=juan_cv_text,
)
match_juan = match_cv(
    cv_text=juan_cv_text, bio="",
    candidate_name="Juan Martín Chavez",
    position_title="Chef",
    position_requirements=pos_chef_db["requirements"],
    is_couple=True,
    partner_name="Julieta Parada",
    partner_cv_text=julieta_cv_text,
)
print(f"  Julieta (Host): {match_julieta['score']}")
print(f"  Juan (Chef):    {match_juan['score']}")

# ── Actualizar Julieta en DB (corregir cv_text y posición) ───────────────
res = sb.table("candidates").select("id,name").ilike("name", "%julieta%parada%").execute()
julieta_rows = res.data or []
if not julieta_rows:
    res = sb.table("candidates").select("id,name").ilike("name", "%parada%").execute()
    julieta_rows = res.data or []

if julieta_rows:
    jid = julieta_rows[0]["id"]
    pdf_url_j = upload_pdf(search_id, f"julieta_parada_{julieta_fn}", julieta_bytes) if julieta_bytes else ""
    sb.table("candidates").update({
        "pdf_text": julieta_cv_text,
        "position": "Host",
        "category": "couple",
        "ai_score": match_julieta["score"],
        "ai_summary": match_julieta["summary"],
        "ai_strengths": json.dumps(match_julieta["strengths"], ensure_ascii=False),
        "ai_gaps": json.dumps(match_julieta["gaps"], ensure_ascii=False),
        **({"pdf_url": pdf_url_j} if pdf_url_j else {}),
    }).eq("id", jid).execute()
    print(f"\nJulieta actualizada (id={jid[:8]}): Host, score={match_julieta['score']}")
else:
    print("ERROR: Julieta Parada no encontrada en DB")
    sys.exit(1)

# ── Crear Juan Martín Chavez en DB ────────────────────────────────────────
# Verificar si ya existe
juan_existing = sb.table("candidates").select("id,name").ilike("name", "%juan%mart%").execute().data or []
juan_existing += sb.table("candidates").select("id,name").ilike("name", "%chavez%").execute().data or []

if juan_existing:
    print(f"Juan Martín ya existe ({juan_existing[0]['name']}), actualizando...")
    jmid = juan_existing[0]["id"]
    pdf_url_jm = upload_pdf(search_id, f"juan_martin_chavez_{juan_fn}", juan_bytes) if juan_bytes else ""
    sb.table("candidates").update({
        "name": "Juan Martín Chavez",
        "pdf_text": juan_cv_text,
        "position": "Chef",
        "category": "couple",
        "ai_score": match_juan["score"],
        "ai_summary": match_juan["summary"],
        "ai_strengths": json.dumps(match_juan["strengths"], ensure_ascii=False),
        "ai_gaps": json.dumps(match_juan["gaps"], ensure_ascii=False),
        **({"pdf_url": pdf_url_jm} if pdf_url_jm else {}),
    }).eq("id", jmid).execute()
else:
    pdf_url_jm = upload_pdf(search_id, f"juan_martin_chavez_{juan_fn}", juan_bytes) if juan_bytes else ""
    res = sb.table("candidates").insert({
        "search_id": search_id,
        "name": "Juan Martín Chavez",
        "position": "Chef",
        "category": "couple",
        "pdf_text": juan_cv_text,
        "bio": "",
        "pdf_url": pdf_url_jm,
        "gmail_message_id": f"{message_id}_p2",
        "ai_score": match_juan["score"],
        "ai_summary": match_juan["summary"],
        "ai_strengths": json.dumps(match_juan["strengths"], ensure_ascii=False),
        "ai_gaps": json.dumps(match_juan["gaps"], ensure_ascii=False),
        "status": "nuevo",
    }).execute()
    jmid = (res.data or [{}])[0].get("id", "")
    print(f"Juan Martín creado (id={jmid[:8] if jmid else '?'}): Chef, score={match_juan['score']}")

# ── Linkear como pareja ───────────────────────────────────────────────────
if jid and jmid:
    link_couple(jid, jmid)
    print(f"\nPareja linkeada: Julieta Parada <-> Juan Martín Chavez")
    print(f"  Julieta: Host score={match_julieta['score']}")
    print(f"  Juan:    Chef score={match_juan['score']}")
