import os
import re
import json
import hashlib
import zipfile
import shutil
from pathlib import Path
from datetime import datetime
import pymupdf

from app.config import (
    LIBRARY_DB_FILE,
    SETTINGS_FILE,
    COVERS_CACHE_DIR,
    DEFAULT_EPUB_FONTSIZE,
    TTS_VOICES,
)
from app.core.epub_loader import open_clean_document

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
            "folders": [],
            "library_root": "",
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
            "category": existing.get("category", ""),
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
    
# ---- PHYSICAL FOLDER / CATEGORY MANAGEMENT ----

    @staticmethod
    def _sanitize_folder_name(name: str) -> str:
        """Strips illegal filesystem characters from user-created folder names."""
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().strip(".")
        return cleaned

    def get_library_root(self) -> Path:
        """
        Returns the base directory on disk where category subfolders are created.
        If not explicitly set by the user, infers the common parent folder of existing books
        or falls back to ~/Documents/LocalLibrary.
        """
        saved_root = self.settings.get("library_root", "").strip()
        if saved_root and os.path.isdir(saved_root):
            return Path(saved_root).resolve()

        # Infer from existing books in the library
        parent_counts: dict[str, int] = {}
        for _book_id, b_info in self.books.items():
            if not isinstance(b_info, dict):
                continue
            b_path = b_info.get("path") or b_info.get("filepath") or ""
            if not b_path or not os.path.exists(b_path):
                continue
            p = Path(b_path).resolve().parent
            # If the book is already inside a known category subfolder, step up one level
            cat = b_info.get("category", "").strip()
            if cat and p.name == cat:
                p = p.parent
            if p.exists():
                parent_counts[str(p)] = parent_counts.get(str(p), 0) + 1

        if parent_counts:
            best_root = max(parent_counts.items(), key=lambda x: x[1])[0]
            self.save_settings(library_root=best_root)
            return Path(best_root)

        default_root = Path.home() / "Documents" / "LocalLibrary"
        default_root.mkdir(parents=True, exist_ok=True)
        self.save_settings(library_root=str(default_root))
        return default_root

    def set_library_root(self, new_root_dir: str) -> Path:
        """Updates the base directory where category folders live and moves existing category folders."""
        new_root = Path(new_root_dir).resolve()
        new_root.mkdir(parents=True, exist_ok=True)
        self.save_settings(library_root=str(new_root))
        return new_root

    def get_folders(self) -> list[str]:
        """Returns sorted list of user-created virtual category/folder names."""
        folders = set(self.settings.get("folders", []))
        for b in self.books.values():
            if isinstance(b, dict):
                cat = b.get("category", "").strip()
                if cat:
                    folders.add(cat)
        sorted_folders = sorted(folders, key=lambda s: s.lower())
        if sorted_folders != self.settings.get("folders"):
            self.save_settings(folders=sorted_folders)
        return sorted_folders
    
    def get_folder_books_map(self) -> dict[str, list[str]]:
        """Returns a dictionary mapping each virtual category name to its list of book file paths."""
        mapping: dict[str, list[str]] = {f: [] for f in self.get_folders()}
        for k, b in self.books.items():
            if not isinstance(b, dict):
                continue
            cat = b.get("category", "").strip()
            path = b.get("path") or b.get("filepath") or str(k)
            if cat:
                mapping.setdefault(cat, []).append(path)
        return mapping

    def create_folder(self, folder_name: str) -> tuple[bool, str]:
        """Creates a new virtual category/folder in settings.json."""
        clean_name = self._sanitize_folder_name(" ".join(folder_name.split()))
        clean_name = " ".join(clean_name.split()).strip()
        if not clean_name:
            return False, "Category name cannot be empty."

        existing = self.get_folders()
        if any(f.lower() == clean_name.lower() for f in existing):
            return False, f"Category '{clean_name}' already exists."

        existing.append(clean_name)
        existing.sort(key=lambda s: s.lower())
        self.save_settings(folders=existing)
        return True, clean_name

    def _safe_move_file(self, src: Path, dst_dir: Path) -> Path:
        """Moves src into dst_dir on the filesystem, avoiding filename collisions."""
        dst_dir.mkdir(parents=True, exist_ok=True)
        candidate = dst_dir / src.name
        if candidate.resolve() == src.resolve():
            return src.resolve()

        stem = src.stem
        suffix = src.suffix
        counter = 1
        while candidate.exists():
            candidate = dst_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        shutil.move(str(src), str(candidate))
        return candidate.resolve()

    def _resolve_book_key_and_entry(self, identifier: str) -> tuple[str | None, dict | None]:
        """Finds (dict_key, book_dict) in self.books regardless of how identifier is formatted."""
        if not identifier:
            return None, None

        if identifier in self.books:
            return identifier, self.books[identifier]

        norm_id = os.path.abspath(identifier)
        if norm_id in self.books:
            return norm_id, self.books[norm_id]

        target_name = os.path.basename(identifier)
        for k, b_info in self.books.items():
            if not isinstance(b_info, dict):
                continue
            b_path = b_info.get("path") or b_info.get("filepath") or str(k)
            if (
                b_path == identifier
                or os.path.abspath(b_path) == norm_id
                or (target_name and os.path.basename(b_path) == target_name)
            ):
                return k, b_info

        return None, None

    def move_book_to_folder(self, book_path: str, folder_name: str) -> tuple[bool, str]:
        """
        Assigns a book to a virtual category/folder in library.json
        WITHOUT moving or touching the file on the filesystem.
        """
        key, book_entry = self._resolve_book_key_and_entry(book_path)
        if not book_entry or key is None:
            return False, "Book not found in library database."

        clean_folder = (
            self._sanitize_folder_name(" ".join(folder_name.split()))
            if folder_name
            else ""
        )
        clean_folder = " ".join(clean_folder.split()).strip()

        if clean_folder:
            folders = self.get_folders()
            if clean_folder not in folders:
                folders.append(clean_folder)
                folders.sort(key=lambda s: s.lower())
                self.save_settings(folders=folders)

        book_entry["category"] = clean_folder
        self.save()
        return True, clean_folder

    def rename_folder(self, old_name: str, new_name: str) -> tuple[bool, str]:
        """Renames a virtual category across settings.json and all associated books in library.json."""
        clean_new = self._sanitize_folder_name(" ".join(new_name.split()))
        clean_new = " ".join(clean_new.split()).strip()
        if not clean_new:
            return False, "New category name cannot be empty."
        if old_name == clean_new:
            return True, clean_new

        folders = self.get_folders()
        if any(f.lower() == clean_new.lower() and f != old_name for f in folders):
            return False, f"A category named '{clean_new}' already exists."

        for b_info in self.books.values():
            if isinstance(b_info, dict) and b_info.get("category", "") == old_name:
                b_info["category"] = clean_new

        updated_folders = [clean_new if f == old_name else f for f in folders]
        updated_folders = sorted(set(updated_folders), key=lambda s: s.lower())
        self.save_settings(folders=updated_folders)
        self.save()
        return True, clean_new

    def delete_folder(self, folder_name: str) -> tuple[bool, str]:
        """
        Deletes a virtual category and resets its books back to Uncategorized ("")
        without touching any files on disk.
        """
        count = 0
        for b_info in self.books.values():
            if isinstance(b_info, dict) and b_info.get("category", "") == folder_name:
                b_info["category"] = ""
                count += 1

        updated_folders = [f for f in self.get_folders() if f != folder_name]
        self.save_settings(folders=updated_folders)
        self.save()
        return True, f"Deleted category '{folder_name}' ({count} book(s) returned to Uncategorized)."