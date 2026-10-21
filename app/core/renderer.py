import numpy as np
import pymupdf
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QFont

from app.config import THEMES, HIGHLIGHT_COLORS


class PageRenderer:
    """
    High-performance PyMuPDF page renderer supporting:
    - Smart Dark Mode & Sepia/Nord NumPy LUT color filters (including preserve_images mode)
    - Multi-color user highlights with custom note badges
    - In-book search match highlights (orange overlays)
    - Real-time sky-blue TTS sentence highlights (both native digital text and cached Tesseract OCR)
    """

    @staticmethod
    def _find_text_rects(
        page: pymupdf.Page, text: str, filepath: str = "", page_idx: int = 0
    ) -> list[pymupdf.Rect]:
        """Finds bounding rectangles for a sentence or highlight on both digital and scanned (OCR) pages."""
        if not text or not text.strip():
            return []
        clean = " ".join(text.split()).strip()

        # 1. Direct native search
        rects = page.search_for(clean)
        if rects:
            return rects

        # 2. First 65 characters
        if len(clean) > 45:
            rects = page.search_for(clean[:65].strip())
            if rects:
                return rects

        # 3. Multi-chunk search (handles hyphenated line breaks and multi-line wraps)
        words = clean.split()
        if len(words) >= 3:
            found_rects: list[pymupdf.Rect] = []
            chunk_size = 5
            for i in range(0, min(len(words), 30), chunk_size):
                chunk = " ".join(words[i : i + chunk_size])
                if len(chunk) >= 4:
                    found_rects.extend(page.search_for(chunk))
            if found_rects:
                return found_rects

        # 4. Scanned PDF fallback: check disk-cached Tesseract OCR word boxes
        if filepath:
            try:
                from app.core.ocr_engine import HybridOCREngine
                ocr_rects = HybridOCREngine.search_cached_ocr_rects(
                    filepath, page_idx, clean
                )
                if ocr_rects:
                    return ocr_rects
            except Exception:
                pass

        return []

    @staticmethod
    def _apply_lut_to_rgb_buffer(
        samples: bytes, width: int, height: int, stride: int, bg_rgb: tuple, fg_rgb: tuple
    ) -> QImage:
        """Applies a fast vectorized NumPy linear interpolation LUT between bg_rgb and fg_rgb."""
        arr = np.frombuffer(samples, dtype=np.uint8).reshape((height, stride))
        rgb = arr[:, : width * 3].reshape((height, width, 3))

        # Perceived luminance (0.0 = black text, 1.0 = white paper)
        lum = (
            0.299 * rgb[:, :, 0].astype(np.float32)
            + 0.587 * rgb[:, :, 1].astype(np.float32)
            + 0.114 * rgb[:, :, 2].astype(np.float32)
        ) / 255.0

        out = np.empty_like(rgb)
        for c in range(3):
            # lum == 1.0 (white paper) -> bg_rgb[c]; lum == 0.0 (black ink) -> fg_rgb[c]
            val = fg_rgb[c] + lum * (bg_rgb[c] - fg_rgb[c])
            out[:, :, c] = np.clip(val, 0, 255).astype(np.uint8)

        contiguous = np.ascontiguousarray(out)
        qimg = QImage(
            contiguous.data,
            width,
            height,
            width * 3,
            QImage.Format_RGB888,
        )
        return qimg.copy()

    @classmethod
    def render_page(cls, *args, **kwargs) -> QPixmap:
        """
        Universal HiDPI PageRenderer that accepts any positional or keyword signature:
        - PageRenderer.render_page(page, zoom, theme_name, dpr=..., active_tts_text=..., user_highlights=..., search_query=...)
        - PageRenderer.render_page(doc, page_idx, zoom, theme_name, ...)
        """
        # 1. Unpack positional arguments safely without ever colliding with **kwargs
        page: pymupdf.Page | None = None
        doc: pymupdf.Document | None = None
        p_idx: int = 0
        zoom: float = float(kwargs.get("zoom", 1.0))
        theme_name: str = str(
            kwargs.get("theme_name")
            or kwargs.get("theme_mode")
            or "Original (Light)"
        )
        dpr: float = float(kwargs.get("dpr", 1.0))
        rotation: int = int(kwargs.get("rotation", 0))

        if len(args) >= 1:
            first = args[0]
            if isinstance(first, pymupdf.Document):
                doc = first
                p_idx = int(args[1]) if len(args) >= 2 else int(kwargs.get("page_idx", 0))
                page = doc.load_page(p_idx)
                if len(args) >= 3 and isinstance(args[2], (int, float)):
                    zoom = float(args[2])
                if len(args) >= 4 and isinstance(args[3], str):
                    theme_name = args[3]
            else:
                # Standard call from widgets.py: (page, zoom, theme_name, ...)
                page = first
                doc = page.parent
                p_idx = int(page.number)
                if len(args) >= 2 and isinstance(args[1], (int, float)):
                    zoom = float(args[1])
                if len(args) >= 3 and isinstance(args[2], str):
                    theme_name = args[2]
                if len(args) >= 4 and isinstance(args[3], (int, float)):
                    dpr = float(args[3])

        if page is None:
            page = kwargs.get("page")
            doc = page.parent if page else None
            p_idx = int(page.number) if page else 0

        # 2. Extract keyword overlay parameters
        active_tts_page = kwargs.get("active_tts_page")
        tts_text = (
            kwargs.get("active_tts_text")
            or kwargs.get("active_tts_sentence")
            or kwargs.get("tts_text")
        )
        if active_tts_page is not None and int(active_tts_page) != p_idx:
            tts_text = None

        hl_list = (
            kwargs.get("user_highlights")
            if "user_highlights" in kwargs
            else kwargs.get("highlights", [])
        )
        search_query = str(kwargs.get("search_query", "") or "")

        # 3. Render base page pixmap at HiDPI scale (zoom * dpr)
        effective_scale = max(0.1, zoom * dpr)
        mat = pymupdf.Matrix(effective_scale, effective_scale)
        if rotation:
            mat = mat.prerotate(rotation)

        pix = page.get_pixmap(matrix=mat, alpha=False)
        theme_spec = THEMES.get(theme_name)

        # 4. Apply reading theme (Original, Smart Dark, Keep Images, Sepia, Nord)
        if theme_spec is None:
            qimg = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888
            ).copy()
        elif theme_spec == "preserve_images":
            bg_rgb, fg_rgb = (30, 30, 46), (205, 214, 244)
            qimg = cls._apply_lut_to_rgb_buffer(
                pix.samples, pix.width, pix.height, pix.stride, bg_rgb, fg_rgb
            )
            orig_qimg = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888
            )
            img_painter = QPainter(qimg)
            for img_info in page.get_images(full=True):
                try:
                    for img_rect in page.get_image_rects(img_info[0]):
                        sr = img_rect * mat
                        rx, ry = max(0, int(sr.x0)), max(0, int(sr.y0))
                        rw = min(orig_qimg.width() - rx, int(sr.width))
                        rh = min(orig_qimg.height() - ry, int(sr.height))
                        if rw > 0 and rh > 0:
                            img_painter.drawImage(rx, ry, orig_qimg, rx, ry, rw, rh)
                except Exception:
                    pass
            img_painter.end()
        else:
            bg_rgb, fg_rgb = theme_spec
            qimg = cls._apply_lut_to_rgb_buffer(
                pix.samples, pix.width, pix.height, pix.stride, bg_rgb, fg_rgb
            )

        # 5. Paint overlays (User Highlights, Search Matches, Active TTS Sentence)
        painter = QPainter(qimg)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # 5a. Saved User Highlights & Note Indicators
        if hl_list:
            for item in hl_list:
                if isinstance(item, str):
                    hl_text = item
                    color_key = "yellow"
                    note_text = ""
                    saved_rects = None
                elif isinstance(item, dict):
                    if "page" in item and int(item["page"]) != p_idx:
                        continue
                    hl_text = item.get("text", "")
                    color_key = item.get("color", "yellow")
                    note_text = item.get("note", "")
                    saved_rects = item.get("rects")
                else:
                    continue

                c_info = HIGHLIGHT_COLORS.get(color_key, HIGHLIGHT_COLORS["yellow"])
                r_c, g_c, b_c = (
                    item.get("color_rgb", c_info["rgb"])
                    if isinstance(item, dict)
                    else c_info["rgb"]
                )

                painter.setBrush(QColor(r_c, g_c, b_c, 95))
                painter.setPen(QPen(QColor(r_c, g_c, b_c, 185), max(1.0, 1.0 * dpr)))

                rects_to_draw: list[pymupdf.Rect] = []
                if saved_rects:
                    for sr in saved_rects:
                        if len(sr) == 4:
                            rects_to_draw.append(pymupdf.Rect(sr))
                elif hl_text:
                    rects_to_draw = cls._find_text_rects(page, hl_text)

                first_scaled_rect = None
                for r in rects_to_draw:
                    sr = r * mat
                    qrect = QRectF(sr.x0, sr.y0, sr.width, sr.height)
                    if first_scaled_rect is None:
                        first_scaled_rect = qrect
                    painter.drawRoundedRect(qrect, 3.0 * dpr, 3.0 * dpr)

                if note_text and first_scaled_rect is not None:
                    badge_sz = 14.0 * dpr
                    bx = max(2.0, first_scaled_rect.right() - badge_sz * 0.4)
                    by = max(2.0, first_scaled_rect.top() - badge_sz * 0.5)
                    badge_rect = QRectF(bx, by, badge_sz, badge_sz)
                    painter.setBrush(QColor("#F9E2AF"))
                    painter.setPen(QPen(QColor("#11111B"), 1.2 * dpr))
                    painter.drawEllipse(badge_rect)

        # 5b. In-Book Search Query Matches (Orange Highlight)
        if search_query.strip():
            s_rects = page.search_for(search_query.strip())
            if s_rects:
                painter.setBrush(QColor(250, 179, 135, 110))
                painter.setPen(QPen(QColor(250, 179, 135, 220), max(1.5, 1.5 * dpr)))
                for r in s_rects:
                    sr = r * mat
                    painter.drawRoundedRect(
                        QRectF(sr.x0, sr.y0, sr.width, sr.height), 3.0 * dpr, 3.0 * dpr
                    )

        # 5c. Active Neural TTS Sentence Highlight (Sky-Blue Overlay)
        if tts_text and str(tts_text).strip():
            doc_path = getattr(doc, "name", "") if doc else ""
            tts_rects = cls._find_text_rects(
                page, str(tts_text), filepath=doc_path, page_idx=p_idx
            )
            if tts_rects:
                painter.setBrush(QColor(137, 180, 250, 85))
                painter.setPen(QPen(QColor(137, 180, 250, 210), max(1.5, 1.5 * dpr)))
                for r in tts_rects:
                    sr = r * mat
                    painter.drawRoundedRect(
                        QRectF(
                            sr.x0 - 2 * dpr,
                            sr.y0 - 1 * dpr,
                            sr.width + 4 * dpr,
                            sr.height + 2 * dpr,
                        ),
                        3.5 * dpr,
                        3.5 * dpr,
                    )

        painter.end()

        # 6. Return HiDPI QPixmap
        out_pixmap = QPixmap.fromImage(qimg)
        out_pixmap.setDevicePixelRatio(dpr)
        return out_pixmap