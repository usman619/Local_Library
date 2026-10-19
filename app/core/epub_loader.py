import os
import re
import struct
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

# Usable content box inside an EPUB page (leaving room for captions on the same page)
MAX_IMG_WIDTH_PT = int(EPUB_PAGE_WIDTH - 84)    # 516pt
MAX_IMG_HEIGHT_PT = int(EPUB_PAGE_HEIGHT - 220)  # 600pt

_PAGE_RULE_RE = re.compile(r"@page\s*\{[^}]*\}", re.IGNORECASE | re.DOTALL)

# Strip ALL CSS properties that cause MuPDF (muhtml) to miscalculate vertical height
# of tables, figures, and divs (causing text to overlap on top of tables/diagrams).
_BROKEN_PROPS_RE = re.compile(
    r"(\bfloat\s*:\s*[^;\}]+;?"
    r"|\bclear\s*:\s*[^;\}]+;?"
    r"|\bposition\s*:\s*[^;\}]+;?"
    r"|\btop\s*:\s*[^;\}]+;?"
    r"|\bbottom\s*:\s*[^;\}]+;?"
    r"|\bleft\s*:\s*[^;\}]+;?"
    r"|\bright\s*:\s*[^;\}]+;?"
    r"|\btransform\s*:\s*[^;\}]+;?"
    r"|\boverflow(?:-[xy])?\s*:\s*[^;\}]+;?"
    r"|\bdisplay\s*:\s*(?:flex|inline-flex|grid|inline-grid|inline-block|inline-table)[^;\}]+;?"
    r"|\b(?:min-|max-)?height\s*:\s*[^;\}]+;?"
    r"|\b(?:min-|max-)?width\s*:\s*[0-9.]+(?:%|vh|vw)[^;\}]+;?"
    r"|\bmargin(?:-(?:top|bottom|left|right))?\s*:\s*-[0-9.]+[^;\}]+;?"
    r"|\bline-height\s*:\s*(?:0|[0-9.]+px|[0-9.]+pt)[^;\}]+;?"
    r"|\bpage-break-[a-z-]+\s*:\s*[^;\}]+;?"
    r"|\bbreak-[a-z-]+\s*:\s*[^;\}]+;?)",
    re.IGNORECASE,
)

# Unwraps <svg ...><image xlink:href="..."/></svg> into a standard <img> tag
_SVG_IMAGE_WRAPPER_RE = re.compile(
    r"<svg[^>]*>[\s\S]*?<image[^>]+(?:xlink:href|href)\s*=\s*([\"'][^\"']+[\"'])[^>]*\/?>[\s\S]*?<\/svg>",
    re.IGNORECASE,
)

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'\bsrc\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(r'\s+style\s*=\s*["\'][^"\']*["\']', re.IGNORECASE)
_WIDTH_ATTR_RE = re.compile(r'\s+width\s*=\s*["\'][^"\']*["\']', re.IGNORECASE)
_HEIGHT_ATTR_RE = re.compile(r'\s+height\s*=\s*["\'][^"\']*["\']', re.IGNORECASE)

# Strip inline style="..." on table, tr, td, th, div, figure, p so fixed heights never cause overlap
_CONTAINER_INLINE_STYLE_RE = re.compile(
    r"(<(?:table|thead|tbody|tfoot|tr|td|th|figure|figcaption|div|p)\b[^>]*?)\s+style\s*=\s*[\"'][^\"']*[\"']([^>]*>)",
    re.IGNORECASE,
)


def _get_image_size_reliable(data: bytes, filename: str) -> tuple[int, int] | None:
    """Reliably reads (width, height) for PNG, JPEG, GIF, and WebP images inside an EPUB."""
    if len(data) < 24:
        return None
    # 1. Fast PNG header check
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    # 2. Fast GIF header check
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return int(w), int(h)
    # 3. PyMuPDF Pixmap / Document stream for JPEG, WebP, etc.
    try:
        pix = pymupdf.Pixmap(data)
        if pix.width > 0 and pix.height > 0:
            return pix.width, pix.height
    except Exception:
        pass
    try:
        ext = Path(filename).suffix.lstrip(".").lower() or "jpg"
        with pymupdf.open(stream=data, filetype=ext) as img_doc:
            rect = img_doc[0].rect
            return int(rect.width), int(rect.height)
    except Exception:
        return None


def _compute_fitted_dimensions(orig_w: int, orig_h: int) -> tuple[int, int]:
    """
    Computes exact (width_pt, height_pt) for an image in MuPDF so it fits cleanly
    on the page along with its surrounding figure caption and text without overflowing.
    """
    if orig_w <= 0 or orig_h <= 0:
        return int(MAX_IMG_WIDTH_PT * 0.85), int(MAX_IMG_HEIGHT_PT * 0.5)

    w, h = float(orig_w), float(orig_h)

    # Scale up low-res full-page portrait covers/illustrations
    if h >= w * 1.25 and 180 <= w < MAX_IMG_WIDTH_PT * 0.80:
        scale_up = min(MAX_IMG_WIDTH_PT / w, MAX_IMG_HEIGHT_PT / h)
        w *= scale_up
        h *= scale_up

    # Scale down any diagram or image larger than our safe page content box
    if w > MAX_IMG_WIDTH_PT or h > MAX_IMG_HEIGHT_PT:
        scale_down = min(MAX_IMG_WIDTH_PT / w, MAX_IMG_HEIGHT_PT / h)
        w *= scale_down
        h *= scale_down

    return max(1, int(round(w))), max(1, int(round(h)))


