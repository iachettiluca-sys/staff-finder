"""
pdf_extractor.py — Extrae texto de CVs en formato PDF o Word.
"""
from __future__ import annotations
import io


def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n".join(pages).strip()
    except Exception as e:
        print(f"[pdf_extractor] Error leyendo PDF: {e}")
        return ""


def extract_text_from_docx(file_bytes: bytes) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
    except Exception as e:
        print(f"[pdf_extractor] Error leyendo DOCX: {e}")
        return ""


def extract_attachment_text(filename: str, file_bytes: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    if name.endswith((".docx", ".doc")):
        return extract_text_from_docx(file_bytes)
    return ""
