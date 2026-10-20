import os
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QStackedWidget, QToolBar, QPushButton,
    QLabel, QComboBox, QFileDialog, QMessageBox, QListView, QTreeView,
    QAbstractItemView, QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QProgressDialog
)

from app.config import THEMES
from app.core.store import LibraryStore, SUPPORTED_EXTENSIONS
from app.ui.styles import GLOBAL_STYLESHEET
from app.ui.library_view import LibraryView
from app.ui.reader_view import ReaderView

from app.ui.dialogs import LookupPopupDialog, BookInfoDialog


class MultiFolderImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Books from Folders")
        self.resize(620, 380)
        self.folders: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        info_lbl = QLabel(
            "Add one or more folders from anywhere on your system.\n"
            "All PDF, EPUB, FB2, MOBI, CBZ, XPS, and TXT files inside them (including subfolders) will be imported."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #A6ADC8; font-size: 12px;")
        layout.addWidget(info_lbl)

        self.folder_list = QListWidget()
        self.folder_list.setStyleSheet(
            "QListWidget { background-color: #181825; border: 1px solid #313244; border-radius: 6px; padding: 6px; }"
        )
        layout.addWidget(self.folder_list, stretch=1)

        top_btns = QHBoxLayout()
        btn_add_native = QPushButton("+ Browse & Add Folder")
        btn_add_native.clicked.connect(self.add_single_folder_native)
        top_btns.addWidget(btn_add_native)

        btn_add_multi = QPushButton("+ Multi-Select Folders (Ctrl/Shift)")
        btn_add_multi.clicked.connect(self.add_multiple_folders_qt)
        top_btns.addWidget(btn_add_multi)

        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self.remove_selected_folder)
        top_btns.addWidget(btn_remove)

        top_btns.addStretch(1)
        layout.addLayout(top_btns)

        bot_btns = QHBoxLayout()
        bot_btns.addStretch(1)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        bot_btns.addWidget(btn_cancel)

        self.btn_import = QPushButton("Scan & Import Books (0 folders)")
        self.btn_import.setEnabled(False)
        self.btn_import.setStyleSheet(
            "QPushButton:enabled { background-color: #89B4FA; color: #11111B; font-weight: bold; }"
        )
        self.btn_import.clicked.connect(self.accept)
        bot_btns.addWidget(self.btn_import)

        layout.addLayout(bot_btns)

    def _add_paths(self, paths: list[str]):
        for p in paths:
            norm = os.path.abspath(p)
            if os.path.isdir(norm) and norm not in self.folders:
                self.folders.append(norm)
                self.folder_list.addItem(norm)
        self.btn_import.setEnabled(len(self.folders) > 0)
        self.btn_import.setText(f"Scan & Import Books ({len(self.folders)} folder(s))")

    def add_single_folder_native(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder to Add", str(Path.home()), QFileDialog.ShowDirsOnly
        )
        if folder:
            self._add_paths([folder])

    def add_multiple_folders_qt(self):
        dlg = QFileDialog(self, "Hold Ctrl or Shift to Select Multiple Folders", str(Path.home()))
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)
        dlg.setFileMode(QFileDialog.Directory)
        dlg.setOption(QFileDialog.ShowDirsOnly, True)

        for view in dlg.findChildren((QListView, QTreeView)):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        if dlg.exec():
            self._add_paths(dlg.selectedFiles())

    def remove_selected_folder(self):
        row = self.folder_list.currentRow()
        if 0 <= row < len(self.folders):
            self.folders.pop(row)
            self.folder_list.takeItem(row)
            self.btn_import.setEnabled(len(self.folders) > 0)
            self.btn_import.setText(f"Scan & Import Books ({len(self.folders)} folder(s))")


