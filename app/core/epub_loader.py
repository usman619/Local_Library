import os
import re
import struct
import hashlib
import zipfile
from pathlib import Path
from urllib.parse import unquote
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

# Safe content box inside an EPUB page (Foliate-style max-inline-size & max-block-size)
MAX_IMG_WIDTH_PT = int(EPUB_PAGE_WIDTH - 90)     # 510pt
MAX_IMG_HEIGHT_PT = int(EPUB_PAGE_HEIGHT - 240)  # 580pt

_CSS_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")

# CSS properties that break vertical block flow and cause tables/diagrams to overlap text in MuPDF
_BANNED_CSS_PROPS = {
    "height",
    "min-height",
    "max-height",
    "width",
    "min-width",
    "max-width",
    "block-size",
    "min-block-size",
    "max-block-size",
    "inline-size",
    "min-inline-size",
    "max-inline-size",
    "float",
    "clear",
    "position",
    "top",
    "bottom",
    "left",
    "right",
    "inset",
    "display",
    "visibility",
    "overflow",
    "overflow-x",
    "overflow-y",
    "clip",
    "clip-path",
    "transform",
    "line-height",
    "vertical-align",
    "page-break-before",
    "page-break-after",
    "page-break-inside",
    "break-before",
    "break-after",
    "break-inside",
    "columns",
    "column-count",
    "column-width",
    "flex",
    "flex-direction",
    "flex-wrap",
    "align-items",
    "justify-content",
    "grid",
    "grid-template",
    "aspect-ratio",
    "object-fit",
}


def _sanitize_css_declarations(decl_block: str) -> str:
    """Filters a CSS declaration block (prop: val; ...) removing all flow-breaking properties."""
    kept = []
    for part in decl_block.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        prop, val = part.split(":", 1)
        prop_clean = prop.strip().lower()

        if prop_clean in _BANNED_CSS_PROPS:
            continue
        if prop_clean.startswith("margin") or prop_clean.startswith("padding"):
            continue
        kept.append(f"{prop.strip()}: {val.strip()}")
    return "; ".join(kept)


def _sanitize_css_text(css_text: str) -> str:
    """
    1. Strips all /* ... */ comments (and any trailing unclosed /* comment) FIRST
       so braces inside comments never cause 'unterminated comment' syntax errors in MuPDF.
    2. Strips @page rules and sanitizes every { ... } declaration block.
    """
    # Strip well-formed /* ... */ comments
    css_text = _CSS_COMMENT_RE.sub("", css_text)
    # Strip any stray unclosed /* comment at the end of a broken publisher CSS file
    if "/*" in css_text:
        css_text = css_text.split("/*", 1)[0]

    # Remove XML/HTML comment wrappers sometimes left inside <style><!-- ... --></style>
    css_text = css_text.replace("<!--", "").replace("-->", "")

    # Remove @page rules
    css_text = re.sub(r"@page\s*[^{]*\{[^}]*\}", "", css_text, flags=re.IGNORECASE | re.DOTALL)

    def _clean_block(match: re.Match) -> str:
        inner = match.group(1)
        if "{" in inner:
            return match.group(0)
        cleaned = _sanitize_css_declarations(inner)
        return "{ " + cleaned + " }"

    return re.sub(r"\{([^{}]*)\}", _clean_block, css_text)


def _sanitize_css_bytes(raw_css: bytes) -> bytes:
    text = raw_css.decode("utf-8", errors="ignore")
    text = _sanitize_css_text(text)
    text = text + "\n" + EPUB_READER_CSS
    return text.encode("utf-8")


def _get_image_size_reliable(data: bytes, filename: str) -> tuple[int, int] | None:
    """Reliably reads (width, height) for PNG, JPEG, GIF, and WebP images inside an EPUB."""
    if len(data) < 24:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return int(w), int(h)
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
    if orig_w <= 0 or orig_h <= 0:
        return int(MAX_IMG_WIDTH_PT * 0.8), int(MAX_IMG_HEIGHT_PT * 0.45)

    w, h = float(orig_w), float(orig_h)

    if h >= w * 1.25 and 200 <= w < MAX_IMG_WIDTH_PT * 0.80:
        scale_up = min(MAX_IMG_WIDTH_PT / w, MAX_IMG_HEIGHT_PT / h)
        w *= scale_up
        h *= scale_up

    if w > MAX_IMG_WIDTH_PT or h > MAX_IMG_HEIGHT_PT:
        scale_down = min(MAX_IMG_WIDTH_PT / w, MAX_IMG_HEIGHT_PT / h)
        w *= scale_down
        h *= scale_down

    return max(1, int(round(w))), max(1, int(round(h)))


