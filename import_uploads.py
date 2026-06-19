#!/usr/bin/env python3
"""
import_uploads.py — Procesa CVs subidos via drag-and-drop en la interfaz web.
Los archivos están en Supabase Storage bajo el prefijo uploads/ del bucket cvs.

Flujo:
  1. Lista archivos en uploads/
  2. Descarga y procesa cada uno (ZIP o archivo individual)
  3. Detecta duplicados: si el candidato ya existe con bio, prioriza esa versión
  4. Detecta parejas en ZIPs (por estructura de subcarpetas)
  5. Opcionalmente busca en Gmail bios para candidatos sin bio
  6. Elimina los archivos procesados de Storage

Uso:
    python import_uploads.py             # procesa + busca bios en Gmail
    python import_uploads.py --no-gmail  # solo procesa sin IMAP
"""
from __future__ import annotations
import sys, os, io, zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))

from dotenv import load_dotenv
load_dotenv(root / ".env")

import yaml
from pdf_extractor import extract_attachment_text
from name_extractor import extract_name_and_position
from cv_matcher import match_cv
from supabase_ops import (
    get_or_create_search, get_processed_message_ids,
    ensure_positions, upload_pdf, create_candidate, link_couple,
    list_upload_files, download_storage_file, delete_storage_files,
    find_candidate_by_name, update_candidate,
)

SUPPORTED_EXTS = {".pdf", ".doc", ".docx"}


def _detect_couples_in_zip(entries: list[tuple[str, bytes]]) -> list[tuple]:
    """
    Groups ZIP entries by their subfolder path.
    Two files in the same subfolder = couple.
    Returns list of ((fname, bytes), (fname, bytes) | None).
    """
    by_folder: dict[str, list] = {}
    for fname, fbytes in entries:
        folder = "/".join(fname.replace("\\", "/").split("/")[:-1])
        by_folder.setdefault(folder, []).append((fname, fbytes))

    groups: list[tuple] = []
    for folder_files in by_folder.values():
        if len(folder_files) == 2:
            groups.append((folder_files[0], folder_files[1]))
        elif len(folder_files) == 1:
            groups.append((folder_files[0], None))
        else:
            for f in folder_files:
                groups.append((f, None))
    return groups


def _process_single_cv(
    fname: str,
    fbytes: bytes,
    positions: list[dict],
    search_id: str,
    processed_ids: set[str],
    fake_id: str | None = None,
) -> dict | None:
    """
    Extracts, names, and matches a single CV.
    Returns a data dict on success, None if skipped (duplicate or error).
    Duplicate logic:
      - If already in processed_ids → skip.
      - If a candidate with the same name exists AND has a bio → skip (prefer bio version).
      - If a candidate with the same name exists but has NO bio → skip here,
        the Gmail enrichment step will add the bio later.
    """
    stem = Path(fname).stem
    fid = fake_id or f"upload_{stem}"

    if fid in processed_ids:
        print(f"  [skip] Ya importado (ID): {fname}")
        return None

    cv_text = extract_attachment_text(fname, fbytes)
    info = extract_name_and_position(cv_text, "", stem)
    name = info["full_name"]
    position = info["position"]

    # Duplicate by name
    existing = find_candidate_by_name(search_id, name)
    if existing:
        if existing.get("bio"):
            print(f"  [dup] {name} ya existe con bio — priorizo la versión con bio, salto éste")
        else:
            print(f"  [dup] {name} ya existe sin bio — se intentará enriquecer con Gmail")
        return None

    pos_cfg = next((p for p in positions if p["title"] == position),
                   positions[0] if positions else None)
    pos_title = pos_cfg["title"] if pos_cfg else position
    pos_req = pos_cfg["requirements"] if pos_cfg else ""

    pdf_url = upload_pdf(search_id, f"upload_{Path(fname).name}", fbytes)
    match = match_cv(
        cv_text=cv_text, bio="",
        candidate_name=name,
        position_title=pos_title,
        position_requirements=pos_req,
    )

    print(f"  {name} → {pos_title} — score: {match['score']}")
    return {
        "fake_id": fid,
        "name": name,
        "position": pos_title,
        "cv_text": cv_text,
        "pdf_url": pdf_url,
        "match": match,
    }


def _insert_candidate(d: dict, search_id: str, category: str = "solo", score_override: dict | None = None) -> str:
    m = score_override or d["match"]
    return create_candidate({
        "search_id": search_id,
        "name": d["name"],
        "email": "",
        "bio": "",
        "pdf_url": d["pdf_url"],
        "pdf_text": d["cv_text"],
        "gmail_message_id": d["fake_id"],
        "position": d["position"],
        "category": category,
        "status": "nuevo",
        "ai_score": m["score"],
        "ai_summary": m["summary"],
        "ai_strengths": m["strengths"],
        "ai_gaps": m["gaps"],
    })


