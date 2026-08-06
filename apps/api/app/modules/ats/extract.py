"""Resume text extraction. PyMuPDF for the PDF text layer (local, no cloud),
capped at 10 pages per spec. Tesseract OCR fallback is deferred.
"""
import fitz  # pymupdf

MAX_PAGES = 10


def extract_text(data: bytes) -> str:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        parts = [page.get_text() for i, page in enumerate(doc) if i < MAX_PAGES]
    finally:
        doc.close()
    text = "\n".join(parts).strip()
    # ponytail: if text is empty the PDF is likely scanned images → Tesseract OCR
    # fallback goes here. Deferred until a real scanned resume shows up.
    return text
