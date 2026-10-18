import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
import pymupdf

from app.config import LIBRARY_DB_FILE, COVERS_CACHE_DIR, DEFAULT_EPUB_FONTSIZE
from app.core.epub_loader import open_clean_document

SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".fb2", ".mobi", ".cbz", ".xps", ".txt"}


class LibraryStore:
    """Manages persistent book metadata, reading progress, reading time, TTS memory, bookmarks, and highlights."""

    def __init__(self):
        self.db_file = LIBRARY_DB_FILE
        self.covers_dir = COVERS_CACHE_DIR
        self.books = self._load()

    def _load(self) -> dict:
        if self.db_file.exists():
            try:
                return json.loads(self.db_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save(self):
        self.db_file.write_text(json.dumps(self.books, indent=2), encoding="utf-8")

    @staticmethod
    def book_id(filepath: str) -> str:
        return hashlib.sha1(os.path.abspath(filepath).encode("utf-8")).hexdigest()[:16]

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

        # Fast path: if already in database, cover exists, and open_doc is provided
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

        if not cover_path.exists() and total_pages > 0:
            try:
                page = doc.load_page(0)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(0.6, 0.6), alpha=False)
                pix.save(str(cover_path))
            except Exception:
                pass

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
            "current_page": existing.get("current_page", 0),
            "total_pages": max(1, total_pages),
            "word_count": existing.get("word_count", 0),
            "reading_seconds": existing.get("reading_seconds", 0),
            "bookmarks": existing.get("bookmarks", []),
            "highlights": existing.get("highlights", []),
            "tts_state": existing.get(
                "tts_state",
                {"page": 0, "para_idx": 0, "sent_idx": 0, "speed": 1.0, "pitch": 0},
            ),
            "last_opened": existing.get(
                "last_opened", datetime.now().isoformat(timespec="minutes")
            ),
        }
        self.books[bid] = record
        self.save()
        return record

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