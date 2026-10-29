import os
import re
import json
import shutil
import hashlib
from pathlib import Path
import pymupdf

from app.config import OCR_CACHE_DIR


# Standalone or decorated page numbers: e.g. "42", "Page 42", "p. 42", "42 of 300", "- 42 -", "[42]", "• 42 •"
_PAGE_NUM_RE = re.compile(
    r"^[-–—~•·|\[\]\(\)\s]*(?:page|p\.|pp\.)?\s*\d+\s*(?:(?:of|/)\s*\d+)?[-–—~•·|\[\]\(\)\s]*$",
    re.IGNORECASE,
)

# Roman numeral page numbers: e.g. "iv", "XIV", "- xii -", "Page iv", "[ix]"
_ROMAN_NUM_RE = re.compile(
    r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)
_ROMAN_EXCLUDE_WORDS = {"i", "mix", "did", "dim", "mid"}


def is_roman_page_num(s: str) -> bool:
    """Detects standalone Roman numeral page numbers like 'iv', 'XIV', '- xii -', 'Page IV'."""
    raw = s.strip(" -–—~•·|[]()")
    has_decor = len(raw) < len(s.strip()) or bool(re.match(r"^(?:page|p\.)\s*", s.strip(), re.I))
    clean = re.sub(r"^(?:page|p\.)\s*", "", raw, flags=re.IGNORECASE).strip()
    if not clean or len(clean) > 12:
        return False
    if not set(clean.lower()).issubset(set("ivxlcdm")):
        return False
    if not _ROMAN_NUM_RE.fullmatch(clean):
        return False
    if clean.lower() in _ROMAN_EXCLUDE_WORDS and not has_decor:
        return False
    return True


# Combined header with page number + title or title + page number:
# e.g. "12 | Chapter 3: Overview", "Chapter 3: Overview | 12", "12 — Deep Learning", "Deep Learning — 12"
_COMBINED_HEADER_PAGE_RE = re.compile(
    r"^(?:\d+\s*[-–—|•·~]\s*.+|.+\s*[-–—|•·~]\s*\d+)$"
)

# Running Chapter / Section / Part headings:
# e.g. "Chapter 4", "CHAPTER IV: THE FOREST", "Ch. 2 - Introduction", "Part I: Basics", "Section 3.1"
_CHAPTER_HEADER_RE = re.compile(
    r"^(?:chapter|ch\.|part|section|sec\.|book|act|scene)\s+([0-9ivxlcdm]+|[a-z]+)\b.*$",
    re.IGNORECASE,
)

# Section numbers: e.g. "1.2 Neural Networks", "3.4.1 Architecture"
_SECTION_NUMBER_RE = re.compile(r"^\d+(\.\d+)+\s+[A-Z].*")

# Front/Back matter titles commonly appearing in running headers:
_NAMED_SECTIONS_RE = re.compile(
    r"^(?:preface|prologue|epilogue|introduction|contents|table of contents|index|bibliography|appendix(?:\s+[a-z0-9]+)?|acknowledgments?|glossary)$",
    re.IGNORECASE,
)


def is_tesseract_available() -> bool:
    """Checks if tesseract-ocr is installed and accessible on the system PATH."""
    return shutil.which("tesseract") is not None


