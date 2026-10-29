import numpy as np
import pymupdf
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QPen

from app.config import THEMES, HIGHLIGHT_COLORS


class PageRenderer:
    """
    High-performance PyMuPDF page renderer supporting:
    - Smart Dark Mode & Sepia/Nord NumPy LUT color filters (including preserve_images mode)
    - Multi-color user highlights with custom note badges
    - In-book search match highlights (orange overlays)
    - Real-time sky-blue TTS sentence highlights with bounding box clipping
    """

    @staticmethod
    def _merge_line_rects(rects: list) -> list:
        """
        Merges individual word-level or span-level rectangles that sit on the same
        horizontal line into a single continuous row rectangle.
        """
        if not rects:
            return []

        import pymupdf

        norm: list[list[float]] = []
        for r in rects:
            if hasattr(r, "x0"):  # pymupdf.Rect
                norm.append([float(r.x0), float(r.y0), float(r.x1), float(r.y1)])
            elif len(r) >= 4:
                norm.append([float(r[0]), float(r[1]), float(r[2]), float(r[3])])

        if not norm:
            return []

        # Group rectangles by line (vertical center proximity) and sort left-to-right
        lines: list[list[list[float]]] = []
        for box in norm:
            y_mid = 0.5 * (box[1] + box[3])
            h = max(1.0, box[3] - box[1])
            placed = False
            for line in lines:
                ref = line[0]
                ref_mid = 0.5 * (ref[1] + ref[3])
                ref_h = max(1.0, ref[3] - ref[1])
                if abs(y_mid - ref_mid) <= 0.55 * min(h, ref_h):
                    line.append(box)
                    placed = True
                    break
            if not placed:
                lines.append([box])

        merged: list[pymupdf.Rect] = []
        for line in lines:
            line.sort(key=lambda b: b[0])
            cur = line[0][:]
            for nxt in line[1:]:
                line_h = max(1.0, cur[3] - cur[1])
                # Merge if horizontal gap is within normal word spacing
                if (nxt[0] - cur[2]) <= line_h * 3.0:
                    cur[0] = min(cur[0], nxt[0])
                    cur[1] = min(cur[1], nxt[1])
                    cur[2] = max(cur[2], nxt[2])
                    cur[3] = max(cur[3], nxt[3])
                else:
                    merged.append(pymupdf.Rect(cur[0], cur[1], cur[2], cur[3]))
                    cur = nxt[:]
            merged.append(pymupdf.Rect(cur[0], cur[1], cur[2], cur[3]))

        return merged

    @staticmethod
    def _find_text_rects(
        page: pymupdf.Page,
        text: str,
        filepath: str = "",
        page_idx: int = 0,
        clip_bbox: tuple[float, float, float, float] | None = None,
    ) -> list[pymupdf.Rect]:
        """
        Finds bounding rectangles for a sentence on a page, strictly constrained
        to clip_bbox (when provided) so TTS highlights never jump across columns or diagrams.
        """
        if not text or not text.strip():
            return []
        clean = " ".join(text.split()).strip()

        clip_rect = None
        if clip_bbox and len(clip_bbox) == 4:
            clip_rect = pymupdf.Rect(
                clip_bbox[0] - 6,
                clip_bbox[1] - 6,
                clip_bbox[2] + 6,
                clip_bbox[3] + 6,
            )

        # 1. Search inside the exact paragraph bounding box first
        if clip_rect is not None:
            rects = page.search_for(clean, clip=clip_rect)
            if rects:
                return PageRenderer._merge_line_rects(rects)
            if len(clean) > 40:
                rects = page.search_for(clean[:50].strip(), clip=clip_rect)
                if rects:
                    return PageRenderer._merge_line_rects(rects)

        # 2. Full-page direct search
        rects = page.search_for(clean)
        if rects:
            if clip_rect is not None:
                in_box = [r for r in rects if r.intersects(clip_rect)]
                if in_box:
                    return PageRenderer._merge_line_rects(in_box)
            selected = [rects[0]] if len(clean) < 25 else rects
            return PageRenderer._merge_line_rects(selected)

        # 3. Prefix search
        if len(clean) > 45:
            rects = page.search_for(clean[:60].strip(), clip=clip_rect)
            if rects:
                return PageRenderer._merge_line_rects(rects)

        # 4. Scanned PDF fallback: check disk-cached Tesseract OCR word boxes
        if filepath:
            try:
                from app.core.ocr_engine import HybridOCREngine
                ocr_rects = HybridOCREngine.search_cached_ocr_rects(
                    filepath, page_idx, clean
                )
                if ocr_rects:
                    return PageRenderer._merge_line_rects(ocr_rects)
            except Exception:
                pass

        # 5. Final fallback: paragraph box
        if clip_rect is not None:
            return [pymupdf.Rect(clip_bbox)]

        return []

    @staticmethod
    def _apply_lut_to_rgb_buffer(
        samples: bytes, width: int, height: int, stride: int, bg_rgb: tuple, fg_rgb: tuple
    ) -> QImage:
        """Applies a fast vectorized NumPy linear interpolation LUT between bg_rgb and fg_rgb."""
        arr = np.frombuffer(samples, dtype=np.uint8).reshape((height, stride))
        rgb = arr[:, : width * 3].reshape((height, width, 3))

        # Integer luminance (0..255), then a 256-entry colour table. This replaces
        # several full-size float32 passes with one uint16 pass and one table lookup.
        lum = (
            (rgb[:, :, 0].astype(np.uint16) * 77)
            + (rgb[:, :, 1].astype(np.uint16) * 150)
            + (rgb[:, :, 2].astype(np.uint16) * 29)
        ) >> 8
        t = np.arange(256, dtype=np.float32) / 255.0
        table = np.empty((256, 3), dtype=np.uint8)
        for c in range(3):
            table[:, c] = np.clip(fg_rgb[c] + t * (bg_rgb[c] - fg_rgb[c]), 0, 255).astype(np.uint8)
        out = table[lum]

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
        Universal HiDPI PageRenderer that accepts any positional or keyword signature.
        """
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

        effective_scale = max(0.1, zoom * dpr)
        mat = pymupdf.Matrix(effective_scale, effective_scale)
        if rotation:
            mat = mat.prerotate(rotation)

        pix = page.get_pixmap(matrix=mat, alpha=False)
        theme_spec = THEMES.get(theme_name)

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

        painter = QPainter(qimg)
        painter.setRenderHint(QPainter.Antialiasing, True)

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

        if tts_text and str(tts_text).strip():
            doc_path = getattr(doc, "name", "") if doc else ""
            tts_bbox = kwargs.get("active_tts_bbox")
            tts_rects = cls._find_text_rects(
                page,
                str(tts_text),
                filepath=doc_path,
                page_idx=p_idx,
                clip_bbox=tts_bbox,
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

        out_pixmap = QPixmap.fromImage(qimg)
        out_pixmap.setDevicePixelRatio(dpr)
        return out_pixmap