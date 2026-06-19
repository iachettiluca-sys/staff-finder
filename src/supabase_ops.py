"""
supabase_ops.py — Operaciones CRUD sobre Supabase para Staff Finder.
"""
from __future__ import annotations
import os, json
from supabase import create_client, Client

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _client


def get_or_create_search(name: str, company: str) -> str:
    sb = get_client()
    res = sb.table("searches").select("id").eq("name", name).eq("company", company).execute()
    if res.data:
        return res.data[0]["id"]
    res = sb.table("searches").insert({"name": name, "company": company}).execute()
    return res.data[0]["id"]


def get_processed_message_ids(search_id: str) -> set[str]:
    sb = get_client()
    res = sb.table("candidates").select("gmail_message_id").eq("search_id", search_id).execute()
    return {r["gmail_message_id"] for r in res.data if r["gmail_message_id"]}


def get_positions(search_id: str) -> list[dict]:
    sb = get_client()
    res = sb.table("positions").select("*").eq("search_id", search_id).execute()
    return res.data or []


def ensure_positions(search_id: str, positions_cfg: list[dict]) -> list[dict]:
    existing = get_positions(search_id)
    existing_titles = {p["title"] for p in existing}
    to_insert = [
        {"search_id": search_id, "title": p["title"], "requirements": p["requirements"]}
        for p in positions_cfg
        if p["title"] not in existing_titles
    ]
    if to_insert:
        sb = get_client()
        sb.table("positions").insert(to_insert).execute()
    return get_positions(search_id)


def upload_pdf(search_id: str, filename: str, file_bytes: bytes) -> str:
    sb = get_client()
    path = f"{search_id}/{filename}"
    try:
        sb.storage.from_("cvs").upload(path, file_bytes, {"content-type": "application/pdf", "upsert": "true"})
    except Exception as e:
        print(f"[supabase] Error subiendo PDF {filename}: {e}")
        return ""
    res = sb.storage.from_("cvs").get_public_url(path)
    return res


def create_candidate(data: dict) -> str:
    sb = get_client()
    # Serialize lists to JSON for jsonb columns
    data = dict(data)
    for col in ("ai_strengths", "ai_gaps"):
        if isinstance(data.get(col), list):
            data[col] = json.dumps(data[col], ensure_ascii=False)
    res = sb.table("candidates").insert(data).execute()
    return res.data[0]["id"]


def link_couple(candidate_id_1: str, candidate_id_2: str) -> None:
    sb = get_client()
    sb.table("candidates").update({"couple_partner_id": candidate_id_2, "category": "couple"}).eq("id", candidate_id_1).execute()
    sb.table("candidates").update({"couple_partner_id": candidate_id_1, "category": "couple"}).eq("id", candidate_id_2).execute()


def list_upload_files() -> list[dict]:
    """List pending files in the uploads/ prefix of the cvs bucket."""
    sb = get_client()
    try:
        items = sb.storage.from_("cvs").list("uploads") or []
        return [{"name": f"uploads/{item['name']}", **item} for item in items
                if item.get("name") and not item["name"].startswith(".")]
    except Exception as e:
        print(f"[supabase] Error listando uploads: {e}")
        return []


def download_storage_file(path: str) -> bytes | None:
    """Download a file from Supabase Storage (path relative to bucket root)."""
    sb = get_client()
    try:
        return sb.storage.from_("cvs").download(path)
    except Exception as e:
        print(f"[supabase] Error descargando {path}: {e}")
        return None


def delete_storage_files(paths: list[str]) -> None:
    """Delete files from the cvs bucket."""
    sb = get_client()
    try:
        sb.storage.from_("cvs").remove(paths)
    except Exception as e:
        print(f"[supabase] Error eliminando archivos de Storage: {e}")


def find_candidate_by_name(search_id: str, name: str) -> dict | None:
    """Find an existing candidate by name (case-insensitive exact match)."""
    if not name or name.lower() in ("desconocido", "unknown", ""):
        return None
    sb = get_client()
    res = (sb.table("candidates")
           .select("id,name,bio,gmail_message_id")
           .eq("search_id", search_id)
           .ilike("name", name)
           .execute())
    return res.data[0] if res.data else None


def update_candidate(candidate_id: str, updates: dict) -> None:
    """Update arbitrary fields on a candidate."""
    sb = get_client()
    sb.table("candidates").update(updates).eq("id", candidate_id).execute()