class HybridOCREngine:
    """
    Hybrid text extraction engine for LocalLibrary:
    - Column-aware reading-order sorting (prevents jumping left-to-right across columns/sidebars)
    - Diagram callout & header/footer stripping (including chapter names & page numbers)
    - Smart OCR Path (On-Demand + Disk-Cached) for scanned pages
    """

    HEADER_STRICT_RATIO = 0.080  # Top 8% of page height (strictly running headers / page numbers)
    HEADER_EXTENDED_RATIO = 0.120  # Top 12% of page height (chapter titles & headers)
    FOOTER_STRICT_RATIO = 0.920  # Bottom 8% of page height (strictly running footers / page numbers)
    FOOTER_EXTENDED_RATIO = 0.880  # Bottom 12% of page height (footers, page numbers, copyright)

    # Backward compatibility
    HEADER_CUTOFF_RATIO = HEADER_STRICT_RATIO
    FOOTER_CUTOFF_RATIO = FOOTER_STRICT_RATIO

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
        if page.parent and page.parent.is_reflowable:
            return False, False

        if cls._has_corrupted_encoding(native_blocks):
            return True, True

        meaningful_chars = sum(
            len("".join(b[4].split()))
            for b in native_blocks
            if len(b) >= 5 and isinstance(b[4], str) and (len(b) < 7 or b[6] == 0)
        )
        if meaningful_chars >= 45:
            return False, False

        page_area = max(1.0, abs(page.rect.width * page.rect.height))
        image_list = page.get_images(full=True)
        if image_list:
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
    def is_page_number_text(cls, text: str) -> bool:
        clean = " ".join(text.split()).strip()
        if not clean:
            return False
        if clean.isdigit() and len(clean) <= 5:
            return True
        if _PAGE_NUM_RE.fullmatch(clean):
            return True
        return is_roman_page_num(clean)

    @classmethod
    def is_chapter_or_header_text(cls, text: str) -> bool:
        clean = " ".join(text.split()).strip()
        if not clean or len(clean) > 120:
            return False
        if _CHAPTER_HEADER_RE.fullmatch(clean):
            return True
        if _SECTION_NUMBER_RE.fullmatch(clean):
            return True
        if _NAMED_SECTIONS_RE.fullmatch(clean):
            return True
        if _COMBINED_HEADER_PAGE_RE.fullmatch(clean):
            return True
        return False

    @classmethod
    def _is_noise_or_margin_block(
        cls,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        text: str,
        page_width: float,
        page_height: float,
        is_reflowable: bool,
        image_rects: list[pymupdf.Rect],
        skip_headers_footers: bool = True,
    ) -> bool:
        """
        Filters out noise blocks and, if skip_headers_footers is True,
        running headers/footers, chapter titles in header zone, and standalone page numbers.
        """
        clean = " ".join(text.split()).strip()
        if not clean or len(clean) < 2:
            return True

        # Tiny isolated 1-2 word diagram callouts (e.g., router ports, arrows, axis labels)
        block_h = y1 - y0
        block_w = x1 - x0
        words = clean.split()
        if len(words) <= 2 and len(clean) <= 14 and not clean.endswith((".", "!", "?", ":")):
            if page_width > 0 and block_w < page_width * 0.22:
                b_rect = pymupdf.Rect(x0, y0, x1, y1)
                for ir in image_rects:
                    expanded = pymupdf.Rect(ir.x0 - 15, ir.y0 - 15, ir.x1 + 15, ir.y1 + 15)
                    if b_rect.intersects(expanded):
                        return True

        if not skip_headers_footers:
            return False

        # --- Header / Footer / Chapter / Page number skipping logic ---

        # 1. Standalone or decorated page numbers (Arabic or Roman, e.g. "42", "Page 42", "xiv")
        if cls.is_page_number_text(clean):
            return True

        if is_reflowable or page_height <= 100:
            return False

        # 2. Strict Header Margin (top 8%): any short single/double line text
        if y1 <= page_height * cls.HEADER_STRICT_RATIO and block_h <= 32.0 and len(clean) <= 120:
            return True

        # 3. Extended Header Margin (top 12%): matches chapter/section title, combined header, or divider
        if y1 <= page_height * cls.HEADER_EXTENDED_RATIO and block_h <= 35.0 and len(clean) <= 120:
            if cls.is_chapter_or_header_text(clean):
                return True
            if any(sep in clean for sep in ("|", "•", "·", "—", "–")) and len(clean) <= 80:
                return True

        # 4. Strict Footer Margin (bottom 8%): any short single line text
        if y0 >= page_height * cls.FOOTER_STRICT_RATIO and block_h <= 32.0 and len(clean) <= 120:
            return True

        # 5. Extended Footer Margin (bottom 12%): matches page number, chapter, combined header, or URL/copyright
        if y0 >= page_height * cls.FOOTER_EXTENDED_RATIO and block_h <= 35.0 and len(clean) <= 120:
            if cls.is_chapter_or_header_text(clean):
                return True
            if any(term in clean.lower() for term in ("http://", "https://", "www.", "©", "copyright", "all rights reserved")):
                return True
            if any(sep in clean for sep in ("|", "•", "·", "—", "–")) and len(clean) <= 80:
                return True

        return False

    @classmethod
    def _sort_blocks_reading_order(
        cls, blocks: list, page_width: float, is_reflowable: bool
    ) -> list:
        """
        Column-aware reading order sorter.
        Prevents PyMuPDF's naive sort=True from jumping left-to-right-to-left
        when a page has two columns or side-by-side callout boxes.
        """
        if is_reflowable or len(blocks) <= 1 or page_width <= 0:
            return sorted(blocks, key=lambda b: (round(b[1] / 8.0), b[0]))

        mid_x = page_width * 0.5

        # Group blocks into vertical bands; if a band has strict left/right column blocks,
        # read the entire left column of that section before the right column.
        left_col = []
        right_col = []
        for b in blocks:
            x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
            bw = x1 - x0
            # Check if block is strictly confined to left half or right half
            if x1 <= mid_x + 18 and bw < page_width * 0.48:
                left_col.append(b)
            elif x0 >= mid_x - 18 and bw < page_width * 0.48:
                right_col.append(b)

        # Only treat page as multi-column if both left and right halves have multiple paragraphs
        is_two_column = len(left_col) >= 2 and len(right_col) >= 2

        if not is_two_column:
            # Single-column layout: sort primarily top-to-bottom (with 6pt tolerance for same-line items)
            return sorted(blocks, key=lambda b: (round(b[1] / 6.0), b[0]))

        # Multi-column layout: segment page by full-width spanning blocks (titles, wide tables),
        # and within each segment read Left Column top-to-bottom, then Right Column top-to-bottom.
        spanning = [
            b for b in blocks if b not in left_col and b not in right_col
        ]
        spanning.sort(key=lambda b: b[1])

        ordered = []
        last_y = -1.0

        for sp in spanning:
            sp_y = sp[1]
            l_seg = sorted(
                [b for b in left_col if last_y < b[1] <= sp_y],
                key=lambda b: (round(b[1] / 6.0), b[0]),
            )
            r_seg = sorted(
                [b for b in right_col if last_y < b[1] <= sp_y],
                key=lambda b: (round(b[1] / 6.0), b[0]),
            )
            ordered.extend(l_seg)
            ordered.extend(r_seg)
            ordered.append(sp)
            last_y = sp[1]

        l_rem = sorted(
            [b for b in left_col if b[1] > last_y],
            key=lambda b: (round(b[1] / 6.0), b[0]),
        )
        r_rem = sorted(
            [b for b in right_col if b[1] > last_y],
            key=lambda b: (round(b[1] / 6.0), b[0]),
        )
        ordered.extend(l_rem)
        ordered.extend(r_rem)
        return ordered

    @classmethod
    def extract_clean_blocks(
        cls,
        page: pymupdf.Page,
        page_idx: int,
        filepath: str = "",
        allow_ocr: bool = True,
        skip_headers_footers: bool = True,
    ) -> tuple[list, bool]:
        """
        Returns (cleaned_blocks, used_ocr_flag).
        Each block in cleaned_blocks is (x0, y0, x1, y1, text, block_no, 0).
        """
        is_reflowable = bool(page.parent and page.parent.is_reflowable)
        page_w = float(page.rect.width)
        page_h = float(page.rect.height)

        used_ocr = False
        raw_blocks = None
        if filepath:
            cached = cls.load_cached_ocr(filepath, page_idx)
            if cached and "blocks" in cached:
                raw_blocks = cached["blocks"]
                used_ocr = True

        # Do NOT enable TEXT_DEHYPHENATE here so extracted strings match page.search_for() 1:1
        flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE

        if raw_blocks is None:
            # Extract in native PDF stream order first, then apply our column-aware sorter
            native_blocks = page.get_text("blocks", sort=False, flags=flags)
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
                        ocr_blocks = page.get_text("blocks", textpage=tp, sort=False)
                        ocr_words = page.get_text("words", textpage=tp, sort=True)
                        if ocr_blocks:
                            raw_blocks = ocr_blocks
                            used_ocr = True
                            cls.save_cached_ocr(
                                filepath,
                                page_idx,
                                {
                                    "blocks": [
                                        [
                                            float(b[0]),
                                            float(b[1]),
                                            float(b[2]),
                                            float(b[3]),
                                            str(b[4]),
                                            int(b[5]),
                                            0,
                                        ]
                                        for b in ocr_blocks
                                        if len(b) >= 5 and isinstance(b[4], str)
                                    ],
                                    "words": [
                                        [
                                            float(w[0]),
                                            float(w[1]),
                                            float(w[2]),
                                            float(w[3]),
                                            str(w[4]),
                                        ]
                                        for w in ocr_words
                                        if len(w) >= 5
                                    ],
                                },
                            )
                    except Exception:
                        pass

        # Collect image rects on this page so we can ignore tiny labels inside diagrams
        image_rects: list[pymupdf.Rect] = []
        if not is_reflowable:
            for img_info in page.get_images(full=True):
                try:
                    image_rects.extend(page.get_image_rects(img_info[0]))
                except Exception:
                    pass

        filtered_blocks = []
        for b in raw_blocks:
            if len(b) < 5:
                continue
            if len(b) >= 7 and b[6] != 0:
                continue
            x0, y0, x1, y1, text = (
                float(b[0]),
                float(b[1]),
                float(b[2]),
                float(b[3]),
                str(b[4]),
            )
            if cls._is_noise_or_margin_block(
                x0,
                y0,
                x1,
                y1,
                text,
                page_w,
                page_h,
                is_reflowable,
                image_rects,
                skip_headers_footers=skip_headers_footers,
            ):
                continue

            if used_ocr:
                text = re.sub(r"\s+\|\s+", " ", text)

            filtered_blocks.append(
                (x0, y0, x1, y1, text, int(b[5]) if len(b) > 5 else 0, 0)
            )

        sorted_blocks = cls._sort_blocks_reading_order(
            filtered_blocks, page_w, is_reflowable
        )
        return sorted_blocks, used_ocr

    @classmethod
    def search_cached_ocr_rects(
        cls, filepath: str, page_idx: int, query_text: str
    ) -> list[pymupdf.Rect]:
        cached = cls.load_cached_ocr(filepath, page_idx)
        if not cached or "words" not in cached:
            return []

        words = cached["words"]
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

        match_len = min(n_q, 6)
        needle = q_tokens[:match_len]

        for i in range(n_p - match_len + 1):
            if page_tokens[i : i + match_len] == needle:
                end_i = min(n_p, i + n_q)
                matched_words = words[i:end_i]
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
            if abs(((r.y0 + r.y1) * 0.5) - ((cur.y0 + cur.y1) * 0.5)) <= 8.0:
                cur |= r
            else:
                lines.append(cur)
                cur = r
        lines.append(cur)
        return lines