"""
pdf_extractor.py — Extrae texto de CVs en formato PDF o Word.

Flujo para PDFs:
  1. pdfplumber  (rápido, texto digital)
  2. Si resultado < 50 chars → OCR con Tesseract via PyMuPDF
     (convierte cada página a imagen 300 DPI y lee con spa+eng)
"""
from __future__ import annotations
import io, os

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
OCR_LANGS      = "spa+eng"
OCR_THRESHOLD  = 50   # chars mínimos para considerar que pdfplumber funcionó


def _ocr_pdf(file_bytes: bytes) -> str:
    """Convierte cada página del PDF a imagen y aplica Tesseract OCR."""
    try:
        import fitz          # PyMuPDF
        import pytesseract
        from PIL import Image

        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages_text = []

        for page_num, page in enumerate(doc):
            # 300 DPI — buena calidad para OCR
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

            text = pytesseract.image_to_string(img, lang=OCR_LANGS,
                                               config="--psm 1")
            pages_text.append(text.strip())
            print(f"[ocr] Página {page_num + 1}/{len(doc)}: {len(text.strip())} chars")

        doc.close()
        return "\n\n".join(pages_text).strip()

    except Exception as e:
        print(f"[ocr] Error: {e}")
        return ""


def extract_text_from_pdf(file_bytes: bytes) -> str:
    # Intento 1: pdfplumber (texto digital)
    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        text = "\n".join(pages).strip()
    except Exception as e:
        print(f"[pdf_extractor] pdfplumber error: {e}")

    if len(text) >= OCR_THRESHOLD:
        return text

    # Intento 2: OCR con Tesseract
    print(f"[pdf_extractor] Texto digital insuficiente ({len(text)} chars) — usando OCR")
    ocr_text = _ocr_pdf(file_bytes)
    return ocr_text if ocr_text else text


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
