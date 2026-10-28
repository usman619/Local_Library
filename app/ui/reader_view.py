from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt, Signal, QTimer, QPoint, QPointF, QUrl, QRectF, QEvent
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem,
    QFrame, QLabel, QComboBox, QPushButton, QLineEdit, QSlider,
    QMenu, QFileDialog, QMessageBox
)

from app.config import DEFAULT_EPUB_FONTSIZE, TTS_VOICES, HIGHLIGHT_COLORS
from app.core.epub_loader import open_clean_document
from app.core.doc_daemon import DocDaemon
from app.ui.widgets import DocumentCanvasView
from app.ui.dialogs import (
    LookupPopupDialog,
    BookInfoDialog,
    AnnotationNoteDialog,
    export_book_notes_to_markdown,
)

from app.core.tts_worker import (
    NeuralTTSWorker,
    TTSUnit,
    build_page_units,
)

# In-book search stops after this many hits. "the" in a 1,000-page book is >100k matches;
# listing them all would stall the UI even with the search itself off the GUI thread.
SEARCH_MAX_MATCHES = 3000


class ReaderView(QWidget):
    """Document reader with Evince-style Search Bar (Ctrl+F), Drag-to-Highlight, Spread Modes, and Neural TTS."""

    progress_updated = Signal(str)
    status_updated = Signal(str)  # <-- Add this signal definition here
    
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
        self._initializing_toc = False

        # Heavy PyMuPDF work (in-book search, OCR / text extraction for TTS) runs in separate
        # worker PROCESSES, never on the GUI thread: PyMuPDF is not thread-safe.
        self._daemons: dict[str, DocDaemon] = {}
        self._search_running = False
        self._search_capped = False
        self._search_pct = 0
        self._search_focused = False
        self._search_start_page = 0
        self._search_matches_by_page: dict[int, list] = {}
        self._tts_prep: dict | None = None
        self._build_ui()
        self._setup_shortcuts()

        self._reading_timer = QTimer(self)
        self._reading_timer.setInterval(15000)  # Every 15 seconds while book is open
        self._reading_timer.timeout.connect(self._record_reading_heartbeat)

        # library.json is rewritten in full on every save(). Page turns, zoom steps,
        # TTS sentences and slider drags can fire many times a second, so those go
        # through this debounce instead of hitting the disk on the GUI thread each time.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(700)
        self._save_timer.timeout.connect(self._flush_save)

    # -----------------------------------------------------------------
    # BACKGROUND WORKER PROCESSES
    # -----------------------------------------------------------------
    def _daemon(self, kind: str) -> DocDaemon:
        d = self._daemons.get(kind)
        if d is None:
            d = DocDaemon(self.current_book.get("path", ""), self.epub_fontsize, self)
            d.event.connect(self._on_search_event if kind == "search" else self._on_tts_prep_event)
            self._daemons[kind] = d
        return d

    def _shutdown_daemons(self):
        for d in self._daemons.values():
            d.shutdown()
            d.deleteLater()
        self._daemons.clear()

    def _schedule_save(self):
        self._save_timer.start()

    def _flush_save(self):
        self._save_timer.stop()
        self.store.save()

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
        self.search_input.setAccessibleName("Search in document")
        self.search_input.setToolTip("Search in this book (Enter: next, Shift+Enter: previous, Esc: close)")
        self.search_input.setPlaceholderText("Find in document (Enter = Next, Shift+Enter = Previous, Esc = Close)...")
        self.search_input.setMinimumWidth(320)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._execute_search)
        self.search_input.textChanged.connect(lambda _: self._search_timer.start())
        self.search_input.installEventFilter(self)
        sb_layout.addWidget(self.search_input)

        self.lbl_search_count = QLabel("0 matches")
        self.lbl_search_count.setMinimumWidth(95)
        self.lbl_search_count.setStyleSheet("color: #A6ADC8; font-size: 12px;")
        sb_layout.addWidget(self.lbl_search_count)

        self.btn_prev_match = QPushButton("▲")
        self.btn_prev_match.setAccessibleName("Previous search match")
        self.btn_prev_match.setToolTip("Previous Match (Shift+Enter)")
        self.btn_prev_match.setFixedWidth(36)
        self.btn_prev_match.setEnabled(False)
        self.btn_prev_match.clicked.connect(self.find_prev)
        sb_layout.addWidget(self.btn_prev_match)

        self.btn_next_match = QPushButton("▼")
        self.btn_next_match.setAccessibleName("Next search match")
        self.btn_next_match.setToolTip("Next Match (Enter)")
        self.btn_next_match.setFixedWidth(36)
        self.btn_next_match.setEnabled(False)
        self.btn_next_match.clicked.connect(self.find_next)
        sb_layout.addWidget(self.btn_next_match)

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
        self.toc_tree.setFocusPolicy(Qt.StrongFocus)
        self.toc_tree.setIndentation(18)
        self.toc_tree.setUniformRowHeights(True)
        self.toc_tree.setTextElideMode(Qt.ElideRight)
        self.toc_tree.setAnimated(True)
        self.toc_tree.setExpandsOnDoubleClick(False)
        self.toc_tree.setRootIsDecorated(True)
        self.toc_tree.setItemsExpandable(True)
        self.toc_tree.setAlternatingRowColors(False)
        toc_icon_dir = Path(__file__).resolve().parents[2] / "images"
        self.toc_tree.setStyleSheet(
            "QTreeWidget::branch:closed:has-children {"
            f" image: url({(toc_icon_dir / 'toc-chevron-right.svg').as_posix()});"
            " }"
            "QTreeWidget::branch:open:has-children {"
            f" image: url({(toc_icon_dir / 'toc-chevron-down.svg').as_posix()});"
            " }"
        )
        self.toc_tree.itemClicked.connect(self._on_toc_clicked)
        self.toc_tree.viewport().installEventFilter(self)
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
        self.canvas.selection_changed.connect(self._on_canvas_selection_changed)
        self.canvas.zoom_changed.connect(self._on_canvas_zoom_changed)
        self.canvas.user_scrolled_manually.connect(self._on_user_scrolled_manually)

        self.canvas.highlight_added.connect(self._on_canvas_highlight_added)
        self.canvas.highlight_deleted.connect(self._delete_highlight_by_index)
        self.canvas.highlight_color_changed.connect(self._change_highlight_color_by_index)

        self.canvas.tts_requested_at_page.connect(self._start_tts_from_selection)
        self.canvas.lookup_requested.connect(self.open_lookup_dialog)
        self.canvas.note_requested_for_selection.connect(self._on_canvas_note_for_selection)
        self.canvas.edit_highlight_note_requested.connect(self._edit_highlight_note_by_index)

        self.splitter.addWidget(self.canvas)
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, stretch=1)

        # ---- BOTTOM STATEFUL NEURAL TTS CONTROL BAR ----
        tts_bar = QFrame()
        tts_bar.setObjectName("TTSBar")
        tts_bar.setStyleSheet(
            "QFrame { background-color: #181825; border-top: 1px solid #313244; padding: 6px 12px; }"
        )
        tts_bar_layout = QVBoxLayout(tts_bar)
        tts_bar_layout.setContentsMargins(4, 2, 4, 2)
        tts_bar_layout.setSpacing(4)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        voice_label = QLabel("Voice:")
        voice_label.setAccessibleName("Text to speech voice")
        controls_row.addWidget(voice_label)
        self.combo_voice = QComboBox()
        self.combo_voice.setAccessibleName("Text to speech voice")
        self.combo_voice.addItems(TTS_VOICES)
        self.combo_voice.setMinimumWidth(180)
        self.combo_voice.setMaximumWidth(320)
        self.combo_voice.setToolTip("Select Neural TTS Voice (saved automatically)")
        saved_global_voice = self.store.settings.get("tts_voice", TTS_VOICES[0])
        if saved_global_voice in TTS_VOICES:
            self.combo_voice.setCurrentText(saved_global_voice)
        self.combo_voice.currentTextChanged.connect(self._on_voice_changed)
        controls_row.addWidget(self.combo_voice)

        icon_btn_style = (
            "QPushButton { padding: 0px; font-size: 15px; min-width: 36px; max-width: 36px; "
            "min-height: 30px; max-height: 30px; }"
        )

        self.btn_prev_para = QPushButton("⏮")
        self.btn_prev_para.setStyleSheet(icon_btn_style)
        self.btn_prev_para.setToolTip("Previous Sentence / Paragraph")
        self.btn_prev_para.clicked.connect(self.tts_prev_paragraph)
        controls_row.addWidget(self.btn_prev_para)

        self.btn_tts_play_pause = QPushButton("▶")
        self.btn_tts_play_pause.setAccessibleName("Play or pause text to speech")
        self.btn_tts_play_pause.setStyleSheet(
            "QPushButton { padding: 0px; font-size: 15px; min-width: 40px; max-width: 40px; "
            "min-height: 30px; max-height: 30px; background-color: #89B4FA; color: #11111B; border: none; }"
            "QPushButton:hover { background-color: #B4BEFE; }"
        )
        self.btn_tts_play_pause.setToolTip("Play / Pause TTS")
        self.btn_tts_play_pause.clicked.connect(self.toggle_tts_play_pause)
        controls_row.addWidget(self.btn_tts_play_pause)

        self.btn_tts_stop = QPushButton("⏹")
        self.btn_tts_stop.setAccessibleName("Stop text to speech")
        self.btn_tts_stop.setStyleSheet(icon_btn_style)
        self.btn_tts_stop.setToolTip("Stop TTS and Clear Highlight")
        self.btn_tts_stop.clicked.connect(self.stop_tts)
        controls_row.addWidget(self.btn_tts_stop)

        self.btn_next_para = QPushButton("⏭")
        self.btn_next_para.setStyleSheet(icon_btn_style)
        self.btn_next_para.setToolTip("Next Sentence / Paragraph")
        self.btn_next_para.clicked.connect(self.tts_next_paragraph)
        controls_row.addWidget(self.btn_next_para)

        controls_row.addSpacing(8)

        controls_row.addWidget(QLabel("Speed:"))
        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setRange(5, 20)
        init_speed = int(round(float(self.store.settings.get("tts_speed", 1.0)) * 10))
        self.slider_speed.setValue(max(5, min(20, init_speed)))
        self.slider_speed.setFixedWidth(115)
        self.slider_speed.setToolTip("Adjust Speaking Speed (0.5x to 2.0x)")
        self.slider_speed.valueChanged.connect(self._on_speed_changed)
        controls_row.addWidget(self.slider_speed)

        self.lbl_speed_val = QLabel(f"{self.slider_speed.value() / 10.0:.1f}x")
        self.lbl_speed_val.setMinimumWidth(42)
        self.lbl_speed_val.setStyleSheet("color: #89B4FA; font-weight: bold;")
        controls_row.addWidget(self.lbl_speed_val)

        controls_row.addSpacing(4)

        controls_row.addWidget(QLabel("Pitch:"))
        self.slider_pitch = QSlider(Qt.Horizontal)
        self.slider_pitch.setRange(-6, 6)
        init_pitch = int(self.store.settings.get("tts_pitch", 0))
        self.slider_pitch.setValue(max(-6, min(6, init_pitch)))
        self.slider_pitch.setFixedWidth(105)
        self.slider_pitch.setToolTip("Adjust Voice Pitch (-6 to +6 semitones)")
        self.slider_pitch.valueChanged.connect(self._on_pitch_changed)
        controls_row.addWidget(self.slider_pitch)

        self.lbl_pitch_val = QLabel(f"{self.slider_pitch.value():+d}st" if self.slider_pitch.value() != 0 else "0st")
        self.lbl_pitch_val.setMinimumWidth(40)
        self.lbl_pitch_val.setStyleSheet("color: #89B4FA; font-weight: bold;")
        controls_row.addWidget(self.lbl_pitch_val)

        btn_reset_tts = QPushButton("Reset")
        # btn_reset_tts.setFixedWidth(30)
        btn_reset_tts.setToolTip("Reset Speed (1.0x) and Pitch (0st) to Default")
        btn_reset_tts.clicked.connect(self._reset_tts_sliders)
        controls_row.addWidget(btn_reset_tts)

        # Single toggleable Lock/Unlock Follow button:
        self.btn_auto_scroll = QPushButton()
        self.btn_auto_scroll.setCheckable(True)
        init_follow = bool(self.store.settings.get("tts_auto_scroll", True))
        self.btn_auto_scroll.setChecked(init_follow)
        self._update_follow_button_ui(init_follow)
        self.btn_auto_scroll.clicked.connect(self._on_follow_button_clicked)
        controls_row.addWidget(self.btn_auto_scroll)

        controls_row.addStretch(1)
        tts_bar_layout.addLayout(controls_row)

        self.lbl_tts_status = QLabel("TTS Ready — Select any text and click '▶ Read' or press ▶ to start")
        self.lbl_tts_status.setObjectName("TTSStatus")
        self.lbl_tts_status.setWordWrap(True)
        self.lbl_tts_status.setAccessibleName("Text to speech status")
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

        self.shortcut_bookmark = QShortcut(QKeySequence("Ctrl+D"), self)
        self.shortcut_bookmark.activated.connect(self.toggle_bookmark)

        self.shortcut_sidebar = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self.shortcut_sidebar.activated.connect(self.toggle_sidebar)

        self.shortcut_reset_zoom = QShortcut(QKeySequence("Ctrl+0"), self)
        self.shortcut_reset_zoom.activated.connect(self.reset_zoom)

        self.shortcut_zoom_in = QShortcut(QKeySequence("Ctrl++"), self)
        self.shortcut_zoom_in.setAutoRepeat(False)
        self.shortcut_zoom_in.activated.connect(self.zoom_in)

        self.shortcut_zoom_in_equal = QShortcut(QKeySequence("Ctrl+="), self)
        self.shortcut_zoom_in_equal.setAutoRepeat(False)
        self.shortcut_zoom_in_equal.activated.connect(self.zoom_in)

        self.shortcut_zoom_in_shift_equal = QShortcut(
            QKeySequence("Ctrl+Shift+="), self
        )
        self.shortcut_zoom_in_shift_equal.setAutoRepeat(False)
        self.shortcut_zoom_in_shift_equal.activated.connect(self.zoom_in)

        self.shortcut_zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        self.shortcut_zoom_out.setAutoRepeat(False)
        self.shortcut_zoom_out.activated.connect(self.zoom_out)

    def eventFilter(self, obj, event):
        search_input = getattr(self, "search_input", None)
        toc_tree = getattr(self, "toc_tree", None)
        if obj is search_input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self.find_prev()
                else:
                    self.find_next()
                return True
            elif event.key() == Qt.Key_Escape:
                self.close_search_bar()
                return True
        if (
            toc_tree is not None
            and obj is toc_tree.viewport()
            and event.type() == QEvent.MouseButtonPress
        ):
            if event.button() == Qt.LeftButton:
                pos = event.position().toPoint()
                item = toc_tree.itemAt(pos)
                if item is not None and item.childCount() > 0:
                    depth = 0
                    parent = item.parent()
                    while parent is not None:
                        depth += 1
                        parent = parent.parent()
                    branch_gutter_width = toc_tree.indentation() * (depth + 1)
                    if pos.x() < branch_gutter_width:
                        item.setExpanded(not item.isExpanded())
                        return True
        return super().eventFilter(obj, event)
    
    def _on_canvas_selection_changed(
        self, page_idx: int | None, selected_text: str, global_pos: QPoint
    ):
        """Handles text selection events on the document canvas."""
        if page_idx is not None and selected_text and selected_text.strip():
            cleaned = " ".join(selected_text.split()).strip()
            if cleaned and self.canvas.selection_popup:
                self.canvas.selection_popup.show_selection_bar(page_idx, cleaned, global_pos)
        else:
            self.canvas.selection_popup.hide()
    
    def _sync_toc_selection(self, page_idx: int, expand_ancestors: bool = True):
        """Synchronizes the Table of Contents tree selection with the active page index."""
        if not hasattr(self, "toc_tree") or not self.toc_tree:
            return
        self._sync_toc_to_page(page_idx, expand_ancestors=expand_ancestors)

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
        self._cancel_search()
        self.search_bar.hide()
        self.canvas.clear_search_overlays()
        self.canvas.setFocus()

    def _cancel_search(self):
        d = self._daemons.get("search")
        if d is not None:
            d.cancel()
        self._search_running = False

    def _execute_search(self):
        self._cancel_search()
        self.search_results.clear()
        self.search_list.clear()
        self.current_search_idx = -1
        self._search_matches_by_page = {}
        self._search_focused = False
        self._search_capped = False
        self._search_pct = 0

        query = self.search_input.text().strip()
        if not self.doc or not self.current_book or len(query) < 2:
            self.lbl_search_count.setText(
                "Enter 2+ characters" if query else "0 matches"
            )
            self.lbl_search_count.setStyleSheet("color: #A6ADC8; font-size: 12px;")
            self.btn_prev_match.setEnabled(False)
            self.btn_next_match.setEnabled(False)
            self.canvas.clear_search_overlays()
            return

        # The search itself runs in a worker process that streams results back, so typing,
        # scrolling and zooming stay smooth even on a 1,000-page book.
        self.canvas.clear_search_overlays()
        self.lbl_search_count.setText("Searching…")
        self.lbl_search_count.setStyleSheet("color: #A6ADC8; font-size: 12px;")
        self.btn_prev_match.setEnabled(False)
        self.btn_next_match.setEnabled(False)
        self._search_running = True
        self._search_start_page = int(self.current_book.get("current_page", 0))
        self._daemon("search").request("search", query=query, cap=SEARCH_MAX_MATCHES)

    def _update_search_label(self):
        n = len(self.search_results)
        if self.current_search_idx >= 0:
            text = f"{self.current_search_idx + 1} of {n}"
        else:
            text = f"{n} found"
        if self._search_capped and not self._search_running:
            text += "+"
        if self._search_running:
            text += f" … {self._search_pct}%"
        self.lbl_search_count.setText(text)

    def _refresh_search_overlays(self):
        active = None
        if 0 <= self.current_search_idx < len(self.search_results):
            page_idx, rect, _ = self.search_results[self.current_search_idx]
            active = (page_idx, rect)
        self.canvas.set_search_overlays(self._search_matches_by_page, active)

    def _on_search_event(self, ev: dict):
        kind = ev.get("event")
        if kind in ("fatal", "error"):
            self._search_running = False
            self.lbl_search_count.setText("Search failed")
            self.lbl_search_count.setToolTip(str(ev.get("error", "")))
            self.lbl_search_count.setStyleSheet("color: #F38BA8; font-size: 12px;")
            return
        if not self._search_running:
            return

        if kind == "search_batch":
            total = max(1, int(ev.get("total", 1)))
            self._search_pct = min(100, int(ev.get("pages_done", 0) * 100 / total))
            first_forward = None
            for item in ev.get("batch", []):
                page_idx = int(item["page"])
                for h in item["hits"]:
                    rect = pymupdf.Rect(*h["rect"])
                    match_idx = len(self.search_results)
                    self.search_results.append((page_idx, rect, h["snippet"]))
                    self._search_matches_by_page.setdefault(page_idx, []).append(rect)
                    li = QListWidgetItem(f"[p. {page_idx + 1}]  {h['snippet']}")
                    li.setData(Qt.UserRole, match_idx)
                    self.search_list.addItem(li)
                    if first_forward is None and page_idx >= self._search_start_page:
                        first_forward = match_idx

            if self.search_results:
                self.lbl_search_count.setStyleSheet("color: #A6E3A1; font-size: 12px;")
                self.btn_prev_match.setEnabled(True)
                self.btn_next_match.setEnabled(True)
            # Jump to the first match at/after the page being read as soon as it shows up
            # (the same match the old blocking search picked), then leave the view alone.
            if not self._search_focused and first_forward is not None:
                self._search_focused = True
                self.sidebar_tabs.setCurrentWidget(self.search_list)
                self._focus_search_match(first_forward, self._search_matches_by_page)
            else:
                self._refresh_search_overlays()
                self._update_search_label()

        elif kind == "search_done":
            self._search_running = False
            self._search_capped = bool(ev.get("capped"))
            if not self.search_results:
                self.lbl_search_count.setText("No matches")
                self.lbl_search_count.setStyleSheet("color: #F38BA8; font-size: 12px;")
                self.btn_prev_match.setEnabled(False)
                self.btn_next_match.setEnabled(False)
                self.canvas.clear_search_overlays()
                return
            if not self._search_focused:
                # Every match is before the current page: wrap around to the first one.
                self._search_focused = True
                self.sidebar_tabs.setCurrentWidget(self.search_list)
                self._focus_search_match(0, self._search_matches_by_page)
            else:
                self._update_search_label()
            if self._search_capped:
                self.lbl_search_count.setToolTip(
                    f"Showing the first {SEARCH_MAX_MATCHES} matches. Refine the search to narrow it down."
                )
            else:
                self.lbl_search_count.setToolTip("")

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

        self._update_search_label()
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
    def load_book(self, filepath: str) -> bool:
        """Opens filepath in the reader. Returns False (and keeps the current view) on failure."""
        self.stop_tts()
        self.close_search_bar()
        self._shutdown_daemons()  # they hold the OLD book open
        self._flush_save()

        try:
            new_doc = open_clean_document(filepath, fontsize=self.epub_fontsize)
        except Exception as e:
            QMessageBox.warning(
                self, "Cannot Open Book", f"Could not open:\n{filepath}\n\n{e}"
            )
            return False

        book = self.store.add_or_update_book(filepath, open_doc=new_doc)
        if book is None:
            new_doc.close()
            QMessageBox.warning(
                self, "Cannot Open Book", f"Unsupported or unreadable file:\n{filepath}"
            )
            return False

        # Only close the previous document once the new one is ready; closing it first
        # left the canvas pointing at a closed document whenever opening failed.
        old_doc = self.doc
        self.doc = new_doc
        self.current_book = book
        self.store.touch_last_opened(filepath)

        tts_state = self.current_book.get("tts_state", {})
        saved_voice = tts_state.get("voice") or self.store.settings.get("tts_voice", TTS_VOICES[0])
        saved_speed = float(tts_state.get("speed", self.store.settings.get("tts_speed", 1.0)))
        saved_pitch = int(tts_state.get("pitch", self.store.settings.get("tts_pitch", 0)))

        self.combo_voice.blockSignals(True)
        if saved_voice in TTS_VOICES:
            self.combo_voice.setCurrentText(saved_voice)
        self.combo_voice.blockSignals(False)

        self.slider_speed.blockSignals(True)
        self.slider_speed.setValue(max(5, min(20, int(round(saved_speed * 10)))))
        self.lbl_speed_val.setText(f"{saved_speed:.1f}x")
        self.slider_speed.blockSignals(False)

        self.slider_pitch.blockSignals(True)
        self.slider_pitch.setValue(max(-6, min(6, saved_pitch)))
        self.lbl_pitch_val.setText(f"{saved_pitch:+d}st" if saved_pitch != 0 else "0st")
        self.slider_pitch.blockSignals(False)

        self._populate_toc()
        self._populate_bookmarks()
        self._populate_highlights()

        self.canvas.set_document(self.doc, self.current_book.get("highlights", []))
        if old_doc is not None and old_doc is not self.doc:
            try:
                old_doc.close()
            except Exception:
                pass
        self._reading_timer.start()

        saved_page = min(
            max(0, int(self.current_book.get("current_page", 0))),
            len(self.doc) - 1,
        )
        saved_zoom = float(self.current_book.get("zoom", 1.25))
        self.canvas.set_zoom(saved_zoom)
        self._initializing_toc = True
        try:
            self.canvas.scroll_to_page(saved_page)
        finally:
            self._initializing_toc = False
        self._sync_toc_selection(saved_page, expand_ancestors=False)
        self._emit_status()
        return True

    def close_book(self):
        """Closes the active document, stops TTS, and clears reader state when returning Home."""
        self.stop_tts()
        self.close_search_bar()
        self._reading_timer.stop()
        self._shutdown_daemons()
        self._flush_save()
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
            # current_book is the very same dict as store.books[<id>], so add_reading_time()
            # already updates it. Adding 15 again here counted every heartbeat as 30 s.
            self.store.add_reading_time(self.current_book["path"], 15)

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

    def _emit_status(self):
        """Emits the current page, reading percentage, and zoom percentage to the top toolbar."""
        if not self.doc:
            self.status_updated.emit("")
            return
        total = max(1, len(self.doc))
        cur = min(total, max(1, self.canvas.current_page + 1))
        pct = int((cur / total) * 100)
        zoom_pct = int(round(self.canvas.zoom * 100))
        self.status_updated.emit(f"Page {cur} / {total} ({pct}%)  •  Zoom {zoom_pct}%")

    def _on_canvas_page_changed(
        self,
        page_idx: int,
        _total_pages: int | None = None,
        _zoom: float | None = None,
    ):
        if not self.doc or not self.current_book:
            return
        # page_changed also fires on every zoom step; only do the heavy work on a real page change.
        if self.current_book.get("current_page") != page_idx:
            self.current_book["current_page"] = page_idx
            self._schedule_save()
            self._sync_toc_selection(
                page_idx, expand_ancestors=not self._initializing_toc
            )
        self._emit_status()

    def _on_canvas_zoom_changed(self, new_zoom: float):
        if self.current_book:
            self.current_book["zoom"] = round(float(new_zoom), 2)
            self._schedule_save()
        self._emit_status()
    
    def zoom_in(self):
        if self.doc:
            self.adjust_zoom(0.15)

    def zoom_out(self):
        if self.doc:
            self.adjust_zoom(-0.15)

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
        self.canvas.animate_zoom_to(self.canvas.zoom + delta, center_pt)

    def reset_zoom(self):
        if self.doc:
            vp = self.canvas.viewport()
            center_pt = QPointF(vp.width() / 2.0, vp.height() / 2.0)
            self.canvas.animate_zoom_to(1.25, center_pt)

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
            if not isinstance(page_num, int) or page_num <= 0:
                continue
            title = " ".join(str(title).split()) or "Untitled section"
            page_idx = page_num - 1
            label = f"{title}  ·  p. {page_num}"
            item = QTreeWidgetItem([label])
            item.setToolTip(0, f"{title}\nPage {page_num}")
            item.setStatusTip(0, f"Go to {title} (page {page_num})")
            item.setData(0, Qt.UserRole, page_idx)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            parent = stack[-1][1] if stack else self.toc_tree.invisibleRootItem()
            parent.addChild(item)
            stack.append((lvl, item))
            self._toc_items.append((page_idx, item))
        # Start compact; users can expand only the branches they need.
        self.toc_tree.collapseAll()
        self._update_toc_icons()
        # Connect expand/collapse signals (only once)
        if not getattr(self, '_toc_signals_connected', False):
            self.toc_tree.itemExpanded.connect(self._on_toc_item_expanded)
            self.toc_tree.itemCollapsed.connect(self._on_toc_item_collapsed)
            self._toc_signals_connected = True

    def _update_toc_icons(self):
        """Use the native tree branch indicator, positioned before the label."""
        return

    def _on_toc_item_expanded(self, item: QTreeWidgetItem):
        return

    def _on_toc_item_collapsed(self, item: QTreeWidgetItem):
        return

    def _highlight_toc_item(
        self, active_item: QTreeWidgetItem, expand_ancestors: bool = True
    ):
        """Applies a single unified highlight to active_item and clears old bold/color states."""
        if not getattr(self, "_toc_items", None):
            return
        
        self.toc_tree.blockSignals(True)
        # Clear all selection first to avoid stale CSS :selected highlights
        self.toc_tree.clearSelection()
        
        for _p, item in self._toc_items:
            font = item.font(0)
            is_curr = item is active_item
            font.setBold(is_curr)
            item.setFont(0, font)
            item.setForeground(0, QColor("#89B4FA" if is_curr else "#CDD6F4"))
            # Set background explicitly: active item gets subtle highlight, others get transparent
            if is_curr:
                item.setBackground(0, QColor("#313244"))
            else:
                item.setBackground(0, QColor("#181825"))
        
        if expand_ancestors:
            parent = active_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()

        self.toc_tree.setCurrentItem(active_item)
        self.toc_tree.scrollToItem(active_item, QTreeWidget.PositionAtCenter)
        self.toc_tree.blockSignals(False)

    def _sync_toc_to_page(
        self, current_page_idx: int, expand_ancestors: bool = True
    ):
        """Highlights and scrolls to the active chapter/section in the Contents tree while scrolling."""
        if not getattr(self, "_toc_items", None):
            return

        # If the currently selected TOC item is already on this exact page, keep it highlighted!
        curr = self.toc_tree.currentItem()
        if curr is not None and curr.data(0, Qt.UserRole) == current_page_idx:
            return

        active_item: QTreeWidgetItem | None = None
        for toc_page, item in self._toc_items:
            if toc_page <= current_page_idx:
                active_item = item
            else:
                break

        if active_item is None:
            active_item = self._toc_items[0][1]

        if self.toc_tree.currentItem() is not active_item:
            self._highlight_toc_item(
                active_item, expand_ancestors=expand_ancestors
            )

    def _on_toc_clicked(self, item: QTreeWidgetItem, _col: int):
        page_idx = item.data(0, Qt.UserRole)
        if page_idx is not None:
            self._highlight_toc_item(item)
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

    def _save_current_tts_prefs(self):
        voice = self.combo_voice.currentText()
        speed = self._current_speed()
        pitch = self._current_pitch()

        # 1. Save globally for future books & sessions
        self.store.save_settings(
            tts_voice=voice,
            tts_speed=speed,
            tts_pitch=pitch,
        )
        # 2. Save in current book's tts_state
        if self.current_book:
            tts_state = self.current_book.setdefault("tts_state", {})
            tts_state["voice"] = voice
            tts_state["speed"] = speed
            tts_state["pitch"] = pitch
            self._schedule_save()

        # 3. Hot-update running worker so upcoming prefetched sentences use the new settings immediately
        if self.tts_worker and self.tts_worker.isRunning():
            self.tts_worker.update_live_settings(
                voice_spec=voice, speed=speed, pitch=pitch
            )

    def _on_voice_changed(self, _text: str):
        self._save_current_tts_prefs()

    def _on_speed_changed(self, val: int):
        speed = val / 10.0
        self.lbl_speed_val.setText(f"{speed:.1f}x")
        self._save_current_tts_prefs()

    def _on_pitch_changed(self, val: int):
        self.lbl_pitch_val.setText(f"{val:+d}st" if val != 0 else "0st")
        self._save_current_tts_prefs()

    def _reset_tts_sliders(self):
        self.slider_speed.setValue(10)
        self.slider_pitch.setValue(0)
        self._save_current_tts_prefs()
    @staticmethod
    def _find_matching_tts_unit_index(
        units: list[TTSUnit],
        page_idx: int,
        selected_text: str,
        hint_rect=None,
    ) -> int:
        """
        Finds the TTSUnit matching the user's selected word, sentence, or paragraph.
        hint_rect (PDF coordinates of the first selected line) disambiguates repeated
        text: selecting the 3rd "the" on a page used to start at the 1st sentence containing "the".
        """
        import re

        def norm(s: str) -> str:
            return re.sub(r"[^\w\s]", "", " ".join(s.lower().split()))

        def pick(cands: list[int]) -> int | None:
            if not cands:
                return None
            if hint_rect is not None:
                cx = (hint_rect[0] + hint_rect[2]) / 2.0
                cy = (hint_rect[1] + hint_rect[3]) / 2.0
                for i in cands:
                    bb = units[i].bbox
                    if bb and bb[0] - 2 <= cx <= bb[2] + 2 and bb[1] - 2 <= cy <= bb[3] + 2:
                        return i
            return cands[0]

        sel_norm = norm(selected_text)
        sel_words = sel_norm.split()
        page_units = [(i, u, norm(u.text)) for i, u in enumerate(units) if u.page_idx == page_idx]

        # 1. Exact substring match on normalized text (first 45 chars)
        needle = sel_norm[:45].strip()
        if needle:
            hit = pick([i for i, _u, n in page_units if needle in n])
            if hit is not None:
                return hit

        # 2. Match if the unit text is contained inside a multi-sentence paragraph selection
        if len(sel_norm) > 25:
            hit = pick([i for i, _u, n in page_units if len(n) >= 10 and n[:30] in sel_norm])
            if hit is not None:
                return hit

        # 3. Single-word or short-phrase token match
        if sel_words:
            first_phrase = " ".join(sel_words[: min(3, len(sel_words))])
            hit = pick([i for i, _u, n in page_units if first_phrase in n])
            if hit is not None:
                return hit
            hit = pick([i for i, _u, n in page_units if sel_words[0] in n.split()])
            if hit is not None:
                return hit

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

        hint = getattr(self.canvas, "tts_hint_rect", None)
        self.canvas.tts_hint_rect = None
        self._begin_tts_prepare(
            page_idx,
            {"mode": "selection", "page_idx": page_idx, "selected": selected_clean, "hint": hint},
        )

    # -----------------------------------------------------------------
    # BACKGROUND TEXT PREPARATION FOR TTS (incl. OCR of scanned pages)
    # -----------------------------------------------------------------
    def _begin_tts_prepare(self, start_page: int, plan: dict):
        """Extracts the text to speak in a worker process; playback starts when page 1 is ready."""
        self._cancel_tts_prepare()
        if not self.doc or not self.current_book:
            return
        self._tts_prep = {
            "plan": plan,
            "start_page": start_page,
            "units": [],
            "launched": False,
            "done": False,
        }
        self.btn_tts_play_pause.setText("⏳")
        self.btn_tts_play_pause.setToolTip("Preparing text… click to cancel")
        self._on_tts_status(f"⏳ Preparing text from page {start_page + 1}...")
        self._daemon("tts").request(
            "tts_units",
            filepath=self.current_book.get("path", ""),
            start_page=start_page,
            max_pages=15,
            allow_ocr=True,
        )

    def _cancel_tts_prepare(self):
        d = self._daemons.get("tts")
        if d is not None:
            d.cancel()
        self._tts_prep = None
        if self.tts_worker is not None:
            self.tts_worker.expect_more_units = False

    def _on_tts_prep_event(self, ev: dict):
        kind = ev.get("event")
        prep = self._tts_prep
        if kind in ("fatal", "error"):
            if prep is not None:
                launched = prep["launched"]
                self._tts_prep = None
                if self.tts_worker is not None:
                    self.tts_worker.expect_more_units = False
                self._on_tts_status(f"⚠ TTS Error: {ev.get('error', 'could not read the page text')}")
                if not launched:
                    self.btn_tts_play_pause.setText("▶")
                    self.btn_tts_play_pause.setToolTip("Play / Resume TTS")
            return
        if prep is None:
            return

        if kind == "tts_page_start":
            if not prep["launched"]:
                self._on_tts_status(
                    f"⏳ Preparing text from page {int(ev['page']) + 1}... "
                    "(scanned pages are OCR'd; this can take a few seconds)"
                )
        elif kind == "tts_page":
            units = [
                TTSUnit(
                    page_idx=int(u["page_idx"]),
                    para_idx=int(u["para_idx"]),
                    sent_idx=int(u["sent_idx"]),
                    text=u["text"],
                    bbox=tuple(u["bbox"]) if u.get("bbox") else None,
                )
                for u in ev.get("units", [])
            ]
            if ev.get("used_ocr"):
                self._on_tts_status(
                    f"🔍 OCR Extracted {len(units)} sentences from scanned Page {int(ev['page']) + 1}..."
                )
            # Same list object the running worker reads from, so playback continues onto
            # these pages without being restarted.
            prep["units"].extend(units)
            if not prep["launched"]:
                self._try_launch_prepared(final=False)
        elif kind == "tts_done":
            prep["done"] = True
            if self.tts_worker is not None:
                self.tts_worker.expect_more_units = False
            if not prep["launched"]:
                self._try_launch_prepared(final=True)
            self._tts_prep = None

    def _try_launch_prepared(self, final: bool):
        prep = self._tts_prep
        if prep is None or prep["launched"]:
            return
        units: list[TTSUnit] = prep["units"]
        plan = prep["plan"]
        if not units and not final:
            return  # blank page so far; keep looking at the following pages

        if plan["mode"] == "selection":
            page_idx = plan["page_idx"]
            selected_clean = plan["selected"]
            # Fallback: if build_page_units filtered out short/copyright text on this page,
            # synthesize TTSUnits directly from the selected text so '▶ Read' ALWAYS works.
            if not units:
                units.append(
                    TTSUnit(page_idx=page_idx, para_idx=0, sent_idx=0, text=selected_clean)
                )
                start_index = 0
            else:
                start_index = self._find_matching_tts_unit_index(
                    units, page_idx, selected_clean, hint_rect=plan.get("hint")
                )
                # If the user selected a phrase inside a longer sentence, trim the first unit
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
                            bbox=matched_unit.bbox,
                        )
            target_unit = units[start_index]
            if self.current_book is not None:
                self.current_book["tts_state"] = {
                    "page": target_unit.page_idx,
                    "para_idx": target_unit.para_idx,
                    "sent_idx": target_unit.sent_idx,
                    "voice": self.combo_voice.currentText(),
                    "speed": self._current_speed(),
                    "pitch": self._current_pitch(),
                }
                self._schedule_save()
            self._on_tts_status(f"Starting TTS on Page {page_idx + 1}: “{selected_clean[:45]}...”")
        else:
            if not units:
                self._tts_prep = None
                self.btn_tts_play_pause.setText("▶")
                self.btn_tts_play_pause.setToolTip("Play / Resume TTS")
                self._on_tts_status("TTS Warning: No readable text found on this page.")
                return
            cur_page = prep["start_page"]
            start_index = 0
            for idx, u in enumerate(units):
                if u.page_idx == cur_page and (
                    u.para_idx > plan["saved_para"]
                    or (u.para_idx == plan["saved_para"] and u.sent_idx >= plan["saved_sent"])
                ):
                    start_index = idx
                    break

        prep["launched"] = True
        self._launch_tts_worker(units, start_index, more_expected=not final)

    def toggle_tts_play_pause(self):
        if not self.doc or not self.current_book:
            return

        # Priority 1: If the user currently has text selected on the page,
        # clicking Play (▶) immediately starts reading from that selection!
        sel_page, sel_text = self.canvas.get_active_selection()
        if sel_page is not None and sel_text:
            self.canvas.tts_hint_rect = self.canvas.first_selection_rect()
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

        # Priority 2b: text is still being prepared in the background -> button cancels it
        if self._tts_prep is not None and not self._tts_prep["launched"]:
            self.stop_tts()
            self._on_tts_status("TTS preparation cancelled.")
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

        self._begin_tts_prepare(
            cur_page, {"mode": "resume", "saved_para": saved_para, "saved_sent": saved_sent}
        )

    def _launch_tts_worker(
        self, units: list[TTSUnit], start_index: int, more_expected: bool | None = None
    ):
        self._stop_tts_worker()  # NOT stop_tts(): that would also cancel background preparation
        if more_expected is None:
            more_expected = self._tts_prep is not None and not self._tts_prep.get("done", False)
        self.current_tts_units = units
        voice_spec = self.combo_voice.currentText()
        speed = self._current_speed()
        pitch = self._current_pitch()

        self.tts_worker = NeuralTTSWorker(
            units=units,
            start_index=start_index,
            voice_spec=voice_spec,
            speed=speed,
            pitch=pitch,
        )
        self.tts_worker.expect_more_units = more_expected
        self.tts_worker.status_changed.connect(self._on_tts_status)
        self.tts_worker.unit_started.connect(self._on_tts_unit_started)
        self.tts_worker.playback_state_changed.connect(self._on_tts_playback_state_changed)
        self.tts_worker.start()

    def _on_tts_playback_state_changed(self, state: str):
        if state == "playing":
            self.btn_tts_play_pause.setText("⏸")
            self.btn_tts_play_pause.setToolTip("Pause TTS")
        elif state == "paused":
            self.btn_tts_play_pause.setText("▶")
            self.btn_tts_play_pause.setToolTip("Resume TTS")
        else:
            self.btn_tts_play_pause.setText("▶")
            self.btn_tts_play_pause.setToolTip("Play / Resume TTS")
            prev_page = self.canvas.active_tts_page
            
            self.canvas.active_tts_page = None
            self.canvas.active_tts_sentence = None
            self.canvas.active_tts_text = None

            if prev_page is not None:
                self.canvas.invalidate_page(prev_page)

    def tts_prev_paragraph(self):
        if not self.tts_worker or not self.current_tts_units:
            return
        new_idx = max(0, self.tts_worker.current_index - 1)
        self._launch_tts_worker(self.current_tts_units, new_idx)

    def tts_next_paragraph(self):
        if not self.tts_worker or not self.current_tts_units:
            return
        new_idx = min(len(self.current_tts_units) - 1, self.tts_worker.current_index + 1)
        self._launch_tts_worker(self.current_tts_units, new_idx)

    def _on_tts_unit_started(
        self, page_idx: int, para_idx: int, sent_idx: int, sentence_text: str
    ):
        prev_page = self.canvas.active_tts_page
        self.canvas.active_tts_page = page_idx
        self.canvas.active_tts_sentence = sentence_text
        self.canvas.active_tts_text = sentence_text

        active_bbox = None
        if self.tts_worker and 0 <= self.tts_worker.current_index < len(self.current_tts_units):
            u = self.current_tts_units[self.tts_worker.current_index]
            if u.page_idx == page_idx and u.bbox:
                active_bbox = u.bbox
        self.canvas.active_tts_bbox = active_bbox

        if self.current_book:
            self.current_book["tts_state"] = {
                "page": page_idx,
                "para_idx": para_idx,
                "sent_idx": sent_idx,
                "voice": self.combo_voice.currentText(),
                "speed": self._current_speed(),
                "pitch": self._current_pitch(),
            }
            self._schedule_save()

        if prev_page is not None and prev_page != page_idx:
            self.canvas.invalidate_page(prev_page)
        self.canvas.invalidate_page(page_idx)

        # Smoothly glide the page ONLY when Follow is Locked (🔒)
        if getattr(self, "btn_auto_scroll", None) and self.btn_auto_scroll.isChecked():
            self._smooth_follow_active_sentence(force_center=False)

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
        self._cancel_tts_prepare()
        self._stop_tts_worker()

    def _stop_tts_worker(self):
        if self.tts_worker:
            worker = self.tts_worker
            self.tts_worker = None
            try:
                worker.status_changed.disconnect()
                worker.unit_started.disconnect()
                worker.playback_state_changed.disconnect()
            except Exception:
                pass
            if worker.isRunning():
                worker.stop()
                if not hasattr(self, "_stopping_workers"):
                    self._stopping_workers = set()
                self._stopping_workers.add(worker)
                worker.finished.connect(lambda w=worker: self._stopping_workers.discard(w))

        prev_page = self.canvas.active_tts_page
        self.canvas.active_tts_page = None
        self.canvas.active_tts_sentence = None
        self.canvas.active_tts_text = None
        
        if prev_page is not None:
            self.canvas.invalidate_page(prev_page)
        self.btn_tts_play_pause.setText("▶")
        self.btn_tts_play_pause.setToolTip("Play / Resume TTS")
    
    def wait_for_workers(self, timeout_ms: int = 4000):
        """Called on app exit: quitting while a QThread is still running aborts the process."""
        self._shutdown_daemons()
        workers = list(getattr(self, "_stopping_workers", set()))
        if self.tts_worker is not None:
            workers.append(self.tts_worker)
        for w in workers:
            try:
                if w.isRunning():
                    w.stop()
                    if not w.wait(timeout_ms):
                        w.terminate()
                        w.wait(500)
            except RuntimeError:
                pass  # underlying C++ object already gone

    # Auto Scrolling
    def _update_follow_button_ui(self, locked: bool):
        """Updates the visual state of the Lock/Unlock TTS Follow button."""
        if not getattr(self, "btn_auto_scroll", None):
            return
        self.btn_auto_scroll.blockSignals(True)
        self.btn_auto_scroll.setChecked(locked)
        self.btn_auto_scroll.blockSignals(False)

        if locked:
            self.btn_auto_scroll.setText("Follow Locked")
            self.btn_auto_scroll.setToolTip(
                "TTS Auto-Follow is LOCKED (Page glides smoothly with speech).\n"
                "Scrolling manually with your mouse wheel will automatically unlock it."
            )
            self.btn_auto_scroll.setStyleSheet("""
                QPushButton {
                    background-color: #313244;
                    color: #89B4FA;
                    border: 1px solid #89B4FA;
                    border-radius: 7px;
                    padding: 5px 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #45475A;
                    color: #FFFFFF;
                }
            """)
        else:
            self.btn_auto_scroll.setText("Follow Unlocked")
            self.btn_auto_scroll.setToolTip(
                "TTS Auto-Follow is UNLOCKED (Free-scroll mode).\n"
                "Click to re-lock and smoothly glide back to the active spoken sentence."
            )
            self.btn_auto_scroll.setStyleSheet("""
                QPushButton {
                    background-color: #1E1E2E;
                    color: #F9E2AF;
                    border: 1px solid #F9E2AF;
                    border-radius: 7px;
                    padding: 5px 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #313244;
                    color: #FFFFFF;
                }
            """)

    def _on_follow_button_clicked(self, checked: bool):
        self._update_follow_button_ui(checked)
        self.store.save_settings(tts_auto_scroll=checked)
        # If the user just re-locked Follow while TTS is active, smoothly glide right back to the active sentence!
        if checked and self.canvas.active_tts_page is not None and self.canvas.active_tts_sentence:
            self._smooth_follow_active_sentence(force_center=True)

    def _on_user_scrolled_manually(self):
        """
        Automatically unlocks TTS auto-follow the moment the user scrolls the document
        while TTS is actively playing, so the screen never fights the user's scrolling.
        """
        if (
            self.tts_worker
            and self.tts_worker.isRunning()
            and not self.tts_worker.is_paused
            and getattr(self, "btn_auto_scroll", None)
            and self.btn_auto_scroll.isChecked()
        ):
            self._update_follow_button_ui(False)
            self.lbl_tts_status.setText(
                "Free-Scroll Mode (Auto-follow paused while you browse — Click 'Follow Unlocked' to snap back)"
            )

    def _smooth_follow_active_sentence(self, force_center: bool = False):
        """Glides the viewport smoothly to keep the active TTS sentence in the reading sweet spot."""
        if not self.doc or self.canvas.active_tts_page is None or not self.canvas.active_tts_sentence:
            return

        page_idx = self.canvas.active_tts_page
        sentence_text = self.canvas.active_tts_sentence
        active_bbox = getattr(self.canvas, "active_tts_bbox", None)

        if self.canvas.view_mode != "continuous":
            if page_idx != self.canvas.current_page:
                self.jump_to_page(page_idx)
            return

        page = self.doc.load_page(page_idx)
        from app.core.renderer import PageRenderer
        filepath = self.current_book.get("path", "") if self.current_book else ""
        rects = PageRenderer._find_text_rects(
            page,
            sentence_text,
            filepath=filepath,
            page_idx=page_idx,
            clip_bbox=active_bbox,
        )
        if rects:
            self.canvas.smooth_scroll_to_page_rect(
                page_idx, rects[0], force_center=force_center
            )
        elif active_bbox and len(active_bbox) == 4:
            import pymupdf
            self.canvas.smooth_scroll_to_page_rect(
                page_idx, pymupdf.Rect(active_bbox), force_center=force_center
            )