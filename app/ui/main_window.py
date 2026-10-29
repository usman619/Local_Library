import os
from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
    QPushButton,
    QLabel,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QFrame,
    QDialog,
    QFormLayout,
)

from app.config import THEMES, SUPPORTED_EXTENSIONS, GLOBAL_QSS
from app.core.store import LibraryStore
from app.ui.icon import get_app_icon, svg_icon
from app.ui.library_view import LibraryView
from app.ui.reader_view import ReaderView


class LocalLibraryWindow(QMainWindow):
    """Main application window with SVG-powered top navigation bar, Library grid, and Reader view."""

    def __init__(self):
        super().__init__()
        self.store = LibraryStore()

        self.setWindowTitle("Linux eBook & PDF Reader — LocalLibrary")
        self.setWindowIcon(get_app_icon())
        self.resize(1380, 860)
        self.setMinimumSize(960, 620)
        self.setStyleSheet(GLOBAL_QSS)

        self._build_ui()
        self._setup_shortcuts()
        self.show_library_view()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---- Top Navigation & Reader Toolbar ----
        self.top_bar = QFrame()
        self.top_bar.setObjectName("TopToolbar")
        self.top_bar.setFixedHeight(52)
        self.top_bar.setStyleSheet("""
            QFrame#TopToolbar {
                background-color: #181825;
                border-bottom: 1px solid #2A2B3D;
            }
            QFrame#TopToolbar QLabel {
                color: #9399B2;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
                border: none;
            }
            QFrame#TopToolbar QPushButton {
                background-color: #1E1E2E;
                color: #CDD6F4;
                border: 1px solid #313244;
                border-radius: 7px;
                padding: 5px 11px;
                font-size: 12px;
                font-weight: 600;
            }
            QFrame#TopToolbar QPushButton:hover {
                background-color: #2A2B3D;
                border-color: #89B4FA;
                color: #FFFFFF;
            }
            QFrame#TopToolbar QPushButton:pressed {
                background-color: #313244;
            }
        """)
        tb_layout = QHBoxLayout(self.top_bar)
        tb_layout.setContentsMargins(16, 6, 16, 6)
        tb_layout.setSpacing(8)

        # --- Library Mode Controls ---
        self.btn_add_books = QPushButton("Add Books")
        self.btn_add_books.setIcon(svg_icon("book_plus", "#89B4FA", 15))
        self.btn_add_books.setIconSize(QSize(15, 15))
        self.btn_add_books.setCursor(Qt.PointingHandCursor)
        self.btn_add_books.setToolTip("Import one or more eBook / PDF files (Ctrl+O)")
        self.btn_add_books.clicked.connect(self.import_multiple_files_dialog)
        tb_layout.addWidget(self.btn_add_books)

        self.btn_import_folders = QPushButton("Import Folders")
        self.btn_import_folders.setIcon(svg_icon("folder_plus", "#A6E3A1", 15))
        self.btn_import_folders.setIconSize(QSize(15, 15))
        self.btn_import_folders.setCursor(Qt.PointingHandCursor)
        self.btn_import_folders.setToolTip("Scan a folder recursively for supported books")
        self.btn_import_folders.clicked.connect(self.import_multiple_folders_dialog)
        tb_layout.addWidget(self.btn_import_folders)

        # --- Reader Mode Controls ---
        self.btn_back_lib = QPushButton("Library Home")
        self.btn_back_lib.setIcon(svg_icon("home", "#89B4FA", 15))
        self.btn_back_lib.setIconSize(QSize(15, 15))
        self.btn_back_lib.setCursor(Qt.PointingHandCursor)
        self.btn_back_lib.setToolTip("Return to your Library (Esc)")
        self.btn_back_lib.clicked.connect(self.show_library_view)
        tb_layout.addWidget(self.btn_back_lib)

        self.sep_1 = self._create_separator()
        tb_layout.addWidget(self.sep_1)

        self.btn_toggle_sidebar = QPushButton("Sidebar")
        self.btn_toggle_sidebar.setIcon(svg_icon("sidebar", "#CDD6F4", 15))
        self.btn_toggle_sidebar.setIconSize(QSize(15, 15))
        self.btn_toggle_sidebar.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_sidebar.setToolTip("Show / Hide Contents, Bookmarks & Highlights Sidebar")
        self.btn_toggle_sidebar.clicked.connect(self._on_toggle_sidebar)
        tb_layout.addWidget(self.btn_toggle_sidebar)

        self.lbl_view = QLabel("View:")
        tb_layout.addWidget(self.lbl_view)

        self.combo_view_mode = QComboBox()
        self.combo_view_mode.addItems(["Continuous Scroll", "Single Page"])
        saved_view_mode = self.store.settings.get("view_mode", "Continuous Scroll")
        if saved_view_mode in ("Continuous Scroll", "Single Page"):
            self.combo_view_mode.setCurrentText(saved_view_mode)
        self.combo_view_mode.currentTextChanged.connect(self._on_view_mode_changed)
        tb_layout.addWidget(self.combo_view_mode)

        self.btn_prev_page = QPushButton()
        self.btn_prev_page.setIcon(svg_icon("chevron_left", "#CDD6F4", 15))
        self.btn_prev_page.setIconSize(QSize(15, 15))
        self.btn_prev_page.setFixedSize(30, 30)
        self.btn_prev_page.setCursor(Qt.PointingHandCursor)
        self.btn_prev_page.setToolTip("Previous Page (PageUp)")
        self.btn_prev_page.clicked.connect(lambda: self.reader_view.step_prev_page())
        tb_layout.addWidget(self.btn_prev_page)

        self.btn_next_page = QPushButton()
        self.btn_next_page.setIcon(svg_icon("chevron_right", "#CDD6F4", 15))
        self.btn_next_page.setIconSize(QSize(15, 15))
        self.btn_next_page.setFixedSize(30, 30)
        self.btn_next_page.setCursor(Qt.PointingHandCursor)
        self.btn_next_page.setToolTip("Next Page (PageDown)")
        self.btn_next_page.clicked.connect(lambda: self.reader_view.step_next_page())
        tb_layout.addWidget(self.btn_next_page)

        self.lbl_theme = QLabel("Theme:")
        tb_layout.addWidget(self.lbl_theme)

        self.combo_theme = QComboBox()
        self.combo_theme.addItems(list(THEMES.keys()))
        saved_theme = self.store.settings.get("theme_name", "Smart Dark (Mocha)")
        if saved_theme in THEMES:
            self.combo_theme.setCurrentText(saved_theme)
        self.combo_theme.currentTextChanged.connect(self._on_theme_changed)
        tb_layout.addWidget(self.combo_theme)

        self.btn_zoom_out = QPushButton()
        self.btn_zoom_out.setIcon(svg_icon("zoom_out", "#CDD6F4", 15))
        self.btn_zoom_out.setIconSize(QSize(15, 15))
        self.btn_zoom_out.setFixedSize(32, 30)
        self.btn_zoom_out.setCursor(Qt.PointingHandCursor)
        self.btn_zoom_out.setToolTip("Zoom Out (Ctrl+-)")
        self.btn_zoom_out.clicked.connect(lambda: self.reader_view.zoom_out())
        tb_layout.addWidget(self.btn_zoom_out)

        self.btn_zoom_in = QPushButton()
        self.btn_zoom_in.setIcon(svg_icon("zoom_in", "#CDD6F4", 15))
        self.btn_zoom_in.setIconSize(QSize(15, 15))
        self.btn_zoom_in.setFixedSize(32, 30)
        self.btn_zoom_in.setCursor(Qt.PointingHandCursor)
        self.btn_zoom_in.setToolTip("Zoom In (Ctrl+=)")
        self.btn_zoom_in.clicked.connect(lambda: self.reader_view.zoom_in())
        tb_layout.addWidget(self.btn_zoom_in)

        self.btn_bookmark = QPushButton("Bookmark")
        self.btn_bookmark.setIcon(svg_icon("bookmark", "#F9E2AF", 14))
        self.btn_bookmark.setIconSize(QSize(14, 14))
        self.btn_bookmark.setCursor(Qt.PointingHandCursor)
        self.btn_bookmark.setToolTip("Toggle Bookmark on Current Page (Ctrl+D)")
        self.btn_bookmark.clicked.connect(lambda: self.reader_view.toggle_bookmark())
        tb_layout.addWidget(self.btn_bookmark)

        self.btn_find = QPushButton("Find")
        self.btn_find.setIcon(svg_icon("search", "#CDD6F4", 14))
        self.btn_find.setIconSize(QSize(14, 14))
        self.btn_find.setCursor(Qt.PointingHandCursor)
        self.btn_find.setToolTip("Search Text in Book (Ctrl+F)")
        self.btn_find.clicked.connect(lambda: self.reader_view.open_search_bar())
        tb_layout.addWidget(self.btn_find)

        self.btn_info = QPushButton("Info")
        self.btn_info.setIcon(svg_icon("info", "#89DCEB", 14))
        self.btn_info.setIconSize(QSize(14, 14))
        self.btn_info.setCursor(Qt.PointingHandCursor)
        self.btn_info.setToolTip("View Book Metadata & File Details")
        self.btn_info.clicked.connect(self._show_book_info_dialog)
        tb_layout.addWidget(self.btn_info)

        tb_layout.addStretch(1)

        # Page & Zoom Status Badge (Right-aligned in Top Bar)
        self.lbl_page_status = QLabel("")
        self.lbl_page_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_page_status.setStyleSheet("""
            QLabel {
                background-color: #1E1E2E;
                color: #89B4FA;
                border: 1px solid #313244;
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 700;
            }
        """)
        tb_layout.addWidget(self.lbl_page_status)

        root_layout.addWidget(self.top_bar)

        # ---- Main Stacked Area (0 = LibraryView, 1 = ReaderView) ----
        self.stack = QStackedWidget()
        self.library_view = LibraryView()
        self.reader_view = ReaderView(self.store)

        self.stack.addWidget(self.library_view)
        self.stack.addWidget(self.reader_view)
        root_layout.addWidget(self.stack, stretch=1)

        # Connect LibraryView signals
        self.library_view.book_selected.connect(self.open_book)
        self.library_view.book_remove_requested.connect(self.remove_book_from_library)
        self.library_view.add_books_requested.connect(self.import_multiple_files_dialog)
        self.library_view.import_folders_requested.connect(self.import_multiple_folders_dialog)
        self.library_view.create_folder_requested.connect(self._on_create_folder)
        self.library_view.rename_folder_requested.connect(self._on_rename_folder)
        self.library_view.delete_folder_requested.connect(self._on_delete_folder)
        self.library_view.move_book_requested.connect(self._on_move_book_to_folder)

        # Connect ReaderView signals
        self.reader_view.status_updated.connect(self._on_reader_status_updated)
        if hasattr(self.reader_view, "book_closed"):
            self.reader_view.book_closed.connect(self.show_library_view)

    @staticmethod
    def _create_separator() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        sep.setFixedHeight(24)
        sep.setStyleSheet("background-color: #313244; border: none;")
        return sep

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.import_multiple_files_dialog)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._shortcut_find)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self._shortcut_bookmark)
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self.reader_view.zoom_in())
        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self.reader_view.zoom_in())
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.reader_view.zoom_out())
        QShortcut(QKeySequence("Escape"), self, activated=self._shortcut_escape)

    def _shortcut_find(self):
        if self.stack.currentIndex() == 1:
            self.reader_view.open_search_bar()

    def _shortcut_bookmark(self):
        if self.stack.currentIndex() == 1:
            self.reader_view.toggle_bookmark()

    def _shortcut_escape(self):
        if self.stack.currentIndex() == 1:
            if hasattr(self.reader_view, "search_bar") and self.reader_view.search_bar.isVisible():
                self.reader_view.search_bar.hide()
                self.reader_view.canvas.set_search_query("")
            else:
                self.show_library_view()

    # ---- TOOLBAR VISIBILITY & STATUS UPDATES ----

    def _set_library_controls_visible(self, visible: bool):
        self.btn_add_books.setVisible(visible)
        self.btn_import_folders.setVisible(visible)

    def _set_reader_controls_visible(self, visible: bool):
        for w in (
            self.btn_back_lib,
            self.sep_1,
            self.btn_toggle_sidebar,
            self.lbl_view,
            self.combo_view_mode,
            self.btn_prev_page,
            self.btn_next_page,
            self.lbl_theme,
            self.combo_theme,
            self.btn_zoom_out,
            self.btn_zoom_in,
            self.btn_bookmark,
            self.btn_find,
            self.btn_info,
            self.lbl_page_status,
        ):
            w.setVisible(visible)

    def _on_reader_status_updated(self, text: str):
        self.lbl_page_status.setText(text)
        self.lbl_page_status.setVisible(bool(text.strip()) and self.stack.currentIndex() == 1)

    # ---- VIEW SWITCHING ----

    def _refresh_library_grid(self):
        self.library_view.populate(
            self.store.books,
            self.width(),
            folders=self.store.get_folders(),
        )

    def show_library_view(self):
        self.reader_view.close_book()
        self.lbl_page_status.setText("")
        self._set_reader_controls_visible(False)
        self._set_library_controls_visible(True)
        self.setWindowTitle("Linux eBook & PDF Reader — LocalLibrary")
        if hasattr(self.store, "refresh_epub_covers_once"):
            self.store.refresh_epub_covers_once()
        self._refresh_library_grid()
        self.stack.setCurrentIndex(0)

    def open_book(self, filepath: str):
        if not filepath or not os.path.exists(filepath):
            QMessageBox.warning(
                self,
                "File Not Found",
                f"The selected book file could not be found on disk:\n{filepath}",
            )
            return

        self._set_library_controls_visible(False)
        self._set_reader_controls_visible(True)

        # Apply saved theme & view mode before loading
        self.reader_view.set_theme(self.combo_theme.currentText())
        self.reader_view.set_view_mode(self.combo_view_mode.currentText())

        self.stack.setCurrentIndex(1)
        self.reader_view.load_book(filepath)
        if hasattr(self.reader_view, "_emit_status"):
            self.reader_view._emit_status()

        book_info = self.store.books.get(filepath) or {}
        title = book_info.get("title") or Path(filepath).stem
        self.setWindowTitle(f"{title} — LocalLibrary")

    # ---- READER TOOLBAR ACTIONS ----

    def _on_toggle_sidebar(self):
        if hasattr(self.reader_view, "toggle_sidebar"):
            self.reader_view.toggle_sidebar()
        elif hasattr(self.reader_view, "sidebar_tabs"):
            vis = self.reader_view.sidebar_tabs.isVisible()
            self.reader_view.sidebar_tabs.setVisible(not vis)

    def _on_view_mode_changed(self, mode_text: str):
        self.store.save_settings(view_mode=mode_text)
        self.reader_view.set_view_mode(mode_text)

    def _on_theme_changed(self, theme_name: str):
        self.store.save_settings(theme_name=theme_name)
        self.reader_view.set_theme(theme_name)

    def _show_book_info_dialog(self):
        if hasattr(self.reader_view, "show_book_info"):
            self.reader_view.show_book_info()
            return

        book = getattr(self.reader_view, "current_book", None)
        if not book:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Book Metadata & File Information")
        dlg.setMinimumWidth(460)
        dlg.setStyleSheet("""
            QDialog { background-color: #181825; color: #CDD6F4; }
            QLabel { color: #CDD6F4; font-size: 13px; }
        """)
        form = QFormLayout(dlg)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(10)

        path_str = book.get("path", "")
        size_mb = (
            f"{os.path.getsize(path_str) / (1024 * 1024):.2f} MB"
            if path_str and os.path.exists(path_str)
            else "Unknown"
        )
        cur = book.get("current_page", 0) + 1
        tot = max(1, book.get("total_pages", 1))

        fields = [
            ("Title:", book.get("title", "Untitled")),
            ("Author:", book.get("author", "Unknown Author")),
            ("Category:", book.get("category") or "Uncategorized"),
            ("Format:", book.get("format", "Unknown")),
            ("Progress:", f"Page {cur} of {tot} ({int((cur / tot) * 100)}%)"),
            ("File Size:", size_mb),
            ("Location:", path_str),
        ]
        for label_txt, val_txt in fields:
            lbl_k = QLabel(label_txt)
            lbl_k.setStyleSheet("color: #89B4FA; font-weight: 700;")
            lbl_v = QLabel(str(val_txt))
            lbl_v.setWordWrap(True)
            lbl_v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(lbl_k, lbl_v)

        dlg.exec()

    # ---- VIRTUAL CATEGORY HANDLERS ----

    def _on_create_folder(self, folder_name: str):
        ok, msg = self.store.create_folder(folder_name)
        if not ok:
            QMessageBox.warning(self, "Category Error", msg)
        self._refresh_library_grid()

    def _on_rename_folder(self, old_name: str, new_name: str):
        ok, msg = self.store.rename_folder(old_name, new_name)
        if not ok:
            QMessageBox.warning(self, "Rename Error", msg)
        self._refresh_library_grid()

    def _on_delete_folder(self, folder_name: str):
        ok, msg = self.store.delete_folder(folder_name)
        if not ok:
            QMessageBox.warning(self, "Delete Error", msg)
        self._refresh_library_grid()

    def _on_move_book_to_folder(self, book_path: str, folder_name: str):
        ok, msg = self.store.move_book_to_folder(book_path, folder_name)
        if not ok:
            QMessageBox.warning(self, "Category Error", msg)
        self._refresh_library_grid()

    # ---- BOOK IMPORT & REMOVAL DIALOGS ----

    def import_multiple_files_dialog(self):
        exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add eBooks & PDFs to Library",
            str(Path.home()),
            f"Supported Books ({exts});;All Files (*)",
        )
        if not files:
            return
        added = 0
        for fpath in files:
            if Path(fpath).suffix.lower() in SUPPORTED_EXTENSIONS:
                self.store.add_or_update_book(fpath)
                added += 1
        if added:
            self._refresh_library_grid()

    def import_multiple_folders_dialog(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Scan for Books",
            str(Path.home()),
        )
        if not folder:
            return

        added = 0
        for root, _dirs, files in os.walk(folder):
            for fname in files:
                if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                    full_path = os.path.join(root, fname)
                    self.store.add_or_update_book(full_path)
                    added += 1

        self._refresh_library_grid()
        QMessageBox.information(
            self,
            "Folder Import Complete",
            f"Scanned '{folder}' and imported/updated {added} book(s).",
        )

    def remove_book_from_library(self, filepath: str):
        book = self.store.books.get(filepath) or {}
        title = book.get("title") or Path(filepath).name
        reply = QMessageBox.question(
            self,
            "Remove Book from Library",
            f"Remove '{title}' from your LocalLibrary list?\n\n"
            "(The actual file on your computer will not be deleted.)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.store.remove_book(filepath)
            self._refresh_library_grid()

    def closeEvent(self, event):
        self.reader_view.close_book()
        self.store.save()
        super().closeEvent(event)