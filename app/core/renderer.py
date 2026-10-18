import numpy as np
import pymupdf
from PySide6.QtGui import QImage, QPixmap

from app.config import THEMES, HIGHLIGHT_COLORS


class PageRenderer:
    """Renders PyMuPDF pages with HiDPI scaling, multi-color highlights, active TTS highlights, and Smart Dark Mode."""

    @classmethod
    def render_page(
        cls,
        doc: pymupdf.Document,
        page_index: int,
        zoom: float,
        theme_name: str,
        highlights: list[dict],
        active_tts_text: str | None = None,
        dpr: float = 1.25,
    ) -> QPixmap:
        page = doc.load_page(page_index)
        effective_scale = zoom * dpr
        mat = pymupdf.Matrix(effective_scale, effective_scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3
        ).copy()

        # 1. Apply Smart Dark / Sepia theme LUT first
        theme = THEMES.get(theme_name)
        if theme == "preserve_images":
            originals = []
            for img_info in page.get_images(full=True):
                for rect in page.get_image_rects(img_info[0]):
                    sr = rect * mat
                    x0, y0 = max(0, int(sr.x0)), max(0, int(sr.y0))
                    x1, y1 = min(pix.width, int(sr.x1)), min(pix.height, int(sr.y1))
                    if x1 > x0 and y1 > y0:
                        originals.append((y0, y1, x0, x1, img[y0:y1, x0:x1].copy()))

            img = cls._apply_duotone_lut(img, (24, 24, 37), (210, 218, 240))
            for y0, y1, x0, x1, patch in originals:
                img[y0:y1, x0:x1] = patch

        elif isinstance(theme, tuple):
            bg_rgb, fg_rgb = theme
            img = cls._apply_duotone_lut(img, bg_rgb, fg_rgb)

        # 2. Overlay Multi-Color User Highlights (Yellow, Green, Blue, Pink)
        for h_item in highlights:
            if isinstance(h_item, str):
                h_item = {"text": h_item, "color": "yellow", "note": ""}

            color_key = h_item.get("color", "yellow")
            rgb = HIGHLIGHT_COLORS.get(color_key, HIGHLIGHT_COLORS["yellow"])["rgb"]
            tint = np.array(rgb, dtype=np.float32)
            has_note = bool((h_item.get("note") or "").strip())

            saved_rects = h_item.get("rects")
            if saved_rects:
                rects = [pymupdf.Rect(*r) for r in saved_rects]
            else:
                rects = cls._find_text_rects(page, h_item.get("text", ""))

            for r_idx, rect in enumerate(rects):
                sr = rect * mat
                pad = max(1, int(1.5 * effective_scale))
                x0 = max(0, int(sr.x0) - pad)
                y0 = max(0, int(sr.y0) - pad)
                x1 = min(pix.width, int(sr.x1) + pad)
                y1 = min(pix.height, int(sr.y1) + pad)
                if x1 > x0 and y1 > y0:
                    region = img[y0:y1, x0:x1].astype(np.float32)
                    img[y0:y1, x0:x1] = np.clip(region * 0.62 + tint * 0.38, 0, 255).astype(np.uint8)
                    ul_h = max(1, int(1.5 * effective_scale))
                    img[max(y0, y1 - ul_h):y1, x0:x1] = rgb

                    # If this highlight has an attached note, draw a small solid badge in top-right corner
                    if has_note and r_idx == 0:
                        badge = max(4, int(5 * effective_scale))
                        bx0 = max(x0, x1 - badge)
                        by1 = min(y1, y0 + badge)
                        img[y0:by1, bx0:x1] = [250, 179, 135]  # Warm peach note indicator

        # 3. Overlay Active TTS Sentence Highlight (Catppuccin Blue #89B4FA + left bar)
        if active_tts_text and active_tts_text.strip():
            tts_rects = cls._find_text_rects(page, active_tts_text)
            accent = np.array([137, 180, 250], dtype=np.float32)
            for rect in tts_rects:
                sr = rect * mat
                pad = max(2, int(2 * effective_scale))
                x0 = max(0, int(sr.x0) - pad)
                y0 = max(0, int(sr.y0) - pad)
                x1 = min(pix.width, int(sr.x1) + pad)
                y1 = min(pix.height, int(sr.y1) + pad)
                if x1 > x0 and y1 > y0:
                    region = img[y0:y1, x0:x1].astype(np.float32)
                    img[y0:y1, x0:x1] = np.clip(region * 0.65 + accent * 0.35, 0, 255).astype(np.uint8)
                    bar_w = min(pix.width - x0, max(2, int(3 * effective_scale)))
                    img[y0:y1, x0:x0 + bar_w] = [137, 180, 250]

        img = np.ascontiguousarray(img, dtype=np.uint8)
        h, w, _ = img.shape
        qimg = QImage(img.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimg)
        pixmap.setDevicePixelRatio(dpr)
        return pixmap

    @staticmethod
    def _find_text_rects(page: pymupdf.Page, text: str) -> list[pymupdf.Rect]:
        cleaned = " ".join(text.split())
        if not cleaned:
            return []
        rects = page.search_for(cleaned[:90])
        if rects:
            return rects

        words = cleaned.split(" ")
        found = []
        for i in range(0, min(len(words), 24), 6):
            chunk = " ".join(words[i:i + 6])
            if len(chunk) >= 8:
                found.extend(page.search_for(chunk))
        return found

    @staticmethod
    def _apply_duotone_lut(img: np.ndarray, bg_rgb: tuple, fg_rgb: tuple) -> np.ndarray:
        t = np.linspace(0.0, 1.0, 256)
        lut = np.zeros((256, 3), dtype=np.uint8)
        for c in range(3):
            lut[:, c] = np.clip(
                fg_rgb[c] * (1.0 - t) + bg_rgb[c] * t, 0, 255
            ).astype(np.uint8)
        return np.stack([lut[img[:, :, c], c] for c in range(3)], axis=-1)