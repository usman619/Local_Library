import os
import re
import hashlib
import zipfile
from pathlib import Path
import pymupdf

from app.config import (
    EPUB_PAGE_WIDTH,
    EPUB_PAGE_HEIGHT,
    DEFAULT_EPUB_FONTSIZE,
    EPUB_READER_CSS,
    SANITIZED_EPUB_CACHE_DIR,
)

try:
    pymupdf.TOOLS.set_small_glyph_heights(True)
    # Increase MuPDF internal display-list store cache for faster continuous scrolling
    if hasattr(pymupdf.TOOLS, "store_maxsize"):
        pymupdf.TOOLS.store_maxsize(256 * 1024 * 1024)
except Exception:
    pass

_PAGE_RULE_RE = re.compile(r"@page\s*\{[^}]*\}", re.IGNORECASE | re.DOTALL)
_BROKEN_PROPS_RE = re.compile(
    r"(float\s*:\s*[^;\}]+;?"
    r"|position\s*:\s*(?:relative|absolute|fixed)[^;\}]+;?"
    r"|overflow(?:-[xy])?\s*:\s*hidden[^;\}]+;?"
    r"|margin(?:-(?:top|bottom|left|right))?\s*:\s*-[0-9.]+(?:px|pt|em|rem|%)[^;\}]+;?"
    r"|max-height\s*:\s*[^;\}]+;?"
    r"|page-break-[a-z-]+\s*:\s*[^;\}]+;?)",
    re.IGNORECASE,
)


def _sanitize_css_bytes(raw_css: bytes) -> bytes:
    text = raw_css.decode("utf-8", errors="ignore")
    text = _PAGE_RULE_RE.sub("", text)
    text = _BROKEN_PROPS_RE.sub("", text)
    text = text + "\n" + EPUB_READER_CSS
    return text.encode("utf-8")


def _get_cached_sanitized_epub(filepath: str) -> str:
    """Returns path to a disk-cached sanitized EPUB so we never re-unzip on every open."""
    abs_path = os.path.abspath(filepath)
    st = os.stat(abs_path)
    cache_key = f"{abs_path}:{st.st_mtime_ns}:{st.st_size}"
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:20]
    cached_epub = SANITIZED_EPUB_CACHE_DIR / f"{digest}.epub"

    if cached_epub.exists() and cached_epub.stat().st_size > 0:
        return str(cached_epub)

    with zipfile.ZipFile(abs_path, "r") as zin, zipfile.ZipFile(
        cached_epub, "w", compression=zipfile.ZIP_STORED
    ) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.lower().endswith(".css"):
                data = _sanitize_css_bytes(data)
            zout.writestr(item, data)

    return str(cached_epub)


def open_clean_document(
    filepath: str, fontsize: float = DEFAULT_EPUB_FONTSIZE
) -> pymupdf.Document:
    """
    Opens PDFs, EPUBs, FB2, MOBI, and CBZ files.
    Uses disk-cached sanitized EPUBs for fast loading and smooth continuous scrolling.
    """
    ext = Path(filepath).suffix.lower()

    if ext == ".epub" and zipfile.is_zipfile(filepath):
        try:
            clean_path = _get_cached_sanitized_epub(filepath)
            doc = pymupdf.open(clean_path)
        except Exception:
            doc = pymupdf.open(filepath)
    else:
        doc = pymupdf.open(filepath)

    if doc.is_reflowable:
        doc.layout(
            rect=pymupdf.Rect(0, 0, EPUB_PAGE_WIDTH, EPUB_PAGE_HEIGHT),
            fontsize=fontsize,
        )

    return doc