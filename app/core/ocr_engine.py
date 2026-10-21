import os
import re
import json
import shutil
import hashlib
from pathlib import Path
import pymupdf

from app.config import OCR_CACHE_DIR


def is_tesseract_available() -> bool:
    """Checks if tesseract-ocr is installed and accessible on the system PATH."""
    return shutil.which("tesseract") is not None


class HybridOCREngine:
    """
    Hybrid text extraction engine for LocalLibrary:
    - Fast Path (<1ms): Native PyMuPDF extraction with C-level de-hyphenation,
      reading-order sorting, and automatic header/footer filtering.
    - Smart OCR Path (On-Demand + Disk-Cached): Triggers PyMuPDF's native Tesseract
      bridge ONLY when a page is scanned (image-only) or has corrupted CID/Unicode fonts.
    """

    HEADER_CUTOFF_RATIO = 0.055  # Top 5.5% of page height
    FOOTER_CUTOFF_RATIO = 0.945  # Bottom 5.5% of page height

    @staticmethod
    def _cache_file(filepath: str, page_idx: int) -> Path:
        book_hash = hashlib.sha1(os.path.abspath(filepath).encode("utf-8")).hexdigest()[:16]
        return OCR_CACHE_DIR / f"{book_hash}_p{page_idx}.json"

    @classmethod
    def load_cached_ocr(cls, filepath: str, page_idx: int) -> dict | None:
        if not filepath:
            return None
        cfile = cls._cache_file(filepath, page_idx)
        if cfile.exists():
            try:
                return json.loads(cfile.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    @classmethod
    def save_cached_ocr(cls, filepath: str, page_idx: int, payload: dict):
        if not filepath:
            return
        cfile = cls._cache_file(filepath, page_idx)
        try:
            cfile.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _has_corrupted_encoding(blocks: list) -> bool:
        """Detects PDFs with broken ToUnicode maps (high ratio of \ufffd or control chars)."""
        total_chars = 0
        bad_chars = 0
        for b in blocks:
            if len(b) >= 5 and isinstance(b[4], str):
                txt = b[4]
                total_chars += len(txt)
                bad_chars += txt.count("\ufffd") + sum(
                    1 for c in txt if ord(c) < 32 and c not in ("\n", "\r", "\t")
                )
        if total_chars == 0:
            return False
        return (bad_chars / total_chars) > 0.05

    @classmethod
    def page_needs_ocr(cls, page: pymupdf.Page, native_blocks: list) -> tuple[bool, bool]:
        """
        Determines if a page requires Tesseract OCR.
        Returns: (should_run_ocr: bool, full_page_ocr: bool)
        """
        # Reflowable EPUBs/FB2/MOBI always have clean digital XHTML text
        if page.parent and page.parent.is_reflowable:
            return False, False

        # 1. Check for corrupted PDF font encodings -> requires full-page OCR
        if cls._has_corrupted_encoding(native_blocks):
            return True, True

        # Count meaningful native characters
        meaningful_chars = sum(
            len("".join( b[4].split() ))
            for b in native_blocks
            if len(b) >= 5 and isinstance(b[4], str) and (len(b) < 7 or b[6] == 0)
        )

        # If the page already has plenty of digital text, no OCR needed
        if meaningful_chars >= 45:
            return False, False

        # 2. Page has almost no digital text (< 45 chars). Check if it contains a scanned image
        page_area = max(1.0, abs(page.rect.width * page.rect.height))
        image_list = page.get_images(full=True)
        if image_list:
            # Check if images cover at least 20% of the page (scanned page or large diagram)
            total_img_area = 0.0
            for img_info in image_list:
                try:
                    for r in page.get_image_rects(img_info[0]):
                        total_img_area += abs(r.width * r.height)
                except Exception:
                    total_img_area += page_area * 0.5
            if (total_img_area / page_area) >= 0.20:
                return True, False

        return False, False

    @classmethod
    def _is_header_or_footer_block(
        cls,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        text: str,
        page_height: float,
        is_reflowable: bool,
    ) -> bool:
        """
        Filters out running headers, footers, and standalone page numbers on fixed-layout PDFs
        so TTS reads continuously across page boundaries without interruption.
        """
        clean = " ".join(text.split()).strip()
        if not clean:
            return True

        # Always skip standalone page numbers (e.g., "122", "- 45 -", "Page 12")
        if re.fullmatch(r"[-–—\s]*(?:page\s*)?\d+\s*(?:of\s*\d+)?[-–—\s]*", clean, re.IGNORECASE):
            return True

        # Reflowable EPUBs don't have baked-in running PDF headers/footers
        if is_reflowable or page_height <= 100:
            return False

        # On PDFs, filter short single-line running headers/footers at extreme top/bottom margins
        block_h = y1 - y0
        if len(clean) <= 95 and block_h <= 28.0:
            if y1 <= page_height * cls.HEADER_CUTOFF_RATIO:
                return True
            if y0 >= page_height * cls.FOOTER_CUTOFF_RATIO:
                return True

        return False

    @classmethod
    def extract_clean_blocks(
        cls,
        page: pymupdf.Page,
        page_idx: int,
        filepath: str = "",
        allow_ocr: bool = True,
    ) -> tuple[list, bool]:
        """
        Returns (cleaned_blocks, used_ocr_flag).
        Each block in cleaned_blocks is (x0, y0, x1, y1, text, block_no, 0).
        """
        is_reflowable = bool(page.parent and page.parent.is_reflowable)
        page_h = float(page.rect.height)

        # 1. Check disk cache first in case this scanned page was already OCR'd
        if filepath:
            cached = cls.load_cached_ocr(filepath, page_idx)
            if cached and "blocks" in cached:
                return cached["blocks"], True

        # 2. Fast native extraction with C-level de-hyphenation & reading-order sort
        flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE
        if hasattr(pymupdf, "TEXT_DEHYPHENATE"):
            flags |= pymupdf.TEXT_DEHYPHENATE

        native_blocks = page.get_text("blocks", sort=True, flags=flags)

        # 3. Check if Smart-Trigger OCR is needed
        used_ocr = False
        raw_blocks = native_blocks

        if allow_ocr and is_tesseract_available():
            should_ocr, full_ocr = cls.page_needs_ocr(page, native_blocks)
            if should_ocr:
                try:
                    tp = page.get_textpage_ocr(
                        flags=flags,
                        language="eng",
                        dpi=150,
                        full=full_ocr,
                    )
                    ocr_blocks = page.get_text("blocks", textpage=tp, sort=True)
                    ocr_words = page.get_text("words", textpage=tp, sort=True)
                    if ocr_blocks:
                        raw_blocks = ocr_blocks
                        used_ocr = True
                        # Persist both blocks and words so highlight lookup works without re-running OCR
                        cls.save_cached_ocr(
                            filepath,
                            page_idx,
                            {
                                "blocks": [
                                    [float(b[0]), float(b[1]), float(b[2]), float(b[3]), str(b[4]), int(b[5]), 0]
                                    for b in ocr_blocks
                                    if len(b) >= 5 and isinstance(b[4], str)
                                ],
                                "words": [
                                    [float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4])]
                                    for w in ocr_words
                                    if len(w) >= 5
                                ],
                            },
                        )
                except Exception:
                    pass

        # 4. Filter out image blocks, running headers/footers, and normalize OCR artifacts
        cleaned_blocks = []
        for b in raw_blocks:
            if len(b) < 5:
                continue
            if len(b) >= 7 and b[6] != 0:
                continue
            x0, y0, x1, y1, text = float(b[0]), float(b[1]), float(b[2]), float(b[3]), str(b[4])
            if cls._is_header_or_footer_block(x0, y0, x1, y1, text, page_h, is_reflowable):
                continue

            # Clean common OCR pipe/tilde noise when OCR was used
            if used_ocr:
                text = re.sub(r"\s+\|\s+", " ", text)

            cleaned_blocks.append((x0, y0, x1, y1, text, int(b[5]) if len(b) > 5 else 0, 0))

        return cleaned_blocks, used_ocr

    @classmethod
    def search_cached_ocr_rects(
        cls, filepath: str, page_idx: int, query_text: str
    ) -> list[pymupdf.Rect]:
        """
        Finds bounding boxes for TTS sentence highlighting on scanned/OCR'd pages
        using the cached word coordinates from Tesseract.
        """
        cached = cls.load_cached_ocr(filepath, page_idx)
        if not cached or "words" not in cached:
            return []

        words = cached["words"]  # list of [x0, y0, x1, y1, word_str]
        if not words:
            return []

        def _norm(s: str) -> str:
            return re.sub(r"[^\w]", "", s.lower())

        q_tokens = [_norm(t) for t in query_text.split() if _norm(t)]
        if not q_tokens:
            return []

        page_tokens = [_norm(w[4]) for w in words]
        n_q = len(q_tokens)
        n_p = len(page_tokens)

        # Match the sequence of words (or first 6 words if sentence is long)
        match_len = min(n_q, 6)
        needle = q_tokens[:match_len]

        for i in range(n_p - match_len + 1):
            if page_tokens[i : i + match_len] == needle:
                end_i = min(n_p, i + n_q)
                matched_words = words[i:end_i]
                # Merge word boxes on the same line into clean line rectangles
                return cls._merge_word_boxes_by_line(matched_words)

        return []

    @staticmethod
    def _merge_word_boxes_by_line(word_list: list) -> list[pymupdf.Rect]:
        if not word_list:
            return []
        lines: list[pymupdf.Rect] = []
        cur = pymupdf.Rect(word_list[0][:4])
        for w in word_list[1:]:
            r = pymupdf.Rect(w[:4])
            # Same line if vertical centers are within 8pt
            if abs(((r.y0 + r.y1) * 0.5) - ((cur.y0 + cur.y1) * 0.5)) <= 8.0:
                cur |= r
            else:
                lines.append(cur)
                cur = r
        lines.append(cur)
        return lines