class LocalLibraryWindow(QMainWindow):
    """Main application window coordinating context-aware toolbar actions."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Linux eBook & PDF Reader")
        self.resize(1280, 860)
        self.setStyleSheet(GLOBAL_STYLESHEET)

        self.store = LibraryStore()
        self.library_view = LibraryView()
        self.reader_view = ReaderView(self.store)

        self.library_only_actions = []
        self.reader_only_actions = []

        self._build_toolbar()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.library_view)
        self.stack.addWidget(self.reader_view)
        self.setCentralWidget(self.stack)

        self.library_view.book_selected.connect(self.open_book)
        self.library_view.book_remove_requested.connect(self.remove_book_from_library)
        self.library_view.add_books_requested.connect(self.import_multiple_files_dialog)
        self.library_view.import_folders_requested.connect(self.import_multiple_folders_dialog)
        self.reader_view.progress_updated.connect(self.lbl_page_status.setText)

        self.show_library_view()

    def _build_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # ---- Library-Only Actions (Shown ONLY on Library Home) ----
        btn_add_files = QPushButton("+ Add Books")
        btn_add_files.setToolTip("Select one or multiple eBook/PDF files to import")
        btn_add_files.clicked.connect(self.import_multiple_files_dialog)
        act_files = toolbar.addWidget(btn_add_files)

        btn_add_folders = QPushButton("+ Import Folders")
        btn_add_folders.setToolTip("Import all supported books from one or multiple folders")
        btn_add_folders.clicked.connect(self.import_multiple_folders_dialog)
        act_folders = toolbar.addWidget(btn_add_folders)

        sep1 = toolbar.addSeparator()

        self.library_only_actions = [act_files, act_folders, sep1]

        # ---- Reader-Only Actions (Shown ONLY when reading a book) ----
        btn_home = QPushButton("Library Home")
        btn_home.setToolTip("Close current book and return to Library Home")
        btn_home.clicked.connect(self.show_library_view)
        act_home = toolbar.addWidget(btn_home)

        sep2 = toolbar.addSeparator()

        btn_sidebar = QPushButton("Sidebar")
        btn_sidebar.setToolTip("Toggle Contents / Bookmarks / Highlights / Search Sidebar")
        btn_sidebar.clicked.connect(self.reader_view.toggle_sidebar)
        act_sidebar = toolbar.addWidget(btn_sidebar)

        lbl_view = QLabel("  View:")
        act_lbl_view = toolbar.addWidget(lbl_view)

        self.combo_view_mode = QComboBox()
        self.combo_view_mode.addItems([
            "Continuous Scroll",
            "Single Page",
            "Two-Column Spread",
        ])
        self.combo_view_mode.setToolTip("Switch between Continuous, Single, and Two-Column Spread")
        self.combo_view_mode.currentTextChanged.connect(self.reader_view.set_view_mode)
        act_view_mode = toolbar.addWidget(self.combo_view_mode)

        btn_prev_page = QPushButton("◀")
        btn_prev_page.setToolTip("Previous Page / Spread (Left Arrow / PageUp)")
        btn_prev_page.setFixedWidth(34)
        btn_prev_page.clicked.connect(self.reader_view.step_prev_page)
        act_prev_p = toolbar.addWidget(btn_prev_page)

        btn_next_page = QPushButton("▶")
        btn_next_page.setToolTip("Next Page / Spread (Right Arrow / PageDown / Space)")
        btn_next_page.setFixedWidth(34)
        btn_next_page.clicked.connect(self.reader_view.step_next_page)
        act_next_p = toolbar.addWidget(btn_next_page)

        lbl_theme = QLabel("  Theme:")
        act_lbl_theme = toolbar.addWidget(lbl_theme)

        self.combo_theme = QComboBox()
        self.combo_theme.addItems(list(THEMES.keys()))
        self.combo_theme.setCurrentText(self.reader_view.theme_name)
        self.combo_theme.currentTextChanged.connect(self.reader_view.set_theme)
        act_theme = toolbar.addWidget(self.combo_theme)

        btn_zoom_out = QPushButton("A-")
        btn_zoom_out.setToolTip("Zoom Out")
        btn_zoom_out.clicked.connect(lambda: self.reader_view.adjust_zoom(-0.15))
        act_zout = toolbar.addWidget(btn_zoom_out)

        btn_zoom_in = QPushButton("A+")
        btn_zoom_in.setToolTip("Zoom In")
        btn_zoom_in.clicked.connect(lambda: self.reader_view.adjust_zoom(+0.15))
        act_zin = toolbar.addWidget(btn_zoom_in)

        btn_bookmark = QPushButton("Bookmark")
        btn_bookmark.setToolTip("Toggle Bookmark on Current Page")
        btn_bookmark.clicked.connect(self.reader_view.toggle_bookmark)
        act_bm = toolbar.addWidget(btn_bookmark)

        btn_find = QPushButton("Find (Ctrl+F)")
        btn_find.setToolTip("Search text inside the book (Ctrl+F)")
        btn_find.clicked.connect(self.reader_view.open_search_bar)
        act_find = toolbar.addWidget(btn_find)

        btn_info = QPushButton("Info")
        btn_info.setToolTip("View Reading Statistics & Book Metadata")
        btn_info.clicked.connect(self.reader_view.open_book_info_dialog)
        act_info = toolbar.addWidget(btn_info)

        self.lbl_page_status = QLabel("")
        act_status = toolbar.addWidget(self.lbl_page_status)

        self.reader_only_actions = [
            act_home, sep2, act_sidebar, act_lbl_view, act_view_mode, act_prev_p, act_next_p,
            act_lbl_theme, act_theme, act_zout, act_zin, act_bm, act_find, act_info, act_status,
        ]

    def _set_library_controls_visible(self, visible: bool):
        for act in self.library_only_actions:
            act.setVisible(visible)

    def _set_reader_controls_visible(self, visible: bool):
        for act in self.reader_only_actions:
            act.setVisible(visible)

    def show_library_view(self):
        self.reader_view.close_book()
        self.lbl_page_status.setText("")
        self._set_reader_controls_visible(False)
        self._set_library_controls_visible(True)
        self.setWindowTitle("Linux eBook & PDF Reader")
        self.library_view.populate(self.store.books, self.width())
        self.stack.setCurrentIndex(0)

    def import_multiple_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Books to Import",
            str(Path.home()),
            "eBooks & Documents (*.pdf *.epub *.fb2 *.mobi *.cbz *.xps *.txt)",
        )
        if not paths:
            return

        self._batch_import_files(paths)
        if len(paths) == 1:
            self.open_book(paths[0])
        else:
            self.show_library_view()

    def import_multiple_folders_dialog(self):
        dlg = MultiFolderImportDialog(self)
        if not dlg.exec() or not dlg.folders:
            return

        found_files: list[str] = []
        seen: set[str] = set()
        for folder in dlg.folders:
            for root_dir, _dirs, files in os.walk(folder, followlinks=True):
                for fname in files:
                    if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                        full_path = os.path.abspath(os.path.join(root_dir, fname))
                        if full_path not in seen:
                            seen.add(full_path)
                            found_files.append(full_path)

        if not found_files:
            QMessageBox.information(
                self,
                "No Supported Books Found",
                "No supported eBook or document files were found in the selected folder(s).",
            )
            return

        imported_count = self._batch_import_files(found_files)
        self.show_library_view()
        QMessageBox.information(
            self,
            "Folder Import Complete",
            f"Successfully imported {imported_count} book(s) from {len(dlg.folders)} folder(s).",
        )

    def _batch_import_files(self, filepaths: list[str]) -> int:
        progress = QProgressDialog("Importing books into library...", "Cancel", 0, len(filepaths), self)
        progress.setWindowTitle("Importing Books")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        imported = 0
        for idx, fpath in enumerate(filepaths):
            if progress.wasCanceled():
                break
            progress.setLabelText(f"Importing ({idx + 1}/{len(filepaths)}):\n{Path(fpath).name}")
            progress.setValue(idx)
            QApplication.processEvents()
            if self.store.add_or_update_book(fpath):
                imported += 1

        progress.setValue(len(filepaths))
        return imported

    def remove_book_from_library(self, filepath: str):
        self.store.remove_book(filepath)
        self.library_view.populate(self.store.books, self.width())

    def open_book(self, filepath: str):
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "File Not Found", f"Could not find:\n{filepath}")
            return
        self._set_library_controls_visible(False)
        self._set_reader_controls_visible(True)
        self.reader_view.load_book(filepath)
        book_title = (
            self.reader_view.current_book.get("title", "Book")
            if self.reader_view.current_book
            else "Book"
        )
        self.setWindowTitle(f"{book_title}")
        self.stack.setCurrentIndex(1)