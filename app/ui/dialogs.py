import os
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QTextBrowser, QTextEdit, QComboBox, QPushButton, QFrame, QGridLayout, QProgressBar
)

from app.config import HIGHLIGHT_COLORS
from app.core.lookup import LookupWorker

TRANSLATION_LANGUAGES = [
    ("English", "en"),
    ("Urdu (اردو)", "ur"),
    ("Arabic (العربية)", "ar"),
    ("Spanish (Español)", "es"),
    ("French (Français)", "fr"),
    ("German (Deutsch)", "de"),
    ("Chinese (中文)", "zh-CN"),
    ("Russian (Русский)", "ru"),
    ("Turkish (Türkçe)", "tr"),
    ("Hindi (हिन्दी)", "hi"),
]


class LookupPopupDialog(QDialog):
    """Foliate-style Popup Dictionary, Wikipedia & Translation Inspector."""

    def __init__(self, selected_text: str, parent=None):
        super().__init__(parent)
        self.selected_text = selected_text.strip()
        self._workers: list[LookupWorker] = []

        self.setWindowTitle(f"Lookup — “{self.selected_text[:36]}”")
        self.resize(540, 430)
        self.setStyleSheet("""
            QDialog { background-color: #181825; color: #CDD6F4; }
            QTextBrowser {
                background-color: #11111B;
                color: #CDD6F4;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        phrase_lbl = QLabel(
            f"“{self.selected_text[:90]}{'...' if len(self.selected_text) > 90 else ''}”"
        )
        phrase_lbl.setWordWrap(True)
        phrase_lbl.setStyleSheet("color: #89B4FA; font-size: 14px; font-weight: bold;")
        layout.addWidget(phrase_lbl)

        self.tabs = QTabWidget()

        # Tab 1: Dictionary
        self.dict_browser = QTextBrowser()
        self.dict_browser.setOpenExternalLinks(True)
        self.dict_browser.setHtml("<p style='color:#A6ADC8;'>Looking up dictionary definition...</p>")
        self.tabs.addTab(self.dict_browser, "📖 Dictionary")

        # Tab 2: Wikipedia
        self.wiki_browser = QTextBrowser()
        self.wiki_browser.setOpenExternalLinks(True)
        self.wiki_browser.setHtml("<p style='color:#A6ADC8;'>Fetching Wikipedia summary...</p>")
        self.tabs.addTab(self.wiki_browser, "🌐 Wikipedia")

        # Tab 3: Translation
        trans_tab = QFrame()
        trans_layout = QVBoxLayout(trans_tab)
        trans_layout.setContentsMargins(6, 6, 6, 6)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Translate to:"))
        self.combo_lang = QComboBox()
        for label, code in TRANSLATION_LANGUAGES:
            self.combo_lang.addItem(label, code)
        self.combo_lang.setCurrentIndex(1)  # Default: Urdu
        self.combo_lang.currentIndexChanged.connect(self._retranslate)
        lang_row.addWidget(self.combo_lang)
        lang_row.addStretch(1)
        trans_layout.addLayout(lang_row)

        self.trans_browser = QTextBrowser()
        self.trans_browser.setHtml("<p style='color:#A6ADC8;'>Translating...</p>")
        trans_layout.addWidget(self.trans_browser, stretch=1)
        self.tabs.addTab(trans_tab, "🔤 Translate")

        if len(self.selected_text.split()) > 3:
            self.tabs.setCurrentIndex(2)

        layout.addWidget(self.tabs, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self._start_lookup(mode="all")

    def _start_lookup(self, mode: str = "all"):
        target_code = self.combo_lang.currentData() or "en"
        worker = LookupWorker(self.selected_text, target_lang=target_code, mode=mode)
        if mode == "all":
            worker.dictionary_ready.connect(self.dict_browser.setHtml)
            worker.wikipedia_ready.connect(self.wiki_browser.setHtml)
        worker.translation_ready.connect(self.trans_browser.setHtml)
        self._workers.append(worker)
        worker.start()

    def _retranslate(self):
        self.trans_browser.setHtml("<p style='color:#A6ADC8;'>Translating...</p>")
        self._start_lookup(mode="translate_only")

    def closeEvent(self, event):
        for w in self._workers:
            if w.isRunning():
                w.quit()
                w.wait(200)
        super().closeEvent(event)


class BookInfoDialog(QDialog):
    """Reading Statistics & Book Metadata Inspector Modal."""

    def __init__(self, doc, book_record: dict, store, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Book Info & Reading Statistics — {book_record.get('title', 'Book')}")
        self.resize(620, 480)
        self.setStyleSheet("""
            QDialog { background-color: #181825; color: #CDD6F4; }
            QFrame#StatCard {
                background-color: #1E1E2E;
                border: 1px solid #313244;
                border-radius: 8px;
                padding: 10px;
            }
        """)

        # Compute word count once and cache it in book_record
        total_pages = max(1, len(doc))
        cur_page = book_record.get("current_page", 0)
        total_words = book_record.get("word_count", 0)
        if total_words <= 0:
            # Sample or sum words across pages
            if total_pages <= 150:
                total_words = sum(len(doc.load_page(i).get_text("words")) for i in range(total_pages))
            else:
                sample_indices = list(range(0, total_pages, max(1, total_pages // 50)))
                sample_words = sum(len(doc.load_page(i).get_text("words")) for i in sample_indices)
                total_words = int((sample_words / len(sample_indices)) * total_pages)
            book_record["word_count"] = total_words
            store.save()

        avg_words_per_page = int(total_words / total_pages)
        pages_left = max(0, total_pages - (cur_page + 1))
        words_left = pages_left * avg_words_per_page
        # Average adult reading speed: 220 words per minute
        est_mins_left = max(1, int(round(words_left / 220.0))) if pages_left > 0 else 0
        est_hours, est_mins = divmod(est_mins_left, 60)
        est_time_str = (
            "Completed!"
            if pages_left == 0
            else (f"{est_hours}h {est_mins}m" if est_hours > 0 else f"{est_mins} min")
        )

        read_secs = int(book_record.get("reading_seconds", 0))
        r_hours, rem = divmod(read_secs, 3600)
        r_mins, _ = divmod(rem, 60)
        spent_str = f"{r_hours}h {r_mins}m" if r_hours > 0 else f"{max(1, r_mins)} min" if read_secs >= 30 else "< 1 min"

        pct = int(((cur_page + 1) / total_pages) * 100)
        file_size_mb = (
            os.path.getsize(book_record["path"]) / (1024 * 1024)
            if os.path.exists(book_record.get("path", ""))
            else 0.0
        )
        meta = doc.metadata or {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        # ---- Top Section: Cover + Primary Metadata ----
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        cover_lbl = QLabel()
        cover_lbl.setFixedSize(120, 165)
        cover_lbl.setAlignment(Qt.AlignCenter)
        cover_lbl.setStyleSheet("background-color: #11111B; border: 1px solid #313244; border-radius: 6px;")
        if os.path.exists(book_record.get("cover", "")):
            pix = QPixmap(book_record["cover"]).scaled(116, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            cover_lbl.setPixmap(pix)
        else:
            cover_lbl.setText(book_record.get("format", "BOOK"))
        top_row.addWidget(cover_lbl)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(4)

        title_lbl = QLabel(book_record.get("title", "Untitled"))
        title_lbl.setFont(QFont("Sans Serif", 14, QFont.Bold))
        title_lbl.setWordWrap(True)
        meta_col.addWidget(title_lbl)

        author_lbl = QLabel(f"Author: {book_record.get('author', 'Unknown Author')}")
        author_lbl.setStyleSheet("color: #89B4FA; font-size: 13px;")
        meta_col.addWidget(author_lbl)

        pub = meta.get("producer") or meta.get("creator") or book_record.get("publisher") or "—"
        for line in [
            f"Format: {book_record.get('format', '—')}  •  Size: {file_size_mb:.2f} MB",
            f"Publisher / Producer: {pub}",
            f"Subject: {meta.get('subject') or '—'}",
            f"File: {Path(book_record.get('path', '')).name}",
        ]:
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #A6ADC8; font-size: 12px;")
            meta_col.addWidget(lbl)

        meta_col.addStretch(1)
        top_row.addLayout(meta_col, stretch=1)
        root.addLayout(top_row)

        # ---- Progress Bar ----
        prog_bar = QProgressBar()
        prog_bar.setRange(0, 100)
        prog_bar.setValue(pct)
        prog_bar.setFormat(f"  Reading Progress: {pct}% (Page {cur_page + 1} of {total_pages})")
        prog_bar.setFixedHeight(22)
        prog_bar.setStyleSheet("""
            QProgressBar {
                background-color: #11111B; border: 1px solid #313244;
                border-radius: 6px; text-align: center; color: #CDD6F4; font-weight: bold;
            }
            QProgressBar::chunk { background-color: #266448; border-radius: 5px; }
        """)
        root.addWidget(prog_bar)

        # ---- Reading Statistics Grid ----
        grid = QGridLayout()
        grid.setSpacing(10)

        stats = [
            ("Estimated Time Left", est_time_str, "at ~220 WPM"),
            ("Time Spent Reading", spent_str, f"Last opened: {book_record.get('last_opened', '—')}"),
            ("Total Word Count", f"{total_words:,} words", f"~{avg_words_per_page} words / page"),
            (
                "Saved Annotations",
                f"{len(book_record.get('highlights', []))} Highlights",
                f"{len(book_record.get('bookmarks', []))} Bookmarks",
            ),
        ]

        for idx, (heading, big_val, sub_val) in enumerate(stats):
            card = QFrame()
            card.setObjectName("StatCard")
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(10, 8, 10, 8)
            c_layout.setSpacing(2)

            h_lbl = QLabel(heading)
            h_lbl.setStyleSheet("color: #A6ADC8; font-size: 11px;")
            c_layout.addWidget(h_lbl)

            v_lbl = QLabel(big_val)
            v_lbl.setStyleSheet("color: #A6E3A1; font-size: 16px; font-weight: bold;")
            c_layout.addWidget(v_lbl)

            s_lbl = QLabel(sub_val)
            s_lbl.setStyleSheet("color: #9399B2; font-size: 11px;")
            c_layout.addWidget(s_lbl)

            grid.addWidget(card, idx // 2, idx % 2)

        root.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

class AnnotationNoteDialog(QDialog):
    """Modal dialog to add or edit a custom note and highlight color for a passage."""

    def __init__(
        self,
        passage_text: str,
        initial_note: str = "",
        initial_color: str = "yellow",
        page_num: int = 1,
        parent=None,
    ):
        super().__init__(parent)
        self.selected_color = initial_color if initial_color in HIGHLIGHT_COLORS else "yellow"
        self.note_text = initial_note
        self.delete_requested = False
        self._swatch_buttons: dict[str, QPushButton] = {}

        self.setWindowTitle(f"Annotation Note — Page {page_num}")
        self.resize(500, 360)
        self.setStyleSheet("""
            QDialog { background-color: #181825; color: #CDD6F4; }
            QTextEdit {
                background-color: #11111B;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }
            QTextEdit:focus { border: 1px solid #89B4FA; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Quoted passage preview
        quote_frame = QFrame()
        quote_frame.setStyleSheet(
            "background-color: #1E1E2E; border-left: 3px solid #89B4FA; border-radius: 4px; padding: 6px;"
        )
        q_layout = QVBoxLayout(quote_frame)
        q_layout.setContentsMargins(8, 6, 8, 6)
        preview = passage_text if len(passage_text) <= 220 else passage_text[:217] + "..."
        quote_lbl = QLabel(f"“{preview}”")
        quote_lbl.setWordWrap(True)
        quote_lbl.setStyleSheet("color: #A6ADC8; font-style: italic; font-size: 12px;")
        q_layout.addWidget(quote_lbl)
        layout.addWidget(quote_frame)

        # Color swatch selector row
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Highlight Color:"))
        for key, info in HIGHLIGHT_COLORS.items():
            btn = QPushButton("")
            btn.setFixedSize(24, 24)
            btn.setToolTip(info["label"])
            btn.clicked.connect(lambda _=False, k=key: self._select_color(k))
            self._swatch_buttons[key] = btn
            color_row.addWidget(btn)
        color_row.addStretch(1)
        layout.addLayout(color_row)
        self._update_swatch_styles()

        # Note editor
        layout.addWidget(QLabel("Your Note (supports Markdown):"))
        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("Write your thoughts, summary, or study notes for this highlight...")
        self.note_edit.setPlainText(initial_note)
        layout.addWidget(self.note_edit, stretch=1)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_delete = QPushButton("Delete Highlight")
        btn_delete.setStyleSheet("QPushButton { background-color: #452736; color: #F38BA8; border-color: #F38BA8; }")
        btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(btn_delete)

        btn_row.addStretch(1)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("Save Note")
        btn_save.setStyleSheet("QPushButton { background-color: #89B4FA; color: #11111B; font-weight: bold; }")
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)
        self.note_edit.setFocus()

    def _select_color(self, color_key: str):
        self.selected_color = color_key
        self._update_swatch_styles()

    def _update_swatch_styles(self):
        for key, btn in self._swatch_buttons.items():
            hex_col = HIGHLIGHT_COLORS[key]["hex"]
            border = "3px solid #FFFFFF" if key == self.selected_color else "2px solid #11111B"
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {hex_col}; border: {border}; border-radius: 12px; padding: 0px; }}"
            )

    def _on_save(self):
        self.note_text = self.note_edit.toPlainText().strip()
        self.accept()

    def _on_delete(self):
        self.delete_requested = True
        self.accept()


def export_book_notes_to_markdown(book: dict, dest_path: str):
    """Generates a clean, structured Markdown (.md) file with all book highlights, notes, and bookmarks."""
    title = book.get("title", "Untitled Book")
    author = book.get("author", "Unknown Author")
    fmt = book.get("format", "BOOK")
    total_pages = book.get("total_pages", 1)
    cur_page = book.get("current_page", 0) + 1
    pct = int((cur_page / max(1, total_pages)) * 100)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# {title}",
        "",
        f"- **Author:** {author}",
        f"- **Format:** {fmt}",
        f"- **Reading Progress:** {pct}% (Page {cur_page} of {total_pages})",
        f"- **Exported on:** {now_str}",
        "",
        "---",
        "",
    ]

    bookmarks = sorted(book.get("bookmarks", []))
    if bookmarks:
        lines.append("## 🔖 Bookmarks")
        lines.append("")
        for p in bookmarks:
            lines.append(f"- Page {p + 1}")
        lines.append("")
        lines.append("---")
        lines.append("")

    highlights = sorted(book.get("highlights", []), key=lambda h: h.get("page", 0))
    lines.append(f"## 📝 Highlights & Notes ({len(highlights)})")
    lines.append("")

    if not highlights:
        lines.append("_No highlights recorded yet._")
    else:
        for h in highlights:
            page_no = h.get("page", 0) + 1
            color_label = HIGHLIGHT_COLORS.get(h.get("color", "yellow"), HIGHLIGHT_COLORS["yellow"])["label"]
            quote = " ".join(h.get("text", "").split())
            note = (h.get("note") or "").strip()

            lines.append(f"### Page {page_no} ({color_label})")
            lines.append(f"> “{quote}”")
            if note:
                lines.append("")
                lines.append(f"**Note:** {note}")
            lines.append("")

    Path(dest_path).write_text("\n".join(lines), encoding="utf-8")