_SVG_IMAGE_WRAPPER_RE = re.compile(
    r"<svg[^>]*>[\s\S]*?<image[^>]+(?:xlink:href|href)\s*=\s*([\"'][^\"']+[\"'])[^>]*\/?>[\s\S]*?<\/svg>",
    re.IGNORECASE,
)
_INLINE_IMG_WRAPPER_RE = re.compile(
    r"<(?:a|span)\b[^>]*>\s*(<img\b[^>]*>)\s*<\/(?:a|span)>",
    re.IGNORECASE,
)
_COLGROUP_RE = re.compile(
    r"<colgroup\b[^>]*>[\s\S]*?<\/colgroup>|<col\b[^>]*\/?>",
    re.IGNORECASE,
)
# Notice the (?!/) lookahead so self-closing tags like <div ... /> keep their trailing '/'!
_BLOCK_TAG_ATTR_CLEAN_RE = re.compile(
    r"<(table|thead|tbody|tfoot|tr|td|th|figure|figcaption|div|section|p)\b([^>]*?)(\/?>)",
    re.IGNORECASE,
)
_STRIP_LAYOUT_ATTRS_RE = re.compile(
    r'\s+(?:style|width|height|align|valign|cellspacing|cellpadding|border)\s*=\s*(?:"[^"]*"|\'[^\']*\')',
    re.IGNORECASE,
)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'\bsrc\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _sanitize_xhtml_bytes(
    raw_html: bytes, html_filename: str, img_dims: dict[str, tuple[int, int]]
) -> bytes:
    text = raw_html.decode("utf-8", errors="ignore")
    html_dir = os.path.dirname(html_filename)

    # 1. Convert <svg><image href="..."/></svg> wrappers into clean <img src=.../>
    text = _SVG_IMAGE_WRAPPER_RE.sub(r"<img src=\1 />", text)

    # 2. Unwrap <a><img></a> and <span><img></span>
    text = _INLINE_IMG_WRAPPER_RE.sub(r"\1", text)

    # 3. Remove <colgroup>/<col> fixed column widths inside tables
    text = _COLGROUP_RE.sub("", text)

    # 4. Strip layout-breaking attributes while preserving self-closing '/>'
    def _clean_block_tag(m: re.Match) -> str:
        tag_name = m.group(1)
        attrs = _STRIP_LAYOUT_ATTRS_RE.sub("", m.group(2))
        closing = m.group(3)
        return f"<{tag_name}{attrs}{closing}"

    text = _BLOCK_TAG_ATTR_CLEAN_RE.sub(_clean_block_tag, text)

    # 5. Sanitize any embedded <style>...</style> blocks in the XHTML
    text = re.sub(
        r"<style\b[^>]*>([\s\S]*?)<\/style>",
        lambda m: f"<style type=\"text/css\">{_sanitize_css_text(m.group(1))}</style>",
        text,
        flags=re.IGNORECASE,
    )

    # 6. Rewrite every <img> with exact proportional width & height
    def _fix_img_tag(match: re.Match) -> str:
        tag = match.group(0)
        src_m = _SRC_ATTR_RE.search(tag)
        if not src_m:
            return tag

        src_val = src_m.group(1)
        raw_src = unquote(src_val.split("#")[0].split("?")[0])
        norm_path = os.path.normpath(os.path.join(html_dir, raw_src)).replace("\\", "/")

        dims = (
            img_dims.get(norm_path)
            or img_dims.get(raw_src.lstrip("/"))
            or img_dims.get(os.path.basename(raw_src))
        )
        if dims:
            fit_w, fit_h = _compute_fitted_dimensions(dims[0], dims[1])
            is_tiny_icon = dims[0] <= 42 and dims[1] <= 42
        else:
            fit_w, fit_h = int(MAX_IMG_WIDTH_PT * 0.75), int(MAX_IMG_HEIGHT_PT * 0.4)
            is_tiny_icon = False

        if is_tiny_icon:
            return f'<img src="{src_val}" width="{fit_w}" height="{fit_h}" />'

        return (
            f'<div style="display:block; margin-top:8pt; margin-bottom:8pt; text-align:center;">'
            f'<img src="{src_val}" width="{fit_w}" height="{fit_h}" '
            f'style="display:block; margin-left:auto; margin-right:auto;" />'
            f"</div>"
        )

    text = _IMG_TAG_RE.sub(_fix_img_tag, text)

    # 7. Append our Foliate-style EPUB_READER_CSS right before </head>
    if "</head>" in text.lower():
        override_style = f"<style type=\"text/css\">\n{EPUB_READER_CSS}\n</style>\n</head>"
        text = re.sub(r"</head>", override_style, text, count=1, flags=re.IGNORECASE)

    return text.encode("utf-8")


def _get_cached_sanitized_epub(filepath: str) -> str:
    """Returns path to a disk-cached sanitized EPUB (v6 with comment-safe CSS parser & clean Zip headers)."""
    abs_path = os.path.abspath(filepath)
    st = os.stat(abs_path)
    # 'v6' invalidates v5 cache
    cache_key = f"v6:{abs_path}:{st.st_mtime_ns}:{st.st_size}"
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