def _sanitize_css_bytes(raw_css: bytes) -> bytes:
    text = raw_css.decode("utf-8", errors="ignore")
    text = _PAGE_RULE_RE.sub("", text)
    text = _BROKEN_PROPS_RE.sub("", text)
    text = text + "\n" + EPUB_READER_CSS
    return text.encode("utf-8")


def _sanitize_xhtml_bytes(
    raw_html: bytes, html_filename: str, img_dims: dict[str, tuple[int, int]]
) -> bytes:
    text = raw_html.decode("utf-8", errors="ignore")
    html_dir = os.path.dirname(html_filename)

    # 1. Convert <svg><image href="..."/></svg> wrappers into clean <p><img></p> blocks
    text = _SVG_IMAGE_WRAPPER_RE.sub(r'<p><img src=\1 /></p>', text)

    # 2. Strip inline styles on tables/divs/figures that force fixed heights and cause text overlap
    text = _CONTAINER_INLINE_STYLE_RE.sub(r"\1\2", text)

    # 3. Also sanitize any internal <style>...</style> blocks embedded inside the XHTML head
    def _clean_internal_style(m: re.Match) -> str:
        inner = _PAGE_RULE_RE.sub("", m.group(1))
        inner = _BROKEN_PROPS_RE.sub("", inner)
        return f"<style type=\"text/css\">{inner}</style>"

    text = re.sub(
        r"<style\b[^>]*>([\s\S]*?)<\/style>",
        _clean_internal_style,
        text,
        flags=re.IGNORECASE,
    )

    # 4. Rewrite every <img> tag with HTML width="W" height="H" attributes ONLY
    #    (In MuPDF, HTML width/height attributes properly reserve vertical layout space!)
    def _fix_img_tag(match: re.Match) -> str:
        tag = match.group(0)
        src_m = _SRC_ATTR_RE.search(tag)
        if not src_m:
            return tag

        raw_src = urllib_unquote(src_m.group(1).split("#")[0].split("?")[0])
        norm_path = os.path.normpath(os.path.join(html_dir, raw_src)).replace("\\", "/")

        dims = (
            img_dims.get(norm_path)
            or img_dims.get(raw_src.lstrip("/"))
            or img_dims.get(os.path.basename(raw_src))
        )
        if dims:
            fit_w, fit_h = _compute_fitted_dimensions(dims[0], dims[1])
        else:
            fit_w, fit_h = int(MAX_IMG_WIDTH_PT * 0.8), int(MAX_IMG_HEIGHT_PT * 0.45)

        clean_tag = _STYLE_ATTR_RE.sub("", tag)
        clean_tag = _WIDTH_ATTR_RE.sub("", clean_tag)
        clean_tag = _HEIGHT_ATTR_RE.sub("", clean_tag)

        size_attrs = f' width="{fit_w}" height="{fit_h}"'
        if clean_tag.endswith("/>"):
            return clean_tag[:-2] + size_attrs + " />"
        return clean_tag[:-1] + size_attrs + ">"

    text = _IMG_TAG_RE.sub(_fix_img_tag, text)

    # 5. Inject our reader CSS override right before </head>
    if "</head>" in text.lower():
        override_style = f"<style type=\"text/css\">\n{EPUB_READER_CSS}\n</style>\n</head>"
        text = re.sub(r"</head>", override_style, text, count=1, flags=re.IGNORECASE)

    return text.encode("utf-8")


def urllib_unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


def _get_cached_sanitized_epub(filepath: str) -> str:
    """Returns path to a disk-cached sanitized EPUB (v4 with table & figure flow fixes)."""
    abs_path = os.path.abspath(filepath)
    st = os.stat(abs_path)
    # 'v4' invalidates older caches so tables and JPEG diagrams re-layout cleanly
    cache_key = f"v4:{abs_path}:{st.st_mtime_ns}:{st.st_size}"
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:20]
    cached_epub = SANITIZED_EPUB_CACHE_DIR / f"{digest}.epub"

    if cached_epub.exists() and cached_epub.stat().st_size > 0:
        return str(cached_epub)

    with zipfile.ZipFile(abs_path, "r") as zin:
        img_dims: dict[str, tuple[int, int]] = {}
        img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp")
        for item in zin.infolist():
            norm_name = item.filename.replace("\\", "/")
            if norm_name.lower().endswith(img_exts):
                sz = _get_image_size_reliable(zin.read(item.filename), norm_name)
                if sz:
                    img_dims[norm_name] = sz
                    # Also index by basename as fallback for relative paths
                    img_dims.setdefault(os.path.basename(norm_name), sz)

        with zipfile.ZipFile(cached_epub, "w", compression=zipfile.ZIP_STORED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                lower_name = item.filename.lower()
                if lower_name.endswith(".css"):
                    data = _sanitize_css_bytes(data)
                elif lower_name.endswith((".xhtml", ".html", ".htm")):
                    data = _sanitize_xhtml_bytes(
                        data, item.filename.replace("\\", "/"), img_dims
                    )
                zout.writestr(item, data)

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