import os
import re
import json
import hashlib
import zipfile
from pathlib import Path
from datetime import datetime
import pymupdf

from app.config import LIBRARY_DB_FILE, COVERS_CACHE_DIR, DEFAULT_EPUB_FONTSIZE
from app.core.epub_loader import open_clean_document

from app.config import (
    LIBRARY_DB_FILE,
    SETTINGS_FILE,
    COVERS_CACHE_DIR,
    DEFAULT_EPUB_FONTSIZE,
    TTS_VOICES,
)

SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".fb2", ".mobi", ".cbz", ".xps", ".txt"}


class LibraryStore:
    """Manages persistent book metadata, high-res covers, reading progress, reading time, and annotations."""

    def __init__(self):
        self.db_file = LIBRARY_DB_FILE
        self.settings_file = SETTINGS_FILE
        self.covers_dir = COVERS_CACHE_DIR
        self.books = self._load()
        self.settings = self._load_settings()

    def _load(self) -> dict:
        if self.db_file.exists():
            try:
                return json.loads(self.db_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save(self):
        self.db_file.write_text(json.dumps(self.books, indent=2), encoding="utf-8")

    def _load_settings(self) -> dict:
        defaults = {
            "tts_voice": TTS_VOICES[0],
            "tts_speed": 1.0,
            "tts_pitch": 0,
            "tts_auto_scroll": True,
            "theme_name": "Smart Dark (Mocha)",
            "view_mode": "Continuous Scroll",
        }
        if self.settings_file.exists():
            try:
                loaded = json.loads(self.settings_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    defaults.update(loaded)
            except Exception:
                pass
        return defaults

    def save_settings(self, **kwargs):
        self.settings.update(kwargs)
        try:
            self.settings_file.write_text(
                json.dumps(self.settings, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    @staticmethod
    def book_id(filepath: str) -> str:
        return hashlib.sha1(os.path.abspath(filepath).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _extract_epub_cover_bytes(filepath: str) -> bytes | None:
        """Extracts raw cover image bytes directly from an EPUB archive so covers never have white margins."""
        if not zipfile.is_zipfile(filepath):
            return None
        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                names = zf.namelist()
                img_exts = (".jpg", ".jpeg", ".png", ".webp")
                image_files = [n for n in names if n.lower().endswith(img_exts)]
                if not image_files:
                    return None

                # 1. Check OPF manifest for explicit cover image reference
                opf_files = [n for n in names if n.lower().endswith(".opf")]
                for opf_name in opf_files:
                    opf_text = zf.read(opf_name).decode("utf-8", errors="ignore")
                    opf_dir = os.path.dirname(opf_name)
                    # Look for <item ... properties="cover-image" href="..."/> or id="cover..."
                    for match in re.finditer(r"<item\b[^>]+>", opf_text, re.IGNORECASE):
                        tag = match.group(0)
                        if "cover" in tag.lower() and any(ext in tag.lower() for ext in img_exts):
                            href_m = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
                            if href_m:
                                candidate = os.path.normpath(os.path.join(opf_dir, href_m.group(1))).replace("\\", "/")
                                if candidate in names:
                                    return zf.read(candidate)

                # 2. Fallback: any image with 'cover' in its filename
                for img_name in image_files:
                    if "cover" in Path(img_name).stem.lower():
                        return zf.read(img_name)

                # 3. Fallback: largest image among the first 5 images in the archive
                first_imgs = image_files[:5]
                best_name = max(first_imgs, key=lambda n: zf.getinfo(n).file_size)
                if zf.getinfo(best_name).file_size > 4096:
                    return zf.read(best_name)
        except Exception:
            pass
        return None

    def _generate_cover(self, filepath: str, doc: pymupdf.Document, cover_path: Path):
        ext = Path(filepath).suffix.lower()

        # 1. For EPUBs, extract native cover image directly from the zip archive
        if ext == ".epub":
            raw_bytes = self._extract_epub_cover_bytes(filepath)
            if raw_bytes:
                try:
                    pix = pymupdf.Pixmap(raw_bytes)
                    if pix.n - pix.alpha > 3:  # Convert CMYK to RGB if needed
                        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                    pix.save(str(cover_path))
                    return
                except Exception:
                    pass

        # 2. Check if Page 0 has an embedded full-cover image we can extract cleanly
        if len(doc) > 0:
            try:
                page = doc.load_page(0)
                imgs = page.get_images(full=True)
                if ext in (".epub", ".fb2", ".mobi") and imgs:
                    xref = imgs[0][0]
                    pix = pymupdf.Pixmap(doc, xref)
                    if pix.width >= 120 and pix.height >= 150:
                        if pix.n - pix.alpha > 3:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        pix.save(str(cover_path))
                        return

                # 3. Standard PDF / fallback page render
                pix = page.get_pixmap(matrix=pymupdf.Matrix(0.75, 0.75), alpha=False)
                pix.save(str(cover_path))
            except Exception:
                pass

    def add_or_update_book(
        self, filepath: str, open_doc: pymupdf.Document | None = None
    ) -> dict | None:
        filepath = os.path.abspath(filepath)
        if not os.path.exists(filepath):
            return None
        if Path(filepath).suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None

        bid = self.book_id(filepath)
        existing = self.books.get(bid, {})
        cover_path = self.covers_dir / f"{bid}.png"

        should_close = False
        doc = open_doc
        if doc is None:
            try:
                doc = open_clean_document(filepath, fontsize=DEFAULT_EPUB_FONTSIZE)
                should_close = True
            except Exception:
                return None

        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip() or Path(filepath).stem
        author = (meta.get("author") or "").strip() or "Unknown Author"
        publisher = (meta.get("producer") or meta.get("creator") or "").strip()
        total_pages = len(doc)

        # Regenerate cover if missing or if an EPUB hasn't been upgraded to v2 native cover extraction
        needs_cover_upgrade = (
            Path(filepath).suffix.lower() == ".epub" and not existing.get("cover_v2", False)
        )
        if (not cover_path.exists() or needs_cover_upgrade) and total_pages > 0:
            self._generate_cover(filepath, doc, cover_path)

        if should_close:
            doc.close()

        record = {
            "id": bid,
            "path": filepath,
            "title": title,
            "author": author,
            "publisher": publisher or existing.get("publisher", ""),
            "format": Path(filepath).suffix.upper().lstrip("."),
            "cover": str(cover_path),
            "cover_v2": True,
            "current_page": existing.get("current_page", 0),
            "total_pages": max(1, total_pages),
            "word_count": existing.get("word_count", 0),
            "reading_seconds": existing.get("reading_seconds", 0),
            "bookmarks": existing.get("bookmarks", []),
            "highlights": existing.get("highlights", []),
            "tts_state": existing.get(
            "tts_state",
            {
                "page": 0,
                "para_idx": 0,
                "sent_idx": 0,
                "voice": self.settings.get("tts_voice", TTS_VOICES[0]),
                "speed": self.settings.get("tts_speed", 1.0),
                "pitch": self.settings.get("tts_pitch", 0),
            },
            ),
            "last_opened": existing.get(
                "last_opened", datetime.now().isoformat(timespec="minutes")
            ),
        }
        self.books[bid] = record
        self.save()
        return record

    def refresh_epub_covers_once(self):
        """Upgrades any existing EPUB book cards that still have old v1 white-box covers."""
        updated = False
        for bid, book in list(self.books.items()):
            if book.get("format") == "EPUB" and not book.get("cover_v2", False):
                path = book.get("path", "")
                if os.path.exists(path):
                    raw_bytes = self._extract_epub_cover_bytes(path)
                    if raw_bytes:
                        try:
                            cover_path = Path(book.get("cover", self.covers_dir / f"{bid}.png"))
                            pix = pymupdf.Pixmap(raw_bytes)
                            if pix.n - pix.alpha > 3:
                                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                            pix.save(str(cover_path))
                            book["cover_v2"] = True
                            updated = True
                        except Exception:
                            pass
        if updated:
            self.save()

    def add_reading_time(self, filepath: str, seconds: int):
        bid = self.book_id(filepath)
        if bid in self.books:
            self.books[bid]["reading_seconds"] = (
                int(self.books[bid].get("reading_seconds", 0)) + seconds
            )
            self.save()

    def touch_last_opened(self, filepath: str):
        bid = self.book_id(filepath)
        if bid in self.books:
            self.books[bid]["last_opened"] = datetime.now().isoformat(timespec="minutes")
            self.save()

    def remove_book(self, filepath_or_id: str):
        bid = (
            filepath_or_id
            if filepath_or_id in self.books
            else self.book_id(filepath_or_id)
        )
        record = self.books.pop(bid, None)
        if record:
            cover = record.get("cover")
            if cover and os.path.exists(cover):
                try:
                    os.remove(cover)
                except OSError:
                    pass
            self.save()