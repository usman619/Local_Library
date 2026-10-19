from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QScrollArea, QGridLayout, QFrame, QPushButton, QSizePolicy
)

from app.ui.widgets import BookCardWidget
from app.ui.icon import _load_clean_pixmap


class LibraryView(QWidget):
    """Responsive Library Home view with fixed-height search bar and rich empty-state card."""

    book_selected = Signal(str)
    book_remove_requested = Signal(str)
    add_books_requested = Signal()
    import_folders_requested = Signal()

    CARD_Outer_WIDTH = 235

    def __init__(self):
        super().__init__()
        self._books_cache: dict = {}
        self._last_cols: int = 0
        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(28, 22, 28, 20)
        root_layout.setSpacing(14)
        root_layout.setAlignment(Qt.AlignTop)

        # ---- 1. Top Header Row (Compact, Never Stretches Vertically) ----
        header_widget = QWidget()
        header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header_row = QHBoxLayout(header_widget)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)

        header = QLabel("Your Reading Library")
        header.setFont(QFont("Sans Serif", 20, QFont.Bold))
        header.setStyleSheet("color: #CDD6F4; background: transparent;")
        title_col.addWidget(header)

        sub = QLabel(
            "Supports PDF, EPUB, FB2, MOBI, CBZ, and TXT with automatic progress tracking."
        )
        sub.setStyleSheet("color: #A6ADC8; font-size: 13px; background: transparent;")
        title_col.addWidget(sub)

        header_row.addLayout(title_col)
        header_row.addStretch(1)

        self.lbl_count = QLabel("0 books")
        self.lbl_count.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.lbl_count.setStyleSheet(
            "color: #89B4FA; font-size: 13px; font-weight: bold; background: transparent;"
        )
        header_row.addWidget(self.lbl_count, alignment=Qt.AlignBottom)

        root_layout.addWidget(header_widget)

        # ---- 2. Fixed-Height Search, Filter & Sort Bar ----
        filter_bar = QFrame()
        filter_bar.setObjectName("FilterBar")
        filter_bar.setFixedHeight(54)
        filter_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        filter_bar.setStyleSheet("""
            QFrame#FilterBar {
                background-color: #181825;
                border: 1px solid #313244;
                border-radius: 10px;
            }
            QFrame#FilterBar QLabel {
                background: transparent;
                border: none;
                color: #A6ADC8;
                font-size: 12px;
                font-weight: 600;
                padding-left: 4px;
            }
            QFrame#FilterBar QLineEdit {
                background-color: #11111B;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QFrame#FilterBar QLineEdit:focus {
                border: 1px solid #89B4FA;
            }
            QFrame#FilterBar QComboBox {
                background-color: #1E1E2E;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
            }
            QFrame#FilterBar QComboBox:hover {
                border-color: #89B4FA;
            }
        """)
        fb_layout = QHBoxLayout(filter_bar)
        fb_layout.setContentsMargins(14, 8, 14, 8)
        fb_layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by title or author...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_filters_changed)
        fb_layout.addWidget(self.search_input, stretch=1)

        fb_layout.addWidget(QLabel("Format:"))
        self.combo_format = QComboBox()
        self.combo_format.addItems(["All Formats", "PDF", "EPUB", "MOBI", "FB2", "CBZ", "TXT"])
        self.combo_format.currentTextChanged.connect(self._on_filters_changed)
        fb_layout.addWidget(self.combo_format)

        fb_layout.addWidget(QLabel("Status:"))
        self.combo_status = QComboBox()
        self.combo_status.addItems(["All Status", "Reading", "Unread", "Completed"])
        self.combo_status.currentTextChanged.connect(self._on_filters_changed)
        fb_layout.addWidget(self.combo_status)

        fb_layout.addWidget(QLabel("Sort by:"))
        self.combo_sort = QComboBox()
        self.combo_sort.addItems([
            "Recently Opened",
            "Title (A–Z)",
            "Author (A–Z)",
            "Progress (%)",
        ])
        self.combo_sort.currentTextChanged.connect(self._on_filters_changed)
        fb_layout.addWidget(self.combo_sort)

        root_layout.addWidget(filter_bar)

        # ---- 3. Scrollable Book Grid Area ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(2, 8, 2, 16)
        self.grid_layout.setHorizontalSpacing(18)
        self.grid_layout.setVerticalSpacing(18)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll.setWidget(self.grid_container)
        root_layout.addWidget(self.scroll, stretch=1)

    def _create_empty_state_widget(self, is_library_totally_empty: bool) -> QWidget:
        """Creates a centered welcome card when no books are in the library (or no filter matches)."""
        wrapper = QWidget()
        wrapper.setMinimumHeight(420)
        w_layout = QVBoxLayout(wrapper)
        w_layout.setAlignment(Qt.AlignCenter)

        card = QFrame()
        card.setObjectName("EmptyCard")
        card.setFixedWidth(480)
        card.setStyleSheet("""
            QFrame#EmptyCard {
                background-color: #181825;
                border: 1px dashed #45475A;
                border-radius: 14px;
            }
            QFrame#EmptyCard QLabel {
                background: transparent;
                border: none;
            }
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(32, 32, 32, 32)
        c_layout.setSpacing(12)
        c_layout.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignCenter)
        pix = _load_clean_pixmap(72)
        if not pix.isNull():
            icon_lbl.setPixmap(pix)
        c_layout.addWidget(icon_lbl)

        if is_library_totally_empty:
            title_txt = "Your Library is Empty"
            desc_txt = (
                "Add individual eBook/PDF files or scan an entire folder of books "
                "to build your reading collection."
            )
        else:
            title_txt = "No Matching Books Found"
            desc_txt = "Try clearing your search text or switching the Format / Status filters above."

        t_lbl = QLabel(title_txt)
        t_lbl.setAlignment(Qt.AlignCenter)
        t_lbl.setFont(QFont("Sans Serif", 15, QFont.Bold))
        t_lbl.setStyleSheet("color: #CDD6F4;")
        c_layout.addWidget(t_lbl)

        d_lbl = QLabel(desc_txt)
        d_lbl.setAlignment(Qt.AlignCenter)
        d_lbl.setWordWrap(True)
        d_lbl.setStyleSheet("color: #A6ADC8; font-size: 13px; line-height: 1.4;")
        c_layout.addWidget(d_lbl)

        if is_library_totally_empty:
            c_layout.addSpacing(8)
            btn_row = QHBoxLayout()
            btn_row.setSpacing(12)
            btn_row.setAlignment(Qt.AlignCenter)

            btn_add = QPushButton("+ Add Books")
            btn_add.setCursor(Qt.PointingHandCursor)
            btn_add.setStyleSheet("""
                QPushButton {
                    background-color: #89B4FA;
                    color: #11111B;
                    font-weight: bold;
                    font-size: 13px;
                    padding: 8px 18px;
                    border-radius: 7px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #B4BEFE;
                }
            """)
            btn_add.clicked.connect(self.add_books_requested.emit)
            btn_row.addWidget(btn_add)

            btn_folder = QPushButton("+ Import Folders")
            btn_folder.setCursor(Qt.PointingHandCursor)
            btn_folder.setStyleSheet("""
                QPushButton {
                    background-color: #313244;
                    color: #CDD6F4;
                    font-weight: bold;
                    font-size: 13px;
                    padding: 8px 18px;
                    border-radius: 7px;
                    border: 1px solid #45475A;
                }
                QPushButton:hover {
                    background-color: #45475A;
                    border-color: #89B4FA;
                }
            """)
            btn_folder.clicked.connect(self.import_folders_requested.emit)
            btn_row.addWidget(btn_folder)

            c_layout.addLayout(btn_row)

        w_layout.addWidget(card, alignment=Qt.AlignCenter)
        return wrapper

    def populate(self, books: dict, window_width: int = 1280):
        self._books_cache = books
        self._rebuild_grid(force=True)

    def _on_filters_changed(self, *_args):
        self._rebuild_grid(force=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild_grid(force=False)

    def _compute_columns(self) -> int:
        avail_w = self.scroll.viewport().width()
        if avail_w <= 100:
            avail_w = max(600, self.width() - 60)
        return max(2, avail_w // self.CARD_Outer_WIDTH)

    def _get_filtered_sorted_books(self) -> list[dict]:
        query = self.search_input.text().strip().lower()
        fmt_filter = self.combo_format.currentText()
        status_filter = self.combo_status.currentText()
        sort_mode = self.combo_sort.currentText()

        results = []
        for b in self._books_cache.values():
            if query:
                haystack = f"{b.get('title', '')} {b.get('author', '')}".lower()
                if query not in haystack:
                    continue

            if fmt_filter != "All Formats" and b.get("format", "").upper() != fmt_filter:
                continue

            cur = b.get("current_page", 0) + 1
            tot = max(1, b.get("total_pages", 1))
            pct = int((cur / tot) * 100) if cur > 1 else 0

            if status_filter == "Unread" and cur > 1:
                continue
            elif status_filter == "Reading" and (cur <= 1 or pct >= 100):
                continue
            elif status_filter == "Completed" and pct < 100:
                continue

            results.append(b)

        if sort_mode == "Title (A–Z)":
            results.sort(key=lambda x: x.get("title", "").lower())
        elif sort_mode == "Author (A–Z)":
            results.sort(key=lambda x: x.get("author", "").lower())
        elif sort_mode == "Progress (%)":
            results.sort(
                key=lambda x: (x.get("current_page", 0) + 1) / max(1, x.get("total_pages", 1)),
                reverse=True,
            )
        else:
            results.sort(key=lambda x: x.get("last_opened", ""), reverse=True)

        return results

    def _rebuild_grid(self, force: bool = False):
        cols = self._compute_columns()
        if not force and cols == self._last_cols and self._books_cache:
            return
        self._last_cols = cols

        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Reset any column stretch left over from empty state
        for c in range(max(cols, 4)):
            self.grid_layout.setColumnStretch(c, 0)

        filtered = self._get_filtered_sorted_books()
        total_count = len(self._books_cache)
        shown_count = len(filtered)

        if total_count == shown_count:
            self.lbl_count.setText(f"{total_count} book{'s' if total_count != 1 else ''}")
        else:
            self.lbl_count.setText(f"Showing {shown_count} of {total_count} books")

        if not filtered:
            self.grid_layout.setAlignment(Qt.AlignCenter)
            empty_widget = self._create_empty_state_widget(
                is_library_totally_empty=(total_count == 0)
            )
            self.grid_layout.addWidget(empty_widget, 0, 0)
            return

        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        for idx, book in enumerate(filtered):
            card = BookCardWidget(book)
            card.clicked.connect(self.book_selected.emit)
            card.remove_requested.connect(self.book_remove_requested.emit)
            self.grid_layout.addWidget(card, idx // cols, idx % cols)