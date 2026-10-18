from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QGridLayout, QLineEdit, QComboBox, QFrame
)

from app.ui.widgets import BookCardWidget


class LibraryView(QWidget):
    """Homepage displaying a searchable, filterable, sortable, and responsive book card grid."""

    book_selected = Signal(str)
    book_remove_requested = Signal(str)

    CARD_WIDTH = 215
    GRID_SPACING = 18

    def __init__(self):
        super().__init__()
        self._all_books: dict = {}
        self._cards: list[BookCardWidget] = []
        self._current_cols: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # ---- Top Header Row ----
        header_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        header_lbl = QLabel("Your Reading Library")
        header_lbl.setFont(QFont("Sans Serif", 18, QFont.Bold))
        title_col.addWidget(header_lbl)

        sub_lbl = QLabel(
            "Supports PDF, EPUB, FB2, MOBI, CBZ, and TXT with automatic progress tracking."
        )
        sub_lbl.setStyleSheet("color: #A6ADC8; font-size: 12px;")
        title_col.addWidget(sub_lbl)

        header_row.addLayout(title_col)
        header_row.addStretch(1)

        self.lbl_count = QLabel("0 books")
        self.lbl_count.setStyleSheet(
            "color: #89B4FA; font-size: 13px; font-weight: bold; padding-right: 4px;"
        )
        header_row.addWidget(self.lbl_count, alignment=Qt.AlignBottom)
        layout.addLayout(header_row)

        # ---- Search, Filter & Sort Bar ----
        filter_bar = QFrame()
        filter_bar.setObjectName("FilterBar")
        filter_bar.setStyleSheet("""
            QFrame#FilterBar {
                background-color: #181825;
                border: 1px solid #313244;
                border-radius: 8px;
                padding: 6px 10px;
            }
            QLineEdit {
                background-color: #11111B;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #89B4FA;
            }
        """)
        fb_layout = QHBoxLayout(filter_bar)
        fb_layout.setContentsMargins(6, 4, 6, 4)
        fb_layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by title or author...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(240)
        self.search_input.textChanged.connect(self._apply_filters_and_rebuild)
        fb_layout.addWidget(self.search_input, stretch=1)

        fb_layout.addWidget(QLabel("Format:"))
        self.combo_format = QComboBox()
        self.combo_format.addItems(["All Formats", "PDF", "EPUB", "FB2", "MOBI", "CBZ", "TXT"])
        self.combo_format.currentTextChanged.connect(self._apply_filters_and_rebuild)
        fb_layout.addWidget(self.combo_format)

        fb_layout.addWidget(QLabel("Status:"))
        self.combo_status = QComboBox()
        self.combo_status.addItems(["All Status", "Unread", "Reading", "Completed"])
        self.combo_status.currentTextChanged.connect(self._apply_filters_and_rebuild)
        fb_layout.addWidget(self.combo_status)

        fb_layout.addWidget(QLabel("Sort by:"))
        self.combo_sort = QComboBox()
        self.combo_sort.addItems([
            "Recently Opened",
            "Title (A–Z)",
            "Author (A–Z)",
            "Progress (High → Low)",
        ])
        self.combo_sort.currentTextChanged.connect(self._apply_filters_and_rebuild)
        fb_layout.addWidget(self.combo_sort)

        layout.addWidget(filter_bar)

        # ---- Empty State Label ----
        self.empty_lbl = QLabel("No matching books found.")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setStyleSheet("color: #6C7086; font-size: 15px; padding: 40px;")
        self.empty_lbl.hide()
        layout.addWidget(self.empty_lbl)

        # ---- Responsive Scroll Grid ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)

        self.grid_container = QWidget()
        self.grid = QGridLayout(self.grid_container)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(self.GRID_SPACING)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.grid_container)
        layout.addWidget(self.scroll, stretch=1)

    def populate(self, books_dict: dict, _window_width: int = 0):
        """Stores the library dictionary and rebuilds cards using active search/filter/sort criteria."""
        self._all_books = dict(books_dict)
        self._apply_filters_and_rebuild()

    @staticmethod
    def _book_progress_pct(book: dict) -> int:
        cur = book.get("current_page", 0) + 1
        tot = max(1, book.get("total_pages", 1))
        return int((cur / tot) * 100) if cur > 1 else 0

    @classmethod
    def _book_status(cls, book: dict) -> str:
        cur = book.get("current_page", 0)
        tot = max(1, book.get("total_pages", 1))
        if cur <= 0:
            return "Unread"
        if cur + 1 >= tot:
            return "Completed"
        return "Reading"

    def _apply_filters_and_rebuild(self, *_args):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()
        self._current_cols = 0

        query = self.search_input.text().strip().lower()
        fmt_filter = self.combo_format.currentText()
        status_filter = self.combo_status.currentText()
        sort_mode = self.combo_sort.currentText()

        filtered = []
        for book in self._all_books.values():
            if query:
                haystack = f"{book.get('title', '')} {book.get('author', '')}".lower()
                if query not in haystack:
                    continue

            if fmt_filter != "All Formats":
                if book.get("format", "").upper() != fmt_filter:
                    continue

            if status_filter != "All Status":
                if self._book_status(book) != status_filter:
                    continue

            filtered.append(book)

        # Sort filtered list
        if sort_mode == "Title (A–Z)":
            filtered.sort(key=lambda b: b.get("title", "").lower())
        elif sort_mode == "Author (A–Z)":
            filtered.sort(key=lambda b: (b.get("author", "").lower(), b.get("title", "").lower()))
        elif sort_mode == "Progress (High → Low)":
            filtered.sort(key=lambda b: (self._book_progress_pct(b), b.get("last_opened", "")), reverse=True)
        else:
            filtered.sort(key=lambda b: b.get("last_opened", ""), reverse=True)

        total_count = len(self._all_books)
        shown_count = len(filtered)
        if shown_count == total_count:
            self.lbl_count.setText(f"{total_count} book{'s' if total_count != 1 else ''}")
        else:
            self.lbl_count.setText(f"Showing {shown_count} of {total_count} books")

        if not filtered:
            self.empty_lbl.setText(
                "Your library is empty. Click '+ Add Books' or '+ Import Folders' to begin."
                if total_count == 0
                else "No books match your current search or filter."
            )
            self.empty_lbl.show()
            self.scroll.hide()
            return

        self.empty_lbl.hide()
        self.scroll.show()

        for book in filtered:
            card = BookCardWidget(book)
            card.clicked.connect(self.book_selected.emit)
            card.remove_requested.connect(self.book_remove_requested.emit)
            self._cards.append(card)

        self._reflow_grid(force=True)

    def _compute_columns(self) -> int:
        avail_w = self.scroll.viewport().width() - 8
        if avail_w <= 100:
            avail_w = max(240, self.width() - 60)
        cols = (avail_w + self.GRID_SPACING) // (self.CARD_WIDTH + self.GRID_SPACING)
        return max(1, int(cols))

    def _reflow_grid(self, force: bool = False):
        if not self._cards:
            return

        cols = self._compute_columns()
        if not force and cols == self._current_cols:
            return
        self._current_cols = cols

        self.grid_container.setUpdatesEnabled(False)
        while self.grid.count():
            self.grid.takeAt(0)

        for idx, card in enumerate(self._cards):
            self.grid.addWidget(card, idx // cols, idx % cols)

        self.grid_container.adjustSize()
        self.grid_container.setUpdatesEnabled(True)

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._reflow_grid()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_grid()

    def showEvent(self, event):
        super().showEvent(event)
        self._reflow_grid(force=True)