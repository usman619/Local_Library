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
    if hasattr(pymupdf.TOOLS, "store_maxsize"):
        pymupdf.TOOLS.store_maxsize(256 * 1024 * 1024)
except Exception:
    pass

_CSS_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")


def _sanitize_css_text(css_text: str) -> str:
    """
    1. Strips all /* ... */ comments (and any trailing unclosed /* comment) FIRST
       so braces inside comments never cause 'unterminated comment' syntax errors in MuPDF.
    2. Strips @page rules that MuPDF's EPUB layout engine handles inconsistently.
       All other declarations are preserved so publisher table/image rules survive.
    """
    # Strip well-formed /* ... */ comments
    css_text = _CSS_COMMENT_RE.sub("", css_text)
    # Strip any stray unclosed /* comment at the end of a broken publisher CSS file
    if "/*" in css_text:
        css_text = css_text.split("/*", 1)[0]

    # Remove XML/HTML comment wrappers sometimes left inside <style><!-- ... --></style>
    css_text = css_text.replace("<!--", "").replace("-->", "")

    # MuPDF does not support WebKit's namespace selector syntax
    # (for example `aside[epub|type~="footnote"]`). Remove only those rules.
    css_text = re.sub(r"@namespace[^;]*;", "", css_text, flags=re.IGNORECASE)

    def _remove_namespace_rule(match: re.Match[str]) -> str:
        return "" if "|" in match.group(1) else match.group(0)

    css_text = re.sub(
        r"([^{}]+)\{[^{}]*\}",
        _remove_namespace_rule,
        css_text,
    )

    # Remove @page rules
    css_text = re.sub(r"@page\s*[^{]*\{[^}]*\}", "", css_text, flags=re.IGNORECASE | re.DOTALL)

    return css_text


def _sanitize_css_bytes(raw_css: bytes) -> bytes:
    text = raw_css.decode("utf-8", errors="ignore")
    text = _sanitize_css_text(text)
    text = text + "\n" + EPUB_READER_CSS
    return text.encode("utf-8")


def _sanitize_xhtml_bytes(
    raw_html: bytes,
    _html_filename: str = "",
    _img_dims: dict[str, tuple[int, int]] | None = None,
) -> bytes:
    text = raw_html.decode("utf-8", errors="ignore")
    # Preserve EPUB-authored markup and inline styles. Foliate delegates layout
    # to a browser engine, so removing table/image attributes changes the book.
    # Only sanitize embedded CSS comments and append reader-level CSS.
    text = re.sub(
        r"<style\b[^>]*>([\s\S]*?)<\/style>",
        lambda m: f"<style type=\"text/css\">{_sanitize_css_text(m.group(1))}</style>",
        text,
        flags=re.IGNORECASE,
    )

    # Append our Foliate-style EPUB_READER_CSS right before </head>.
    if "</head>" in text.lower():
        override_style = f"<style type=\"text/css\">\n{EPUB_READER_CSS}\n</style>\n</head>"
        text = re.sub(r"</head>", override_style, text, count=1, flags=re.IGNORECASE)

    return text.encode("utf-8")


def _get_cached_sanitized_epub(filepath: str) -> str:
    """Returns path to a disk-cached EPUB with Foliate-style reader CSS."""
    abs_path = os.path.abspath(filepath)
    st = os.stat(abs_path)
    # 'v9' invalidates output generated before MuPDF namespace-selector cleanup.
    cache_key = f"v9:{abs_path}:{st.st_mtime_ns}:{st.st_size}"
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:20]
    cached_epub = SANITIZED_EPUB_CACHE_DIR / f"{digest}.epub"

    if cached_epub.exists() and cached_epub.stat().st_size > 0:
        return str(cached_epub)

    with zipfile.ZipFile(abs_path, "r") as zin:
        with zipfile.ZipFile(cached_epub, "w", compression=zipfile.ZIP_STORED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                lower_name = item.filename.lower()
                if lower_name.endswith(".css"):
                    data = _sanitize_css_bytes(data)
                elif lower_name.endswith((".xhtml", ".html", ".htm")):
                    data = _sanitize_xhtml_bytes(
                        data, item.filename.replace("\\", "/")
                    )
                # Always pass item.filename (str) instead of item (ZipInfo) so zipfile
                # computes fresh file_size & CRC32 headers without leaving trailing bytes!
                zout.writestr(item.filename, data)

    return str(cached_epub)


def open_clean_document(
    filepath: str, fontsize: float = DEFAULT_EPUB_FONTSIZE
) -> pymupdf.Document:
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