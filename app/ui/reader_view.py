from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt, QTimer, Signal, QPointF, QEvent
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem,
    QFrame, QLabel, QComboBox, QPushButton, QLineEdit, QSlider,
    QMenu, QFileDialog, QMessageBox
)

from app.config import DEFAULT_EPUB_FONTSIZE, TTS_VOICES, HIGHLIGHT_COLORS
from app.core.epub_loader import open_clean_document
from app.core.tts_worker import NeuralTTSWorker, TTSUnit, build_page_units
from app.ui.widgets import DocumentCanvasView
from app.ui.dialogs import (
    LookupPopupDialog,
    BookInfoDialog,
    AnnotationNoteDialog,
    export_book_notes_to_markdown,
)

class ReaderView(QWidget):
    """Document reader with Evince-style Search Bar (Ctrl+F), Drag-to-Highlight, Spread Modes, and Neural TTS."""

    progress_updated = Signal(str)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.doc: pymupdf.Document | None = None
        self.current_book: dict | None = None
        self.epub_fontsize = DEFAULT_EPUB_FONTSIZE
        self.theme_name = "Smart Dark (Mocha)"

        self.search_results: list[tuple[int, pymupdf.Rect, str]] = []
        self.current_search_idx: int = -1

        self.tts_worker: NeuralTTSWorker | None = None
        self._build_ui()
        self._setup_shortcuts()

        self._reading_timer = QTimer(self)
        self._reading_timer.setInterval(15000)  # Every 15 seconds while book is open
        self._reading_timer.timeout.connect(self._record_reading_heartbeat)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- EVINCE-STYLE TOP SLIDING SEARCH BAR (Ctrl+F) ----
        self.search_bar = QFrame()
        self.search_bar.setStyleSheet("""
            QFrame {
                background-color: #1E1E2E;
                border-bottom: 1px solid #45475A;
                padding: 4px 10px;
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
        sb_layout = QHBoxLayout(self.search_bar)
        sb_layout.setContentsMargins(6, 2, 6, 2)
        sb_layout.setSpacing(8)

        sb_layout.addWidget(QLabel("Find:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in document (Enter = Next, Shift+Enter = Previous, Esc = Close)...")
        self.search_input.setMinimumWidth(320)
        self.search_input.textChanged.connect(lambda _: self._search_timer.start())
        self.search_input.installEventFilter(self)
        sb_layout.addWidget(self.search_input)

        self.lbl_search_count = QLabel("0 matches")
        self.lbl_search_count.setMinimumWidth(95)
        self.lbl_search_count.setStyleSheet("color: #A6ADC8; font-size: 12px;")
        sb_layout.addWidget(self.lbl_search_count)

        btn_prev_match = QPushButton("▲")
        btn_prev_match.setToolTip("Previous Match (Shift+Enter)")
        btn_prev_match.setFixedWidth(36)
        btn_prev_match.clicked.connect(self.find_prev)
        sb_layout.addWidget(btn_prev_match)

        btn_next_match = QPushButton("▼")
        btn_next_match.setToolTip("Next Match (Enter)")
        btn_next_match.setFixedWidth(36)
        btn_next_match.clicked.connect(self.find_next)
        sb_layout.addWidget(btn_next_match)

        sb_layout.addStretch(1)

        btn_close_search = QPushButton("✕")
        btn_close_search.setToolTip("Close Search Bar (Esc)")
        btn_close_search.setFixedWidth(34)
        btn_close_search.clicked.connect(self.close_search_bar)
        sb_layout.addWidget(btn_close_search)

        self.search_bar.hide()
        root.addWidget(self.search_bar)

        # ---- MAIN SPLITTER: SIDEBAR + DOCUMENT CANVAS ----
        self.splitter = QSplitter(Qt.Horizontal)

        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setMinimumWidth(280)
        self.sidebar_tabs.setMaximumWidth(380)

        self.toc_tree = QTreeWidget()
        self.toc_tree.setHeaderHidden(True)
        self.toc_tree.itemClicked.connect(self._on_toc_clicked)
        self.sidebar_tabs.addTab(self.toc_tree, "Contents")

        self.bookmarks_list = QListWidget()
        self.bookmarks_list.itemClicked.connect(self._on_bookmark_clicked)
        self.sidebar_tabs.addTab(self.bookmarks_list, "Bookmarks")

        # Highlights Tab with bottom "Export to Markdown (.md)" button
        hl_tab = QWidget()
        hl_layout = QVBoxLayout(hl_tab)
        hl_layout.setContentsMargins(0, 0, 0, 0)
        hl_layout.setSpacing(4)

        self.highlights_list = QListWidget()
        self.highlights_list.itemClicked.connect(self._on_highlight_clicked)
        self.highlights_list.itemDoubleClicked.connect(self._on_highlight_double_clicked)
        self.highlights_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.highlights_list.customContextMenuRequested.connect(self._on_highlights_sidebar_menu)
        hl_layout.addWidget(self.highlights_list, stretch=1)

        btn_export_md = QPushButton("⬇ Export Highlights & Notes (.md)")
        btn_export_md.setToolTip("Export all bookmarks, highlights, and notes to a Markdown file")
        btn_export_md.setStyleSheet("margin: 4px 6px 6px 6px; padding: 6px;")
        btn_export_md.clicked.connect(self.export_annotations_markdown)
        hl_layout.addWidget(btn_export_md)

        self.sidebar_tabs.addTab(hl_tab, "Highlights")

        self.search_list = QListWidget()
        self.search_list.itemClicked.connect(self._on_search_result_clicked)
        self.sidebar_tabs.addTab(self.search_list, "Search")

        self.splitter.addWidget(self.sidebar_tabs)

        # Center Virtualized Document Canvas
        self.canvas = DocumentCanvasView()
        self.canvas.page_changed.connect(self._on_canvas_page_changed)
        self.canvas.highlight_added.connect(self._on_canvas_highlight_added)
        self.canvas.highlight_deleted.connect(self._delete_highlight_by_index)
        self.canvas.highlight_color_changed.connect(self._change_highlight_color_by_index)
        self.canvas.tts_requested_at_page.connect(self._start_tts_from_selection)
        self.canvas.lookup_requested.connect(self.open_lookup_dialog)
        self.canvas.note_requested_for_selection.connect(self._on_canvas_note_for_selection)
        self.canvas.edit_highlight_note_requested.connect(self._edit_highlight_note_by_index)
        self.canvas.tts_requested_at_page.connect(self._start_tts_from_selection)

        self.splitter.addWidget(self.canvas)
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, stretch=1)

        # ---- BOTTOM STATEFUL NEURAL TTS CONTROL BAR ----
        tts_bar = QFrame()
        tts_bar.setStyleSheet(
            "background-color: #181825; border-top: 1px solid #313244; padding: 6px 12px;"
        )
        tts_bar_layout = QVBoxLayout(tts_bar)
        tts_bar_layout.setContentsMargins(4, 2, 4, 2)
        tts_bar_layout.setSpacing(4)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        controls_row.addWidget(QLabel("Voice:"))
        self.combo_voice = QComboBox()
        self.combo_voice.addItems(TTS_VOICES)
        self.combo_voice.setToolTip("Select Neural TTS Voice")
        self.combo_voice.currentTextChanged.connect(self._on_voice_changed)
        controls_row.addWidget(self.combo_voice)

        icon_btn_style = (
            "QPushButton { padding: 0px; font-size: 15px; min-width: 36px; max-width: 36px; "
            "min-height: 30px; max-height: 30px; }"
        )

        self.btn_prev_para = QPushButton("⏮")
        self.btn_prev_para.setStyleSheet(icon_btn_style)
        self.btn_prev_para.setToolTip("Previous Paragraph")
        self.btn_prev_para.clicked.connect(self.tts_prev_paragraph)
        controls_row.addWidget(self.btn_prev_para)

        self.btn_tts_play_pause = QPushButton("▶")
        self.btn_tts_play_pause.setStyleSheet(icon_btn_style)
        self.btn_tts_play_pause.setToolTip("Play / Resume TTS")
        self.btn_tts_play_pause.clicked.connect(self.toggle_tts_play_pause)
        controls_row.addWidget(self.btn_tts_play_pause)

        self.btn_tts_stop = QPushButton("⏹")
        self.btn_tts_stop.setStyleSheet(icon_btn_style)
        self.btn_tts_stop.setToolTip("Stop TTS and Clear Highlight")
        self.btn_tts_stop.clicked.connect(self.stop_tts)
        controls_row.addWidget(self.btn_tts_stop)

        self.btn_next_para = QPushButton("⏭")
        self.btn_next_para.setStyleSheet(icon_btn_style)
        self.btn_next_para.setToolTip("Next Paragraph")
        self.btn_next_para.clicked.connect(self.tts_next_paragraph)
        controls_row.addWidget(self.btn_next_para)

        controls_row.addSpacing(10)

        controls_row.addWidget(QLabel("Speed:"))
        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setRange(5, 20)
        self.slider_speed.setValue(10)
        self.slider_speed.setFixedWidth(130)
        self.slider_speed.setToolTip("Adjust Speaking Speed (0.5x to 2.0x)")
        self.slider_speed.valueChanged.connect(self._on_speed_changed)
        controls_row.addWidget(self.slider_speed)

        self.lbl_speed_val = QLabel("1.0x")
        self.lbl_speed_val.setMinimumWidth(54)
        self.lbl_speed_val.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_speed_val.setStyleSheet("color: #89B4FA; font-weight: bold; padding-left: 2px;")
        controls_row.addWidget(self.lbl_speed_val)

        controls_row.addSpacing(6)

        controls_row.addWidget(QLabel("Pitch:"))
        self.slider_pitch = QSlider(Qt.Horizontal)
        self.slider_pitch.setRange(-6, 6)
        self.slider_pitch.setValue(0)
        self.slider_pitch.setFixedWidth(130)
        self.slider_pitch.setToolTip("Adjust Voice Pitch (-6 to +6 semitones)")
        self.slider_pitch.valueChanged.connect(self._on_pitch_changed)
        controls_row.addWidget(self.slider_pitch)

        self.lbl_pitch_val = QLabel("0st")
        self.lbl_pitch_val.setMinimumWidth(54)
        self.lbl_pitch_val.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_pitch_val.setStyleSheet("color: #89B4FA; font-weight: bold; padding-left: 2px;")
        controls_row.addWidget(self.lbl_pitch_val)

        controls_row.addStretch(1)
        tts_bar_layout.addLayout(controls_row)

        self.lbl_tts_status = QLabel("TTS Ready — Drag over any text on the page to Copy or Highlight")
        self.lbl_tts_status.setStyleSheet("color: #A6E3A1; font-size: 12px;")
        tts_bar_layout.addWidget(self.lbl_tts_status)

        root.addWidget(tts_bar)

    def _setup_shortcuts(self):
        self.shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self.shortcut_find.activated.connect(self.open_search_bar)

        self.shortcut_find_next = QShortcut(QKeySequence("Ctrl+G"), self)
        self.shortcut_find_next.activated.connect(self.find_next)

        self.shortcut_find_prev = QShortcut(QKeySequence("Ctrl+Shift+G"), self)
        self.shortcut_find_prev.activated.connect(self.find_prev)

    def eventFilter(self, obj, event):
        if obj is self.search_input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self.find_prev()
                else:
                    self.find_next()
                return True
            elif event.key() == Qt.Key_Escape:
                self.close_search_bar()
                return True
        return super().eventFilter(obj, event)

    # -----------------------------------------------------------------
    # IN-BOOK SEARCH (Ctrl+F, Next/Prev Match & Sidebar Snippets)
    # -----------------------------------------------------------------
    def open_search_bar(self):
        if not self.doc:
            return
        self.search_bar.show()
        self.search_input.setFocus()
        self.search_input.selectAll()
        if self.search_input.text().strip():
            self._execute_search()

    def close_search_bar(self):
        self.search_bar.hide()
        self.canvas.clear_search_overlays()
        self.canvas.setFocus()

    def _execute_search(self):
        self.search_results.clear()
        self.search_list.clear()
        self.current_search_idx = -1

        query = self.search_input.text().strip()
        if not self.doc or len(query) < 2:
            self.lbl_search_count.setText("0 matches")
            self.canvas.clear_search_overlays()
            return

        matches_by_page: dict[int, list[pymupdf.Rect]] = {}
        q_lower = query.lower()

        for page_idx in range(len(self.doc)):
            page = self.doc.load_page(page_idx)
            rects = page.search_for(query)
            if not rects:
                continue

            matches_by_page[page_idx] = rects
            page_text = " ".join(page.get_text("text").split())
            pt_lower = page_text.lower()
            search_pos = 0

            for r in rects:
                pos = pt_lower.find(q_lower, search_pos)
                if pos != -1:
                    start = max(0, pos - 30)
                    end = min(len(page_text), pos + len(query) + 40)
                    snippet = ("..." if start > 0 else "") + page_text[start:end] + ("..." if end < len(page_text) else "")
                    search_pos = pos + len(query)
                else:
                    snippet = query

                match_idx = len(self.search_results)
                self.search_results.append((page_idx, r, snippet))

                item = QListWidgetItem(f"[p. {page_idx + 1}]  {snippet}")
                item.setData(Qt.UserRole, match_idx)
                self.search_list.addItem(item)

        if not self.search_results:
            self.lbl_search_count.setText("No matches")
            self.lbl_search_count.setStyleSheet("color: #F38BA8; font-size: 12px;")
            self.canvas.clear_search_overlays()
            return

        self.lbl_search_count.setStyleSheet("color: #A6E3A1; font-size: 12px;")
        cur_p = self.current_book.get("current_page", 0) if self.current_book else 0
        first_idx = 0
        for idx, (p_idx, _, _) in enumerate(self.search_results):
            if p_idx >= cur_p:
                first_idx = idx
                break

        self.sidebar_tabs.setCurrentWidget(self.search_list)
        self._focus_search_match(first_idx, matches_by_page)

    def _focus_search_match(
        self,
        match_idx: int,
        matches_by_page: dict[int, list[pymupdf.Rect]] | None = None,
    ):
        if not self.search_results:
            return
        self.current_search_idx = match_idx % len(self.search_results)
        page_idx, rect, _ = self.search_results[self.current_search_idx]

        if matches_by_page is None:
            matches_by_page = self.canvas.search_matches_by_page

        self.lbl_search_count.setText(
            f"{self.current_search_idx + 1} of {len(self.search_results)}"
        )
        self.search_list.setCurrentRow(self.current_search_idx)
        self.canvas.set_search_overlays(matches_by_page, (page_idx, rect))
        self.canvas.ensure_page_rect_visible(page_idx, rect)

    def find_next(self):
        if self.search_results:
            self._focus_search_match(self.current_search_idx + 1)

    def find_prev(self):
        if self.search_results:
            self._focus_search_match(self.current_search_idx - 1)

    def _on_search_result_clicked(self, item: QListWidgetItem):
        idx = item.data(Qt.UserRole)
        if idx is not None:
            self._focus_search_match(int(idx))

    # -----------------------------------------------------------------
    # BOOK LOADING, VIEW MODES & CANVAS COORDINATION
    # -----------------------------------------------------------------
    def load_book(self, filepath: str):
        self.stop_tts()
        self.close_search_bar()
        if self.doc:
            self.doc.close()

        self.doc = open_clean_document(filepath, fontsize=self.epub_fontsize)
        self.current_book = self.store.add_or_update_book(filepath, open_doc=self.doc)
        self.store.touch_last_opened(filepath)

        tts_state = self.current_book.get("tts_state", {})
        saved_speed = float(tts_state.get("speed", 1.0))
        saved_pitch = int(tts_state.get("pitch", 0))
        self.slider_speed.setValue(int(round(saved_speed * 10)))
        self.slider_pitch.setValue(saved_pitch)

        self._populate_toc()
        self._populate_bookmarks()
        self._populate_highlights()

        self.canvas.set_document(self.doc, self.current_book.get("highlights", []))
        self._reading_timer.start()

        saved_page = min(self.current_book.get("current_page", 0), len(self.doc) - 1)
        QTimer.singleShot(40, lambda: self.jump_to_page(saved_page))

    def close_book(self):
        """Closes the active document, stops TTS, and clears reader state when returning Home."""
        self.stop_tts()
        self.close_search_bar()
        if self.doc:
            try:
                self.doc.close()
            except Exception:
                pass
            self.doc = None
        self.current_book = None
        self.toc_tree.clear()
        self.bookmarks_list.clear()
        self.highlights_list.clear()
        self.search_list.clear()
        self.canvas.set_document(None, [])
        self.progress_updated.emit("")

    def _record_reading_heartbeat(self):
        if self.doc and self.current_book and self.isVisible():
            self.store.add_reading_time(self.current_book["path"], 15)
            self.current_book["reading_seconds"] = (
                int(self.current_book.get("reading_seconds", 0)) + 15
            )

    def open_lookup_dialog(self, selected_text: str):
        if selected_text.strip():
            dlg = LookupPopupDialog(selected_text, self)
            dlg.exec()

    def open_book_info_dialog(self):
        if self.doc and self.current_book:
            dlg = BookInfoDialog(self.doc, self.current_book, self.store, self)
            dlg.exec()

    def set_view_mode(self, mode_label: str):
        mapping = {
            "Continuous Scroll": "continuous",
            "Single Page": "single",
            "Two-Column Spread": "spread",
        }
        mode = mapping.get(mode_label, "continuous")
        self.canvas.set_view_mode(mode)
        self.canvas.setFocus()

    def step_prev_page(self):
        self.canvas.step_prev()

    def step_next_page(self):
        self.canvas.step_Next()

    def _on_canvas_page_changed(self, current_page: int, total_pages: int, zoom: float):
        if self.current_book:
            self.current_book["current_page"] = current_page
            self.current_book["total_pages"] = total_pages
            self.store.save()

            pages = self.canvas.get_active_paginated_pages()
            last_visible_page = pages[-1] if pages else current_page
            pct = int(((last_visible_page + 1) / max(1, total_pages)) * 100)

            if self.canvas.view_mode == "spread" and len(pages) == 2:
                page_str = f"Pages {pages[0] + 1}–{pages[1] + 1} / {total_pages}"
            else:
                page_str = f"Page {current_page + 1} / {total_pages}"

            self.progress_updated.emit(
                f"  {page_str} ({pct}%)  •  Zoom {int(zoom * 100)}%  "
            )
            # Visually sync the Contents (TOC) sidebar to the current scrolled page
            self._sync_toc_to_page(last_visible_page)

    def jump_to_page(self, page_index: int):
        self.canvas.jump_to_page(page_index)

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        self.canvas.theme_name = theme_name
        self.canvas.invalidate_all()

    def adjust_zoom(self, delta: float):
        if not self.doc:
            return
        vp = self.canvas.viewport()
        center_pt = QPointF(vp.width() / 2.0, vp.height() / 2.0)
        factor = (self.canvas.zoom + delta) / self.canvas.zoom
        self.canvas.apply_zoom_factor(factor, center_pt)

    def toggle_sidebar(self):
        self.sidebar_tabs.setVisible(not self.sidebar_tabs.isVisible())

    # -----------------------------------------------------------------
    # TOC, BOOKMARKS & MULTI-COLOR HIGHLIGHTS
    # -----------------------------------------------------------------
    def _populate_toc(self):
        self.toc_tree.clear()
        self._toc_items: list[tuple[int, QTreeWidgetItem]] = []
        if not self.doc:
            return
        toc = self.doc.get_toc()
        stack = [(0, self.toc_tree.invisibleRootItem())]
        for lvl, title, page_num in toc:
            page_idx = max(0, page_num - 1)
            item = QTreeWidgetItem([f"{title}  (p. {page_num})"])
            item.setData(0, Qt.UserRole, page_idx)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            parent = stack[-1][1] if stack else self.toc_tree.invisibleRootItem()
            parent.addChild(item)
            stack.append((lvl, item))
            self._toc_items.append((page_idx, item))
        self.toc_tree.expandToDepth(1)

    def _sync_toc_to_page(self, current_page_idx: int):
        """Highlights and scrolls to the active chapter/section in the Contents tree while scrolling."""
        if not getattr(self, "_toc_items", None):
            return

        active_item: QTreeWidgetItem | None = None
        for toc_page, item in self._toc_items:
            if toc_page <= current_page_idx:
                active_item = item
            else:
                break

        if active_item is None:
            active_item = self._toc_items[0][1]

        if self.toc_tree.currentItem() is active_item:
            return

        # Block signals briefly so programmatically selecting the item doesn't trigger a page jump
        self.toc_tree.blockSignals(True)
        for _p, item in self._toc_items:
            font = item.font(0)
            is_curr = item is active_item
            font.setBold(is_curr)
            item.setFont(0, font)
            item.setForeground(0, QColor("#89B4FA" if is_curr else "#CDD6F4"))

        # Expand parent nodes so the active subsection is visible
        parent = active_item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()

        self.toc_tree.setCurrentItem(active_item)
        self.toc_tree.scrollToItem(active_item, QTreeWidget.PositionAtCenter)
        self.toc_tree.blockSignals(False)

    def _on_toc_clicked(self, item: QTreeWidgetItem, _col: int):
        page_idx = item.data(0, Qt.UserRole)
        if page_idx is not None:
            self.jump_to_page(int(page_idx))

    def toggle_bookmark(self):
        if not self.current_book:
            return
        cur = self.current_book.get("current_page", 0)
        bms = self.current_book.setdefault("bookmarks", [])
        if cur in bms:
            bms.remove(cur)
        else:
            bms.append(cur)
            bms.sort()
        self.store.save()
        self._populate_bookmarks()

    def _populate_bookmarks(self):
        self.bookmarks_list.clear()
        if not self.current_book:
            return
        for p in self.current_book.get("bookmarks", []):
            item = QListWidgetItem(f"Bookmark — Page {p + 1}")
            item.setData(Qt.UserRole, p)
            self.bookmarks_list.addItem(item)

    def _on_bookmark_clicked(self, item: QListWidgetItem):
        self.jump_to_page(int(item.data(Qt.UserRole)))

    def _on_canvas_highlight_added(self, page_idx: int, text: str, color_key: str, rects: list):
        if not self.current_book:
            return
        self.current_book.setdefault("highlights", []).append({
            "page": page_idx,
            "text": text,
            "color": color_key,
            "note": "",
            "rects": rects,
        })
        self.store.save()
        self._populate_highlights()
        self.canvas.book_highlights = self.current_book["highlights"]
        self.canvas.invalidate_page(page_idx)

    def _delete_highlight_by_index(self, idx: int):
        if not self.current_book:
            return
        hls = self.current_book.get("highlights", [])
        if 0 <= idx < len(hls):
            page_idx = hls[idx].get("page", 0)
            hls.pop(idx)
            self.store.save()
            self._populate_highlights()
            self.canvas.book_highlights = hls
            self.canvas.invalidate_page(page_idx)

    def _change_highlight_color_by_index(self, idx: int, new_color: str):
        if not self.current_book:
            return
        hls = self.current_book.get("highlights", [])
        if 0 <= idx < len(hls):
            hls[idx]["color"] = new_color
            page_idx = hls[idx].get("page", 0)
            self.store.save()
            self._populate_highlights()
            self.canvas.book_highlights = hls
            self.canvas.invalidate_page(page_idx)

    def _on_canvas_note_for_selection(self, page_idx: int, text: str, rects: list):
        if not self.current_book:
            return
        dlg = AnnotationNoteDialog(
            passage_text=text,
            initial_note="",
            initial_color="yellow",
            page_num=page_idx + 1,
            parent=self,
        )
        if dlg.exec() and not dlg.delete_requested:
            self.current_book.setdefault("highlights", []).append({
                "page": page_idx,
                "text": text,
                "color": dlg.selected_color,
                "note": dlg.note_text,
                "rects": rects,
            })
            self.store.save()
            self._populate_highlights()
            self.canvas.book_highlights = self.current_book["highlights"]
            self.canvas.invalidate_page(page_idx)

    def _edit_highlight_note_by_index(self, idx: int):
        if not self.current_book:
            return
        hls = self.current_book.get("highlights", [])
        if not (0 <= idx < len(hls)):
            return
        h = hls[idx]
        page_idx = h.get("page", 0)
        dlg = AnnotationNoteDialog(
            passage_text=h.get("text", ""),
            initial_note=h.get("note", ""),
            initial_color=h.get("color", "yellow"),
            page_num=page_idx + 1,
            parent=self,
        )
        if dlg.exec():
            if dlg.delete_requested:
                self._delete_highlight_by_index(idx)
            else:
                h["color"] = dlg.selected_color
                h["note"] = dlg.note_text
                self.store.save()
                self._populate_highlights()
                self.canvas.book_highlights = hls
                self.canvas.invalidate_page(page_idx)

    def _populate_highlights(self):
        self.highlights_list.clear()
        if not self.current_book:
            return
        for idx, h in enumerate(self.current_book.get("highlights", [])):
            color_key = h.get("color", "yellow")
            hex_col = HIGHLIGHT_COLORS.get(color_key, HIGHLIGHT_COLORS["yellow"])["hex"]
            snippet = " ".join(h.get("text", "").split())
            if len(snippet) > 58:
                snippet = snippet[:55] + "..."
            note = (h.get("note") or "").strip()
            label = f"● [p. {h['page'] + 1}] “{snippet}”"
            if note:
                note_preview = note.replace("\n", " ")
                if len(note_preview) > 48:
                    note_preview = note_preview[:45] + "..."
                label += f"\n   {note_preview}"

            item = QListWidgetItem(label)
            item.setForeground(QColor(hex_col))
            item.setToolTip(
                f"Page {h['page'] + 1}\n\nHighlight:\n“{h.get('text', '')}”"
                + (f"\n\nNote:\n{note}" if note else "\n\n(Double-click to add a note)")
            )
            item.setData(Qt.UserRole, h["page"])
            item.setData(Qt.UserRole + 1, idx)
            self.highlights_list.addItem(item)

    def _on_highlight_clicked(self, item: QListWidgetItem):
        self.jump_to_page(int(item.data(Qt.UserRole)))

    def _on_highlight_double_clicked(self, item: QListWidgetItem):
        hl_idx = item.data(Qt.UserRole + 1)
        if hl_idx is not None:
            self._edit_highlight_note_by_index(int(hl_idx))

    def _on_highlights_sidebar_menu(self, pos):
        item = self.highlights_list.itemAt(pos)
        if not item:
            return
        hl_idx = int(item.data(Qt.UserRole + 1))
        menu = QMenu(self)
        act_note = menu.addAction("Add / Edit Note...")
        act_note.triggered.connect(lambda: self._edit_highlight_note_by_index(hl_idx))
        menu.addSeparator()
        for key, info in HIGHLIGHT_COLORS.items():
            act_c = menu.addAction(f"Color: {info['label']}")
            act_c.triggered.connect(
                lambda _=False, i=hl_idx, k=key: self._change_highlight_color_by_index(i, k)
            )
        menu.addSeparator()
        act_del = menu.addAction("Remove Highlight")
        act_del.triggered.connect(lambda: self._delete_highlight_by_index(hl_idx))
        menu.exec(self.highlights_list.mapToGlobal(pos))

    def export_annotations_markdown(self):
        if not self.current_book:
            return
        safe_stem = "".join(
            c if c.isalnum() or c in (" ", "-", "_") else "_"
            for c in self.current_book.get("title", "book")
        ).strip()
        default_path = str(Path.home() / f"{safe_stem}_notes.md")

        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Highlights & Notes to Markdown",
            default_path,
            "Markdown Files (*.md)",
        )
        if not dest_path:
            return
        if not dest_path.lower().endswith(".md"):
            dest_path += ".md"

        try:
            export_book_notes_to_markdown(self.current_book, dest_path)
            QMessageBox.information(
                self,
                "Markdown Export Complete",
                f"Saved highlights and notes to:\n{dest_path}",
            )
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export Markdown:\n{e}")

    # -----------------------------------------------------------------
    # STATEFUL NEURAL TTS CONTROLS, MEMORY & HIGHLIGHTING
    # -----------------------------------------------------------------
    def _current_speed(self) -> float:
        return round(self.slider_speed.value() / 10.0, 1)

    def _current_pitch(self) -> int:
        return int(self.slider_pitch.value())

    def _on_voice_changed(self, text: str):
        if self.tts_worker and self.tts_worker.isRunning():
            self.tts_worker.set_voice(text.split(" ")[0])

    def _on_speed_changed(self, value: int):
        speed = round(value / 10.0, 1)
        self.lbl_speed_val.setText(f"{speed:.1f}x")
        if self.current_book:
            self.current_book.setdefault("tts_state", {})["speed"] = speed
            self.store.save()
        if self.tts_worker and self.tts_worker.isRunning():
            self.tts_worker.set_speed(speed)

    def _on_pitch_changed(self, value: int):
        self.lbl_pitch_val.setText(f"{value:+d}st" if value != 0 else "0st")
        if self.current_book:
            self.current_book.setdefault("tts_state", {})["pitch"] = int(value)
            self.store.save()
        if self.tts_worker and self.tts_worker.isRunning():
            self.tts_worker.set_pitch(value)

    @staticmethod
    def _find_matching_tts_unit_index(
        units: list[TTSUnit], page_idx: int, selected_text: str
    ) -> int:
        """Finds the exact TTSUnit matching the user's selected word, sentence, or paragraph."""
        import re

        def norm(s: str) -> str:
            return re.sub(r"[^\w\s]", "", " ".join(s.lower().split()))

        sel_clean = " ".join(selected_text.split()).lower()
        sel_norm = norm(selected_text)
        sel_words = sel_norm.split()

        # 1. Exact substring match on normalized text (first 40 chars)
        needle = sel_norm[:45].strip()
        if needle:
            for idx, u in enumerate(units):
                if u.page_idx == page_idx and needle in norm(u.text):
                    return idx

        # 2. Match if the unit text is contained inside a multi-sentence paragraph selection
        if len(sel_norm) > 25:
            for idx, u in enumerate(units):
                u_norm = norm(u.text)
                if u.page_idx == page_idx and len(u_norm) >= 10 and u_norm[:30] in sel_norm:
                    return idx

        # 3. Single-word or short-phrase token match
        if sel_words:
            first_phrase = " ".join(sel_words[: min(3, len(sel_words))])
            for idx, u in enumerate(units):
                if u.page_idx == page_idx and first_phrase in norm(u.text):
                    return idx
            first_word = sel_words[0]
            for idx, u in enumerate(units):
                if u.page_idx == page_idx and first_word in norm(u.text).split():
                    return idx

        return 0

    def _start_tts_from_selection(self, page_idx: int, selected_text: str):
        """
        Immediately stops any active TTS worker, overrides saved TTS memory,
        and starts reading directly from the user's selected text.
        """
        self.stop_tts()
        if not self.doc or not self.current_book:
            return

        selected_clean = " ".join(selected_text.split()).strip()
        if not selected_clean:
            return

        end_page = min(len(self.doc), page_idx + 20)
        units: list[TTSUnit] = []
        for p_idx in range(page_idx, end_page):
            blocks = self.doc.load_page(p_idx).get_text("blocks")
            units.extend(build_page_units(p_idx, blocks))

        # Fallback: if build_page_units filtered out short/copyright text on this page,
        # synthesize TTSUnits directly from the selected text so '▶ Read' ALWAYS works.
        if not units:
            units = [
                TTSUnit(
                    page_idx=page_idx,
                    para_idx=0,
                    sent_idx=0,
                    text=selected_clean,
                )
            ]
            start_index = 0
        else:
            start_index = self._find_matching_tts_unit_index(units, page_idx, selected_clean)
            # If the user selected a specific phrase inside a longer sentence, trim the first unit
            # so speech starts at the exact word they selected
            matched_unit = units[start_index]
            pos = matched_unit.text.lower().find(selected_clean[:25].lower())
            if pos > 0:
                trimmed = matched_unit.text[pos:].strip()
                if len(trimmed) >= 3:
                    units[start_index] = TTSUnit(
                        page_idx=matched_unit.page_idx,
                        para_idx=matched_unit.para_idx,
                        sent_idx=matched_unit.sent_idx,
                        text=trimmed,
                    )

        target_unit = units[start_index]
        self.current_book["tts_state"] = {
            "page": target_unit.page_idx,
            "para_idx": target_unit.para_idx,
            "sent_idx": target_unit.sent_idx,
            "speed": self._current_speed(),
            "pitch": self._current_pitch(),
        }
        self.store.save()

        self._on_tts_status(f"Starting TTS on Page {page_idx + 1}: “{selected_clean[:45]}...”")
        self._launch_tts_worker(units, start_index)

    def toggle_tts_play_pause(self):
        if not self.doc or not self.current_book:
            return

        # Priority 1: If the user currently has text selected on the page,
        # clicking Play (▶) immediately starts reading from that selection!
        sel_page, sel_text = self.canvas.get_active_selection()
        if sel_page is not None and sel_text:
            self.canvas.clear_selection()
            self._start_tts_from_selection(sel_page, sel_text)
            return

        # Priority 2: Pause or resume an already running TTS worker
        if self.tts_worker and self.tts_worker.isRunning():
            if self.tts_worker.is_paused:
                self.tts_worker.resume()
            else:
                self.tts_worker.pause()
            return

        # Priority 3: Resume from saved TTS position on current page (or start at top of current page)
        cur_page = self.current_book.get("current_page", 0)
        tts_state = self.current_book.get("tts_state", {})
        saved_page = tts_state.get("page", cur_page)
        active_pages = self.canvas.get_active_paginated_pages()
        if saved_page in active_pages:
            cur_page = saved_page
            saved_para = tts_state.get("para_idx", 0)
            saved_sent = tts_state.get("sent_idx", 0)
        else:
            saved_para = 0
            saved_sent = 0

        end_page = min(len(self.doc), cur_page + 20)
        units: list[TTSUnit] = []
        for p_idx in range(cur_page, end_page):
            blocks = self.doc.load_page(p_idx).get_text("blocks")
            units.extend(build_page_units(p_idx, blocks))

        if not units:
            self._on_tts_status("TTS Warning: No readable text found on this page.")
            return

        start_index = 0
        for idx, u in enumerate(units):
            if u.page_idx == cur_page and (
                u.para_idx > saved_para
                or (u.para_idx == saved_para and u.sent_idx >= saved_sent)
            ):
                start_index = idx
                break

        self._launch_tts_worker(units, start_index)

    def _launch_tts_worker(self, units: list[TTSUnit], start_index: int):
        voice_raw = self.combo_voice.currentText().split(" ")[0]
        self.tts_worker = NeuralTTSWorker(
            units=units,
            start_index=start_index,
            voice_id=voice_raw,
            speed=self._current_speed(),
            pitch_semitones=self._current_pitch(),
        )
        self.tts_worker.status_changed.connect(self._on_tts_status)
        self.tts_worker.unit_started.connect(self._on_tts_unit_started)
        self.tts_worker.playback_state_changed.connect(self._on_tts_state_changed)
        self.tts_worker.start()

    def tts_prev_paragraph(self):
        if self.tts_worker and self.tts_worker.isRunning():
            self.tts_worker.prev_paragraph()

    def tts_next_paragraph(self):
        if self.tts_worker and self.tts_worker.isRunning():
            self.tts_worker.next_paragraph()

    def _on_tts_unit_started(self, page_idx: int, para_idx: int, sent_idx: int, sentence_text: str):
        prev_page = self.canvas.active_tts_page
        self.canvas.active_tts_page = page_idx
        self.canvas.active_tts_sentence = sentence_text

        if self.current_book:
            self.current_book["tts_state"] = {
                "page": page_idx,
                "para_idx": para_idx,
                "sent_idx": sent_idx,
                "speed": self._current_speed(),
                "pitch": self._current_pitch(),
            }
            self.store.save()

        # In Two-Column Spread mode, only jump if page_idx is not already one of the 2 visible pages
        visible_spread = self.canvas.get_active_paginated_pages()
        if self.canvas.view_mode == "continuous":
            if self.current_book and page_idx != self.current_book.get("current_page", 0):
                self.jump_to_page(page_idx)
        else:
            if page_idx not in visible_spread:
                self.jump_to_page(page_idx)

        if prev_page is not None and prev_page != page_idx:
            self.canvas.invalidate_page(prev_page)
        self.canvas.invalidate_page(page_idx)

    def _on_tts_state_changed(self, state: str):
        if state == "playing":
            self.btn_tts_play_pause.setText("⏸")
            self.btn_tts_play_pause.setToolTip("Pause TTS")
        elif state == "paused":
            self.btn_tts_play_pause.setText("▶")
            self.btn_tts_play_pause.setToolTip("Resume TTS from paused sentence")
        else:
            self.btn_tts_play_pause.setText("▶")
            self.btn_tts_play_pause.setToolTip("Play / Resume TTS")

    def _on_tts_status(self, msg: str):
        self.lbl_tts_status.setText(msg)
        if "Error" in msg or "Warning" in msg:
            self.lbl_tts_status.setStyleSheet("color: #F38BA8; font-size: 12px; font-weight: bold;")
        elif "Paused" in msg:
            self.lbl_tts_status.setStyleSheet("color: #F9E2AF; font-size: 12px;")
        else:
            self.lbl_tts_status.setStyleSheet("color: #A6E3A1; font-size: 12px;")

    def stop_tts(self):
        if self.tts_worker:
            # Disconnect signals first so the dying thread's 'stopped' signal
            # never clobbers a newly started TTS worker!
            try:
                self.tts_worker.status_changed.disconnect()
                self.tts_worker.unit_started.disconnect()
                self.tts_worker.playback_state_changed.disconnect()
            except Exception:
                pass
            if self.tts_worker.isRunning():
                self.tts_worker.stop()
                self.tts_worker.wait(600)
            self.tts_worker = None

        prev_page = self.canvas.active_tts_page
        self.canvas.active_tts_page = None
        self.canvas.active_tts_sentence = None
        if prev_page is not None:
            self.canvas.invalidate_page(prev_page)
        self.btn_tts_play_pause.setText("▶")
        self.btn_tts_play_pause.setToolTip("Play / Resume TTS")