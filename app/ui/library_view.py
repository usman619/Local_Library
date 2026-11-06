import os
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QTimer, QPoint, QUrl, QSize
from PySide6.QtGui import QFont, QDesktopServices, QAction
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QScrollArea, QGridLayout, QFrame, QPushButton,
    QSizePolicy, QMenu, QInputDialog, QMessageBox, QListWidget,
    QListWidgetItem, QWidgetAction
)

from app.ui.widgets import BookCardWidget
from app.ui.icon import _load_clean_pixmap, svg_icon, svg_pixmap
from app.ui.cover_loader import CoverLoader


class LibraryView(QWidget):
    """Responsive Library Home view with SVG-based virtual Category management, search bar, and grid."""

    book_selected = Signal(str)
    book_remove_requested = Signal(str)
    add_books_requested = Signal()
    import_folders_requested = Signal()

    create_folder_requested = Signal(str)
    rename_folder_requested = Signal(str, str)
    delete_folder_requested = Signal(str)
    move_book_requested = Signal(str, str)
    change_root_dir_requested = Signal(str)

    CARD_WIDTH = 218
    GRID_SPACING = 18

    def __init__(self):
        super().__init__()
        self._books_cache: dict = {}
        self._folders_list: list[str] = []
        self._active_folder: str = "__ALL__"
        self._last_cols: int = 0
        self._fallback_width: int = 1280
        self._filtered_books: list[dict] = []
        self._visible_cards: dict[str, BookCardWidget] = {}
        self._cover_cards: dict[str, set[BookCardWidget]] = {}
        self._empty_widget: QWidget | None = None
        self._layout_dirty = True
        self._last_grid_key = None
        self._cover_loader = CoverLoader(parent=self)
        self._cover_loader.loaded.connect(self._on_cover_loaded)
        self._build_ui()
        self.scroll.verticalScrollBar().valueChanged.connect(self._update_visible_cards)

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(28, 22, 28, 20)
        root_layout.setSpacing(12)
        root_layout.setAlignment(Qt.AlignTop)

        # ---- 1. Top Header Row ----
        header_widget = QWidget()
        header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header_row = QHBoxLayout(header_widget)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)

        header = QLabel("Your Reading Library")
        header.setFont(QFont("Inter", 20, QFont.Bold))
        header.setStyleSheet("color: #CDD6F4; background: transparent;")
        title_col.addWidget(header)

        sub = QLabel(
            "Supports PDF, EPUB, FB2, MOBI, CBZ, and TXT with virtual collections & automatic progress tracking."
        )
        sub.setStyleSheet("color: #9399B2; font-size: 13px; background: transparent;")
        title_col.addWidget(sub)

        header_row.addLayout(title_col)
        header_row.addStretch(1)

        self.lbl_count = QLabel("0 books")
        self.lbl_count.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.lbl_count.setStyleSheet(
            "background-color: #1E1E2E; color: #89B4FA; font-size: 12px; "
            "font-weight: 700; padding: 5px 12px; border-radius: 12px; border: 1px solid #313244;"
        )
        header_row.addWidget(self.lbl_count, alignment=Qt.AlignBottom)

        root_layout.addWidget(header_widget)

        # ---- 2. Search, Filter & Sort Bar ----
        filter_bar = QFrame()
        filter_bar.setObjectName("FilterBar")
        filter_bar.setFixedHeight(54)
        filter_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        filter_bar.setStyleSheet("""
            QFrame#FilterBar {
                background-color: #181825;
                border: 1px solid #2A2B3D;
                border-radius: 10px;
            }
            QFrame#FilterBar QLabel {
                background: transparent;
                border: none;
                color: #9399B2;
                font-size: 12px;
                font-weight: 600;
                padding-left: 4px;
            }
            QFrame#FilterBar QLineEdit {
                background-color: #11111B;
                color: #CDD6F4;
                border: 1px solid #313244;
                border-radius: 7px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QFrame#FilterBar QLineEdit:focus {
                border: 1px solid #89B4FA;
            }
        """)
        fb_layout = QHBoxLayout(filter_bar)
        fb_layout.setContentsMargins(14, 8, 14, 8)
        fb_layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by title, author, or category...")
        self.search_input.addAction(svg_icon("search", "#6C7086", 15), QLineEdit.LeadingPosition)
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

        # ---- 3. Interactive Virtual Category Bar (SVG Icons, No Emojis) ----
        folder_bar = QFrame()
        folder_bar.setObjectName("FolderBar")
        folder_bar.setFixedHeight(48)
        folder_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        folder_bar.setStyleSheet("""
            QFrame#FolderBar {
                background-color: #181825;
                border: 1px solid #2A2B3D;
                border-radius: 10px;
            }
            QFrame#FolderBar QLabel {
                background: transparent;
                border: none;
                color: #F9E2AF;
                font-size: 12px;
                font-weight: 700;
            }
        """)
        fbar_layout = QHBoxLayout(folder_bar)
        fbar_layout.setContentsMargins(14, 6, 14, 6)
        fbar_layout.setSpacing(8)

        cat_icon_lbl = QLabel()
        cat_icon_lbl.setPixmap(svg_pixmap("layers", "#F9E2AF", 15))
        fbar_layout.addWidget(cat_icon_lbl)
        fbar_layout.addWidget(QLabel("Categories:"))

        self.pills_scroll = QScrollArea()
        self.pills_scroll.setWidgetResizable(True)
        self.pills_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pills_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pills_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.pills_container = QWidget()
        self.pills_container.setStyleSheet("background: transparent;")
        self.pills_layout = QHBoxLayout(self.pills_container)
        self.pills_layout.setContentsMargins(0, 0, 0, 0)
        self.pills_layout.setSpacing(6)
        self.pills_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.pills_scroll.setWidget(self.pills_container)
        fbar_layout.addWidget(self.pills_scroll, stretch=1)

        action_btn_style = """
            QPushButton {
                background-color: #1E1E2E;
                color: #CDD6F4;
                border: 1px solid #313244;
                border-radius: 7px;
                padding: 5px 11px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2A2B3D;
                border-color: #89B4FA;
                color: #FFFFFF;
            }
            QPushButton:disabled {
                color: #585B70;
                border-color: #232436;
            }
        """

        self.btn_new_folder = QPushButton("New Category")
        self.btn_new_folder.setIcon(svg_icon("folder_plus", "#89B4FA", 14))
        self.btn_new_folder.setIconSize(QSize(14, 14))
        self.btn_new_folder.setCursor(Qt.PointingHandCursor)
        self.btn_new_folder.setToolTip("Create a new virtual category to organize your books")
        self.btn_new_folder.setStyleSheet(action_btn_style)
        self.btn_new_folder.clicked.connect(self._prompt_create_folder)
        fbar_layout.addWidget(self.btn_new_folder)

        self.btn_rename_folder = QPushButton("Rename")
        self.btn_rename_folder.setIcon(svg_icon("edit", "#CDD6F4", 13))
        self.btn_rename_folder.setIconSize(QSize(13, 13))
        self.btn_rename_folder.setCursor(Qt.PointingHandCursor)
        self.btn_rename_folder.setToolTip("Rename the active category")
        self.btn_rename_folder.setStyleSheet(action_btn_style)
        self.btn_rename_folder.clicked.connect(self._prompt_rename_folder)
        fbar_layout.addWidget(self.btn_rename_folder)

        self.btn_delete_folder = QPushButton("Delete")
        self.btn_delete_folder.setIcon(svg_icon("trash", "#F38BA8", 13))
        self.btn_delete_folder.setIconSize(QSize(13, 13))
        self.btn_delete_folder.setCursor(Qt.PointingHandCursor)
        self.btn_delete_folder.setToolTip(
            "Delete the selected category (books stay in your library as Uncategorized)"
        )
        self.btn_delete_folder.setStyleSheet(action_btn_style)
        self.btn_delete_folder.clicked.connect(self._prompt_delete_folder)
        fbar_layout.addWidget(self.btn_delete_folder)

        root_layout.addWidget(folder_bar)

        # ---- 4. Scrollable Book Grid Area ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 8, 0, 16)
        self.grid_layout.setHorizontalSpacing(self.GRID_SPACING)
        self.grid_layout.setVerticalSpacing(self.GRID_SPACING)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        self.scroll.setWidget(self.grid_container)
        root_layout.addWidget(self.scroll, stretch=1)

    # ---- VIRTUAL CATEGORY PILLS & CRUD DIALOGS ----

    def _get_store(self):
        win = self.window()
        return getattr(win, "store", None)

    def _refresh_from_store(self):
        store = self._get_store()
        if store is not None:
            self._books_cache = store.books
            self._folders_list = store.get_folders()
        self._rebuild_folder_pills()
        self._rebuild_grid(force=True)

    def _rebuild_folder_pills(self):
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total_books = len(self._books_cache)
        uncat_count = sum(
            1 for b in self._books_cache.values()
            if isinstance(b, dict) and not b.get("category", "").strip()
        )
        folder_counts: dict[str, int] = {f: 0 for f in self._folders_list}
        for b in self._books_cache.values():
            if isinstance(b, dict):
                cat = b.get("category", "").strip()
                if cat:
                    folder_counts[cat] = folder_counts.get(cat, 0) + 1

        if (
            self._active_folder not in ("__ALL__", "__UNCAT__")
            and self._active_folder not in folder_counts
        ):
            self._active_folder = "__ALL__"

        pills_spec = [
            ("__ALL__", f"All Books ({total_books})", None),
            ("__UNCAT__", f"Uncategorized ({uncat_count})", None),
        ]
        for f_name in self._folders_list:
            pills_spec.append((f_name, f"{f_name} ({folder_counts.get(f_name, 0)})", "folder"))

        for key, label, icon_name in pills_spec:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            is_active = key == self._active_folder
            if icon_name:
                btn.setIcon(svg_icon(icon_name, "#11111B" if is_active else "#F9E2AF", 13))
                btn.setIconSize(QSize(13, 13))

            if is_active:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #89B4FA;
                        color: #11111B;
                        border: 1px solid #89B4FA;
                        border-radius: 13px;
                        padding: 4px 12px;
                        font-size: 11px;
                        font-weight: 700;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #11111B;
                        color: #A6ADC8;
                        border: 1px solid #313244;
                        border-radius: 13px;
                        padding: 4px 12px;
                        font-size: 11px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background-color: #252538;
                        color: #CDD6F4;
                        border-color: #89B4FA;
                    }
                """)
            btn.clicked.connect(lambda _checked=False, k=key: self._select_folder_pill(k))
            self.pills_layout.addWidget(btn)

        is_custom_folder = self._active_folder not in ("__ALL__", "__UNCAT__")
        self.btn_rename_folder.setEnabled(is_custom_folder)
        self.btn_delete_folder.setEnabled(is_custom_folder)

    def _select_folder_pill(self, folder_key: str):
        self._active_folder = folder_key
        self._rebuild_folder_pills()
        self._rebuild_grid(force=True)

    def _prompt_create_folder(self):
        name, ok = QInputDialog.getText(
            self,
            "New Category",
            "Enter a name for the new category (e.g. AI Agents, Networking, Math):",
        )
        if ok and name.strip():
            clean = name.strip()
            store = self._get_store()
            if store is not None:
                success, msg = store.create_folder(clean)
                if not success:
                    QMessageBox.warning(self, "Category Error", msg)
                self._refresh_from_store()
            else:
                self.create_folder_requested.emit(clean)

    def _prompt_rename_folder(self):
        if self._active_folder in ("__ALL__", "__UNCAT__"):
            return
        old_name = self._active_folder
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Category",
            f"Rename category '{old_name}' to:",
            text=old_name,
        )
        if ok and new_name.strip() and new_name.strip() != old_name:
            clean_new = new_name.strip()
            store = self._get_store()
            if store is not None:
                success, msg = store.rename_folder(old_name, clean_new)
                if not success:
                    QMessageBox.warning(self, "Rename Error", msg)
                else:
                    self._active_folder = msg
                self._refresh_from_store()
            else:
                self._active_folder = clean_new
                self.rename_folder_requested.emit(old_name, clean_new)

    def _prompt_delete_folder(self):
        if self._active_folder in ("__ALL__", "__UNCAT__"):
            return
        folder_name = self._active_folder
        reply = QMessageBox.question(
            self,
            "Delete Category",
            f"Delete category '{folder_name}'?\n\n"
            f"(Your book files will not be touched; books in '{folder_name}' will return to Uncategorized.)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._active_folder = "__ALL__"
            store = self._get_store()
            if store is not None:
                success, msg = store.delete_folder(folder_name)
                if not success:
                    QMessageBox.warning(self, "Delete Error", msg)
                self._refresh_from_store()
            else:
                self.delete_folder_requested.emit(folder_name)

    def _execute_move_book(self, book_path: str, folder_name: str):
        store = self._get_store()
        if store is not None:
            success, msg = store.move_book_to_folder(book_path, folder_name)
            if not success:
                QMessageBox.warning(self, "Category Error", msg)
            self._refresh_from_store()
        else:
            self.move_book_requested.emit(book_path, folder_name)

    def _find_book_entry(self, book_path: str) -> dict | None:
        if not book_path:
            return None
        if book_path in self._books_cache:
            return self._books_cache[book_path]
        norm = os.path.abspath(book_path)
        if norm in self._books_cache:
            return self._books_cache[norm]
        target_name = os.path.basename(book_path)
        for k, b in self._books_cache.items():
            if not isinstance(b, dict):
                continue
            bp = b.get("path") or b.get("filepath") or str(k)
            if (
                bp == book_path
                or os.path.abspath(bp) == norm
                or os.path.basename(bp) == target_name
            ):
                b["path"] = bp
                return b
        return None

    def _show_book_folder_menu(self, book_path: str, global_pos: QPoint):
        store = self._get_store()
        if store is not None:
            self._folders_list = store.get_folders()

        book = self._find_book_entry(book_path)
        if not book:
            return
        cur_cat = book.get("category", "").strip()

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1E1E2E;
                color: #CDD6F4;
                border: 1px solid #313244;
                border-radius: 10px;
                padding: 6px;
            }
            QMenu::item {
                padding: 6px 14px;
                min-height: 24px;
                border-radius: 6px;
                font-size: 13px;
                margin: 1px 4px;
            }
            QMenu::item:selected {
                background-color: #313244;
                color: #89B4FA;
                font-weight: 600;
            }
            QMenu::separator {
                height: 1px;
                background: #313244;
                margin: 5px 6px;
            }
        """)

        act_open = QAction(
            svg_icon("book_open", "#89B4FA", 14),
            "Open Book",
            menu,
        )
        act_open.triggered.connect(
            lambda _chk=False, bp=book_path: self.book_selected.emit(bp)
        )
        menu.addAction(act_open)
        menu.addSeparator()

        # Category Mini Panel
        cat_panel = QFrame()
        cat_panel.setObjectName("CategoryMiniPanel")
        panel_layout = QVBoxLayout(cat_panel)
        panel_layout.setContentsMargins(8, 6, 8, 6)
        panel_layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(2, 2, 2, 2)
        header_layout.setSpacing(6)

        lbl_title = QLabel("CATEGORIES")
        lbl_title.setStyleSheet("color: #89B4FA; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()

        cur_badge = QLabel(f"Current: {cur_cat}" if cur_cat else "Uncategorized")
        cur_badge.setStyleSheet(
            "background-color: #252538; color: #F9E2AF; font-size: 10px; font-weight: 600; "
            "padding: 2px 8px; border-radius: 9px; border: 1px solid #36384C;"
            if cur_cat else
            "background-color: #181825; color: #9399B2; font-size: 10px; font-weight: 500; "
            "padding: 2px 8px; border-radius: 9px; border: 1px solid #2A2B3D;"
        )
        header_layout.addWidget(cur_badge)
        panel_layout.addLayout(header_layout)

        category_list = QListWidget(cat_panel)
        category_list.setObjectName("CategoryMiniList")
        category_list.setUniformItemSizes(True)
        category_list.setFocusPolicy(Qt.StrongFocus)
        category_list.setCursor(Qt.PointingHandCursor)
        category_list.setTextElideMode(Qt.ElideRight)
        category_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        category_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        folder_count = len(self._folders_list)
        if folder_count > 5:
            row_count = 4.5  # View showing 4 to 5 categories with smooth scroll indicator
        elif folder_count == 5:
            row_count = 5.0
        else:
            row_count = 4.0
        category_list.setFixedHeight(int(row_count * 28) + 4)
        category_list.setFixedWidth(270)
        cat_panel.setStyleSheet("""
            QFrame#CategoryMiniPanel {
                background-color: #181825;
                border: 1px solid #313244;
                border-radius: 8px;
                margin: 2px 4px;
            }
            QListWidget#CategoryMiniList {
                background: transparent;
                color: #CDD6F4;
                border: none;
                padding: 0px;
                outline: none;
            }
            QListWidget#CategoryMiniList::item {
                min-height: 26px;
                padding: 3px 8px;
                border-radius: 5px;
                font-size: 12px;
                margin-bottom: 2px;
            }
            QListWidget#CategoryMiniList::item:hover {
                background-color: #313244;
                color: #89B4FA;
            }
            QScrollBar:vertical {
                background: #11111B;
                width: 6px;
                margin: 2px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #45475A;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #89B4FA;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        if not self._folders_list:
            empty_item = QListWidgetItem(
                svg_icon("info", "#6C7086", 13),
                "No categories yet",
            )
            empty_item.setFlags(Qt.NoItemFlags)
            empty_item.setData(Qt.UserRole, "")
            category_list.addItem(empty_item)
        else:
            for f_name in self._folders_list:
                is_cur = f_name == cur_cat
                label = f"{f_name}  ✓" if is_cur else f_name
                icon = (
                    svg_icon("check", "#A6E3A1", 13)
                    if is_cur
                    else svg_icon("folder", "#89B4FA", 13)
                )
                item = QListWidgetItem(icon, label)
                item.setData(Qt.UserRole, f_name)
                item.setToolTip(f"Currently in '{f_name}'" if is_cur else f"Move to '{f_name}'")
                category_list.addItem(item)

        def assign_category(item: QListWidgetItem):
            folder_name = item.data(Qt.UserRole)
            if folder_name and folder_name != cur_cat:
                self._execute_move_book(book_path, folder_name)
            menu.close()

        panel_layout.addWidget(category_list)
        category_list.itemClicked.connect(assign_category)
        category_action = QWidgetAction(menu)
        category_action.setDefaultWidget(cat_panel)
        menu.addAction(category_action)

        if cur_cat:
            act_uncat = QAction(
                svg_icon("undo", "#FAB387", 14),
                "Remove from Category (Uncategorized)",
                menu,
            )
            act_uncat.triggered.connect(
                lambda _chk=False, bp=book_path: self._execute_move_book(bp, "")
            )
            menu.addAction(act_uncat)

        menu.addSeparator()
        act_new_move = QAction(
            svg_icon("folder_plus", "#A6E3A1", 14),
            "New Category && Assign Here...",
            menu,
        )
        act_new_move.triggered.connect(
            lambda _chk=False, bp=book_path: self._create_folder_and_move_book(bp)
        )
        menu.addAction(act_new_move)

        menu.addSeparator()
        act_reveal = QAction(
            svg_icon("external", "#CDD6F4", 14),
            "Show File in File Manager",
            menu,
        )
        act_reveal.triggered.connect(lambda _chk=False, bp=book_path: self._reveal_in_file_manager(bp))
        menu.addAction(act_reveal)

        act_rem = QAction(
            svg_icon("trash", "#F38BA8", 14),
            "Remove Book from Library",
            menu,
        )
        act_rem.triggered.connect(lambda _chk=False, bp=book_path: self.book_remove_requested.emit(bp))
        menu.addAction(act_rem)

        menu.exec(global_pos)

    def _create_folder_and_move_book(self, book_path: str):
        name, ok = QInputDialog.getText(
            self,
            "New Category & Assign Book",
            "Enter new category name:",
        )
        if ok and name.strip():
            clean = name.strip()
            store = self._get_store()
            if store is not None:
                store.create_folder(clean)
                self._execute_move_book(book_path, clean)
            else:
                self.create_folder_requested.emit(clean)
                self.move_book_requested.emit(book_path, clean)

    @staticmethod
    def _reveal_in_file_manager(book_path: str):
        folder = str(Path(book_path).resolve().parent)
        if os.path.exists(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ---- EMPTY STATE & GRID RENDERING ----

    def _create_empty_state_widget(self, is_library_totally_empty: bool) -> QWidget:
        wrapper = QWidget()
        wrapper.setMinimumHeight(380)
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
        pix = _load_clean_pixmap(64)
        if not pix.isNull():
            icon_lbl.setPixmap(pix)
        c_layout.addWidget(icon_lbl)

        if is_library_totally_empty:
            title_txt = "Your Library is Empty"
            desc_txt = (
                "Add individual eBook/PDF files or scan an entire folder of books "
                "to build your reading collection."
            )
        elif self._active_folder not in ("__ALL__", "__UNCAT__"):
            title_txt = f"Category '{self._active_folder}' is Empty"
            desc_txt = (
                f"Click the 'Category' button (or right-click) on any book card in 'All Books' "
                f"to assign it to '{self._active_folder}'."
            )
        else:
            title_txt = "No Matching Books Found"
            desc_txt = "Try clearing your search text or switching the Category / Format / Status filters above."

        t_lbl = QLabel(title_txt)
        t_lbl.setAlignment(Qt.AlignCenter)
        t_lbl.setFont(QFont("Inter", 15, QFont.Bold))
        t_lbl.setStyleSheet("color: #CDD6F4;")
        c_layout.addWidget(t_lbl)

        d_lbl = QLabel(desc_txt)
        d_lbl.setAlignment(Qt.AlignCenter)
        d_lbl.setWordWrap(True)
        d_lbl.setStyleSheet("color: #9399B2; font-size: 13px; line-height: 1.4;")
        c_layout.addWidget(d_lbl)

        if is_library_totally_empty:
            c_layout.addSpacing(8)
            btn_row = QHBoxLayout()
            btn_row.setSpacing(12)
            btn_row.setAlignment(Qt.AlignCenter)

            btn_add = QPushButton("Add Books")
            btn_add.setIcon(svg_icon("book_plus", "#11111B", 15))
            btn_add.setCursor(Qt.PointingHandCursor)
            btn_add.setStyleSheet("""
                QPushButton {
                    background-color: #89B4FA;
                    color: #11111B;
                    font-weight: 700;
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

            btn_folder = QPushButton("Import Folders")
            btn_folder.setIcon(svg_icon("folder_plus", "#CDD6F4", 15))
            btn_folder.setCursor(Qt.PointingHandCursor)
            btn_folder.setStyleSheet("""
                QPushButton {
                    background-color: #313244;
                    color: #CDD6F4;
                    font-weight: 700;
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

    def populate(
        self,
        books: dict,
        window_width: int = 1280,
        folders: list[str] | None = None,
        library_root: str = "",
    ):
        self._books_cache = books
        self._fallback_width = window_width
        store = self._get_store()
        if folders is not None:
            self._folders_list = folders
        elif store is not None:
            self._folders_list = store.get_folders()

        self._rebuild_folder_pills()
        QTimer.singleShot(20, lambda: self._rebuild_grid(force=True))

    def _on_filters_changed(self, *_args):
        self._rebuild_grid(force=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild_grid(force=False)

    def _compute_columns(self) -> int:
        avail_w = self.scroll.viewport().width()
        if avail_w <= 100:
            avail_w = getattr(self, "_fallback_width", 1280) - 70
        return max(2, (avail_w + self.GRID_SPACING) // (self.CARD_WIDTH + self.GRID_SPACING))

    def _get_filtered_sorted_books(self) -> list[dict]:
        query = self.search_input.text().strip().lower()
        fmt_filter = self.combo_format.currentText()
        status_filter = self.combo_status.currentText()
        sort_mode = self.combo_sort.currentText()

        results = []
        for k, b in self._books_cache.items():
            if not isinstance(b, dict):
                continue
            if not b.get("path"):
                b["path"] = b.get("filepath") or str(k)

            cat = b.get("category", "").strip()

            if self._active_folder == "__UNCAT__" and cat != "":
                continue
            elif (
                self._active_folder not in ("__ALL__", "__UNCAT__")
                and cat != self._active_folder
            ):
                continue

            if query:
                haystack = f"{b.get('title', '')} {b.get('author', '')} {cat}".lower()
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
                key=lambda x: (x.get("current_page", 0) + 1)
                / max(1, x.get("total_pages", 1)),
                reverse=True,
            )
        else:
            results.sort(key=lambda x: x.get("last_opened", ""), reverse=True)

        return results

    def _rebuild_grid(self, force: bool = False):
        cols = self._compute_columns()
        filtered = self._get_filtered_sorted_books()
        filter_key = (
            self.search_input.text().strip().lower(),
            self.combo_format.currentText(),
            self.combo_status.currentText(),
            self.combo_sort.currentText(),
            self._active_folder,
            cols,
            tuple(book.get("path", "") for book in filtered),
        )
        if force and filter_key == self._last_grid_key:
            return
        if not force and cols == self._last_cols and self._books_cache:
            self._update_visible_cards()
            return
        self._last_grid_key = filter_key
        columns_changed = cols != self._last_cols
        self._last_cols = cols

        if self._empty_widget is not None:
            self.grid_layout.removeWidget(self._empty_widget)
            self._empty_widget.deleteLater()
            self._empty_widget = None
        if columns_changed or force:
            while self.grid_layout.count():
                item = self.grid_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._visible_cards.clear()
            self._cover_cards.clear()
        self._layout_dirty = True

        for c in range(max(cols, 4)):
            self.grid_layout.setColumnStretch(c, 0)

        self._filtered_books = filtered
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
            self._empty_widget = empty_widget
            self.grid_layout.addWidget(self._empty_widget, 0, 0)
            return

        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        for idx, book in enumerate(filtered):
            path = book.get("path", "")
            if not path:
                continue
            card = BookCardWidget(book)
            card.clicked.connect(self.book_selected.emit)
            card.remove_requested.connect(self.book_remove_requested.emit)
            card.folder_menu_requested.connect(self._show_book_folder_menu)
            self._visible_cards[path] = card
            self.grid_layout.addWidget(card, idx // cols, idx % cols)
            cover_path = (
                book.get("cover_path")
                or book.get("cover_image")
                or book.get("cover")
                or ""
            )
            if cover_path:
                self._cover_cards.setdefault(cover_path, set()).add(card)
                self._cover_loader.request(cover_path)
        rows = (len(filtered) + cols - 1) // cols
        margins = self.grid_layout.contentsMargins()
        self.grid_container.setMinimumHeight(
            rows * (346 + self.GRID_SPACING) + margins.top() + margins.bottom()
        )

    def _update_visible_cards(self):
        # Cards are intentionally kept in the layout so QScrollArea has
        # deterministic row geometry while covers load asynchronously.
        return

    def _on_cover_loaded(self, path: str, image):
        for card in self._cover_cards.get(path, ()):
            card.set_cover_image(image)

    def closeEvent(self, event):
        self._cover_loader.close()
        super().closeEvent(event)