def enrich_bios_from_gmail(search_id: str, since_date: str) -> int:
    """For candidates with no bio in this search, search Gmail for a matching email."""
    try:
        from gmail_scraper import find_bio_for_candidate
    except ImportError:
        return 0

    from supabase_ops import get_client
    sb = get_client()
    res = sb.table("candidates").select("id,name,bio").eq("search_id", search_id).execute()
    no_bio = [c for c in (res.data or []) if not c.get("bio")]
    if not no_bio:
        return 0

    print(f"[gmail] Buscando bios para {len(no_bio)} candidato(s) sin bio...")
    enriched = 0
    for c in no_bio:
        bio = find_bio_for_candidate(c["name"], since_date)
        if bio:
            update_candidate(c["id"], {"bio": bio})
            enriched += 1
            print(f"  [gmail] Bio encontrada: {c['name']}")
    return enriched


def main() -> int:
    no_gmail = "--no-gmail" in sys.argv

    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    search_cfg = cfg["search"]
    positions_cfg = cfg["positions"]

    search_id = get_or_create_search(search_cfg["name"], search_cfg["company"])
    positions = ensure_positions(search_id, positions_cfg)
    processed_ids = get_processed_message_ids(search_id)

    upload_files = list_upload_files()
    if not upload_files:
        print("[uploads] No hay archivos pendientes en Storage uploads/")
    else:
        print(f"[uploads] {len(upload_files)} archivo(s) en Storage uploads/")

    imported = 0
    to_delete: list[str] = []

    for file_info in upload_files:
        storage_path = file_info["name"]   # e.g. "uploads/1234_cvs.zip"
        filename = Path(storage_path).name
        print(f"\n→ {filename}")

        file_bytes = download_storage_file(storage_path)
        if not file_bytes:
            print("  [error] No se pudo descargar — saltando")
            continue

        to_delete.append(storage_path)
        ext = Path(filename).suffix.lower()

        if ext == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                    entries = [
                        (name, zf.read(name))
                        for name in zf.namelist()
                        if Path(name).suffix.lower() in SUPPORTED_EXTS
                        and not Path(name).name.startswith((".", "__"))
                    ]
            except Exception as e:
                print(f"  [error] ZIP inválido: {e}")
                continue

            print(f"  ZIP: {len(entries)} CV(s)")
            groups = _detect_couples_in_zip(entries)
            n_couples = sum(1 for g in groups if g[1])
            print(f"  Grupos: {len(groups)} ({n_couples} pareja(s))")

            for item1, item2 in groups:
                f1, b1 = item1
                if item2:
                    f2, b2 = item2
                    print(f"  Pareja: {Path(f1).name} + {Path(f2).name}")
                    d1 = _process_single_cv(f1, b1, positions, search_id, processed_ids)
                    d2 = _process_single_cv(f2, b2, positions, search_id, processed_ids)

                    if d1 and d2:
                        pos_cfg = next((p for p in positions if p["title"] == d1["position"]), positions[0])
                        couple_match = match_cv(
                            cv_text=d1["cv_text"], bio="",
                            candidate_name=d1["name"],
                            position_title=d1["position"],
                            position_requirements=pos_cfg["requirements"],
                            is_couple=True,
                            partner_name=d2["name"],
                            partner_cv_text=d2["cv_text"],
                        )
                        id1 = _insert_candidate(d1, search_id, "couple", couple_match)
                        id2 = _insert_candidate(d2, search_id, "couple", couple_match)
                        link_couple(id1, id2)
                        imported += 2
                        processed_ids.update({d1["fake_id"], d2["fake_id"]})
                    else:
                        for d in [d1, d2]:
                            if d:
                                _insert_candidate(d, search_id, "solo")
                                imported += 1
                                processed_ids.add(d["fake_id"])
                else:
                    print(f"  Individual: {Path(f1).name}")
                    d = _process_single_cv(f1, b1, positions, search_id, processed_ids)
                    if d:
                        _insert_candidate(d, search_id, "solo")
                        imported += 1
                        processed_ids.add(d["fake_id"])

        elif ext in SUPPORTED_EXTS:
            d = _process_single_cv(filename, file_bytes, positions, search_id, processed_ids,
                                   fake_id=f"upload_{Path(filename).stem}")
            if d:
                _insert_candidate(d, search_id, "solo")
                imported += 1
                processed_ids.add(d["fake_id"])
        else:
            print("  [skip] Extensión no soportada")

    # Clean up processed files from Storage
    if to_delete:
        delete_storage_files(to_delete)
        print(f"\n[uploads] {len(to_delete)} archivo(s) eliminado(s) de Storage")

    print(f"\n[uploads] Importados: {imported} candidato(s)")

    # Enrich bios from Gmail
    if not no_gmail and os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASS"):
        enriched = enrich_bios_from_gmail(search_id, search_cfg["gmail_since"])
        if enriched:
            print(f"[gmail] {enriched} bio(s) añadida(s)")
    elif not no_gmail:
        print("[gmail] Sin credenciales de Gmail — saltando enriquecimiento de bios")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
