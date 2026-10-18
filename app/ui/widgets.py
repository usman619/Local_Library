import os
import bisect
import webbrowser
import pymupdf
from PySide6.QtCore import Qt, QSize, Signal, QEvent, QPoint, QPointF, QRect, QRectF, QTimer
from PySide6.QtGui import (
    QPixmap, QFont, QCursor, QNativeGestureEvent, QPainter, QColor, QPen
)
from PySide6.QtWidgets import (
    QAbstractScrollArea, QLabel, QFrame, QVBoxLayout, QHBoxLayout,
    QProgressBar, QPushButton, QGestureEvent, QPinchGesture, QApplication, QMenu
)

from app.config import EPUB_PAGE_WIDTH, EPUB_PAGE_HEIGHT, HIGHLIGHT_COLORS
from app.core.renderer import PageRenderer


class SelectionPopupBar(QFrame):
    """Floating Evince/Foliate-style action bar when text is selected with the mouse."""

    copy_requested = Signal()
    highlight_requested = Signal(str)
    note_requested = Signal()
    lookup_requested = Signal()
    tts_from_here_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: #1E1E2E;
                border: 1px solid #89B4FA;
                border-radius: 8px;
                padding: 3px;
            }
            QPushButton {
                background-color: #313244;
                color: #CDD6F4;
                border: 1px solid #45475A;
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #45475A;
                border-color: #89B4FA;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        btn_copy = QPushButton("Copy")
        btn_copy.setToolTip("Copy selected text (Ctrl+C)")
        btn_copy.clicked.connect(self.copy_requested.emit)
        layout.addWidget(btn_copy)

        for key, info in HIGHLIGHT_COLORS.items():
            swatch = QPushButton("")
            swatch.setFixedSize(22, 22)
            swatch.setCursor(QCursor(Qt.PointingHandCursor))
            swatch.setToolTip(f"Highlight {info['label']}")
            swatch.setStyleSheet(f"""
                QPushButton {{
                    background-color: {info['hex']};
                    border: 2px solid #11111B;
                    border-radius: 11px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    border: 2px solid #FFFFFF;
                }}
            """)
            swatch.clicked.connect(lambda _=False, k=key: self.highlight_requested.emit(k))
            layout.addWidget(swatch)

        btn_lookup = QPushButton("Lookup")
        btn_lookup.setToolTip("Dictionary, Wikipedia & Translation Lookup")
        btn_lookup.clicked.connect(self.lookup_requested.emit)
        layout.addWidget(btn_lookup)

        btn_note = QPushButton("Note")
        btn_note.setToolTip("Highlight and attach a custom note")
        btn_note.clicked.connect(self.note_requested.emit)
        layout.addWidget(btn_note)

        btn_tts = QPushButton("▶ Read")
        btn_tts.setToolTip("Read aloud starting from this page")
        btn_tts.clicked.connect(self.tts_from_here_requested.emit)
        layout.addWidget(btn_tts)

        self.adjustSize()
        self.hide()


class DocumentCanvasView(QAbstractScrollArea):
    """
    Virtualized document canvas supporting 3 layout modes with working internal links,
    mouse text selection, search match overlays, and pinch-to-zoom.
    """

    page_changed = Signal(int, int, float)
    highlight_added = Signal(int, str, str, list)
    highlight_deleted = Signal(int)
    highlight_color_changed = Signal(int, str)
    tts_requested_at_page = Signal(int, str)
    lookup_requested = Signal(str)
    note_requested_for_selection = Signal(int, str, list)  # (page_idx, text, rects_list)
    edit_highlight_note_requested = Signal(int)  # index in book_highlights

    PAGE_GAP = 18
    SPREAD_SPINE_GAP = 16
    MARGIN_Y = 20
    MARGIN_X = 20

    def __init__(self):
        super().__init__()

        self.doc: pymupdf.Document | None = None
        self.view_mode: str = "continuous"
        self.zoom = 1.25
        self.theme_name = "Smart Dark (Mocha)"
        self.book_highlights: list[dict] = []
        self.active_tts_page: int | None = None
        self.active_tts_sentence: str | None = None

        self.base_sizes: list[tuple[float, float]] = []
        self.page_y: list[int] = []
        self.page_w: list[int] = []
        self.page_h: list[int] = []
        self.total_height: int = 0
        self.max_width: int = 0
        self.current_page: int = 0

        self._pixmap_cache: dict[int, tuple[float, QPixmap]] = {}
        self._words_cache: dict[int, list] = {}
        self._links_cache: dict[int, list] = {}

        self._is_selecting: bool = False
        self._sel_page: int | None = None
        self._sel_anchor_idx: int | None = None
        self._sel_current_idx: int | None = None
        self._sel_rects_pdf: list[tuple[float, float, float, float]] = []
        self._sel_text: str = ""

        self.search_matches_by_page: dict[int, list[pymupdf.Rect]] = {}
        self.active_search_match: tuple[int, pymupdf.Rect] | None = None

        self.selection_popup = SelectionPopupBar(self.viewport())
        self.selection_popup.copy_requested.connect(self.copy_selected_text)
        self.selection_popup.highlight_requested.connect(self._create_highlight_from_selection)
        self.selection_popup.tts_from_here_requested.connect(self._request_tts_from_selection)
        self.selection_popup.note_requested.connect(self._request_note_from_selection)


        self.verticalScrollBar().setSingleStep(22)
        self.horizontalScrollBar().setSingleStep(22)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(self._on_hscroll)

        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.grabGesture(Qt.PinchGesture)
        self.viewport().grabGesture(Qt.PinchGesture)
        self.viewport().setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.viewport().setStyleSheet("background-color: #11111B;")

        self._sharp_timer = QTimer()
        self._sharp_timer.setSingleShot(True)
        self._sharp_timer.setInterval(140)
        self._sharp_timer.timeout.connect(self._rerender_sharp)

        self._flip_cooldown = QTimer()
        self._flip_cooldown.setSingleShot(True)
        self._flip_cooldown.setInterval(220)

        self.selection_popup.lookup_requested.connect(self._request_lookup_from_selection)

        self._prefetch_timer = QTimer()
        self._prefetch_timer.setSingleShot(True)
        self._prefetch_timer.setInterval(85)
        self._prefetch_timer.timeout.connect(self._prefetch_nearby_pages)

    def set_document(self, doc, highlights: list[dict]):
        self.doc = doc
        self.book_highlights = highlights
        self._pixmap_cache.clear()
        self._words_cache.clear()
        self._links_cache.clear()
        self.base_sizes.clear()
        self.clear_selection()
        self.clear_search_overlays()

        if not self.doc or len(self.doc) == 0:
            self._recalculate_layout()
            self.viewport().update()
            return

        is_reflow = self.doc.is_reflowable
        for i in range(len(self.doc)):
            if is_reflow:
                self.base_sizes.append((float(EPUB_PAGE_WIDTH), float(EPUB_PAGE_HEIGHT)))
            else:
                r = self.doc[i].rect
                self.base_sizes.append((float(r.width), float(r.height)))

        self._recalculate_layout()
        self.viewport().update()

    def set_view_mode(self, mode: str):
        if mode not in ("continuous", "single", "spread"):
            return
        saved_page = self.current_page
        self.view_mode = mode
        self.clear_selection()

        if mode in ("single", "spread") and self.doc:
            self._fit_zoom_for_paginated_mode(saved_page)
        else:
            self._recalculate_layout()

        self.jump_to_page(saved_page)

    def _fit_zoom_for_paginated_mode(self, page_idx: int):
        if not self.base_sizes:
            return
        vp_w = max(400, self.viewport().width() - (self.MARGIN_X * 2))
        vp_h = max(400, self.viewport().height() - (self.MARGIN_Y * 2))

        pages = self._get_spread_pages(page_idx)
        base_w = sum(self.base_sizes[p][0] for p in pages)
        if len(pages) == 2:
            base_w += self.SPREAD_SPINE_GAP / max(0.5, self.zoom)
        base_h = max(self.base_sizes[p][1] for p in pages)

        fit_z = min(vp_w / max(1.0, base_w), vp_h / max(1.0, base_h))
        self.zoom = max(0.5, min(2.5, round(fit_z, 2)))
        self._recalculate_layout()

    @staticmethod
    def _normalize_spread_anchor(page_idx: int) -> int:
        if page_idx <= 0:
            return 0
        return page_idx if (page_idx % 2 == 1) else (page_idx - 1)

    def _get_spread_pages(self, page_idx: int) -> list[int]:
        if not self.doc or len(self.doc) == 0:
            return []
        total = len(self.doc)
        page_idx = max(0, min(page_idx, total - 1))

        if self.view_mode == "single":
            return [page_idx]
        if self.view_mode == "spread":
            anchor = self._normalize_spread_anchor(page_idx)
            if anchor == 0:
                return [0]
            if anchor + 1 < total:
                return [anchor, anchor + 1]
            return [anchor]
        return [page_idx]

    def get_active_paginated_pages(self) -> list[int]:
        return self._get_spread_pages(self.current_page)

    def _recalculate_layout(self):
        self.page_y.clear()
        self.page_w.clear()
        self.page_h.clear()

        y = self.MARGIN_Y
        max_w = 0
        for bw, bh in self.base_sizes:
            w = int(round(bw * self.zoom))
            h = int(round(bh * self.zoom))
            self.page_y.append(y)
            self.page_w.append(w)
            self.page_h.append(h)
            y += h + self.PAGE_GAP
            if w > max_w:
                max_w = w

        if self.view_mode == "continuous":
            self.total_height = max(0, y - self.PAGE_GAP + self.MARGIN_Y)
            self.max_width = max_w + (self.MARGIN_X * 2)
        else:
            pages = self.get_active_paginated_pages()
            if pages:
                spread_w = sum(self.page_w[p] for p in pages)
                if len(pages) == 2:
                    spread_w += self.SPREAD_SPINE_GAP
                spread_h = max(self.page_h[p] for p in pages)
                self.total_height = spread_h + (self.MARGIN_Y * 2)
                self.max_width = spread_w + (self.MARGIN_X * 2)
            else:
                self.total_height = 0
                self.max_width = 0

        self._update_scrollbars()

    def _update_scrollbars(self):
        vp_w = self.viewport().width()
        vp_h = self.viewport().height()
        self.verticalScrollBar().setRange(0, max(0, self.total_height - vp_h))
        self.verticalScrollBar().setPageStep(vp_h)
        self.horizontalScrollBar().setRange(0, max(0, self.max_width - vp_w))
        self.horizontalScrollBar().setPageStep(vp_w)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scrollbars()
        self.selection_popup.hide()

    def _page_target_rect(self, page_idx: int) -> QRect:
        vp_w = self.viewport().width()
        vp_h = self.viewport().height()
        scroll_x = self.horizontalScrollBar().value()
        scroll_y = self.verticalScrollBar().value()

        w = self.page_w[page_idx]
        h = self.page_h[page_idx]

        if self.view_mode == "continuous":
            x = (vp_w - w) // 2 if vp_w > w else (self.MARGIN_X - scroll_x)
            y = self.page_y[page_idx] - scroll_y
            return QRect(x, y, w, h)

        pages = self.get_active_paginated_pages()
        if page_idx not in pages:
            return QRect(-10000, -10000, w, h)

        total_w = sum(self.page_w[p] for p in pages)
        if len(pages) == 2:
            total_w += self.SPREAD_SPINE_GAP
        max_h = max(self.page_h[p] for p in pages)

        start_x = (vp_w - total_w) // 2 if vp_w > total_w else (self.MARGIN_X - scroll_x)
        base_y = (vp_h - max_h) // 2 if vp_h > max_h else (self.MARGIN_Y - scroll_y)

        if len(pages) == 2 and page_idx == pages[1]:
            x = start_x + self.page_w[pages[0]] + self.SPREAD_SPINE_GAP
        else:
            x = start_x

        y = base_y + (max_h - h) // 2
        return QRect(x, y, w, h)

    def _pdf_rect_to_viewport(self, page_idx: int, rect) -> QRectF:
        if isinstance(rect, (tuple, list)):
            x0, y0, x1, y1 = rect[:4]
        else:
            x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
        target = self._page_target_rect(page_idx)
        return QRectF(
            target.x() + x0 * self.zoom,
            target.y() + y0 * self.zoom,
            (x1 - x0) * self.zoom,
            (y1 - y0) * self.zoom,
        )

    def _viewport_point_to_pdf(self, pos: QPoint) -> tuple[int | None, QPointF | None]:
        if not self.page_y or not self.doc:
            return None, None

        if self.view_mode == "continuous":
            doc_y = pos.y() + self.verticalScrollBar().value()
            idx = max(0, bisect.bisect_right(self.page_y, doc_y) - 1)
            candidates = [idx]
        else:
            candidates = self.get_active_paginated_pages()

        for idx in candidates:
            if 0 <= idx < len(self.page_y):
                rect = self._page_target_rect(idx)
                if rect.contains(pos):
                    pdf_x = (pos.x() - rect.x()) / self.zoom
                    pdf_y = (pos.y() - rect.y()) / self.zoom
                    return idx, QPointF(pdf_x, pdf_y)
        return None, None

    def _get_page_words(self, page_idx: int) -> list:
        if page_idx not in self._words_cache and self.doc:
            words = self.doc.load_page(page_idx).get_text("words")
            words.sort(key=lambda w: (w[5], w[6], w[7]))
            self._words_cache[page_idx] = words
        return self._words_cache.get(page_idx, [])

    def _get_page_links(self, page_idx: int) -> list:
        if page_idx not in self._links_cache and self.doc:
            self._links_cache[page_idx] = self.doc.load_page(page_idx).get_links()
        return self._links_cache.get(page_idx, [])

    def _find_word_index_at(self, page_idx: int, pdf_pt: QPointF, max_dist: float = 18.0) -> int | None:
        words = self._get_page_words(page_idx)
        if not words:
            return None
        px, py = pdf_pt.x(), pdf_pt.y()
        best_idx = None
        best_dist = max_dist

        for i, w in enumerate(words):
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            if (x0 - 2) <= px <= (x1 + 2) and (y0 - 2) <= py <= (y1 + 2):
                return i
            dx = max(x0 - px, 0.0, px - x1)
            dy = max(y0 - py, 0.0, py - y1)
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        return best_idx

    # -----------------------------------------------------------------
    # WORKING LINKS / REFERENCES JUMP NAVIGATION
    # -----------------------------------------------------------------
    def _check_and_handle_link_click(self, page_idx: int, pdf_pt: QPointF) -> bool:
        """Checks if a click landed on a PDF/EPUB hyperlink and triggers navigation."""
        links = self._get_page_links(page_idx)
        for link in links:
            r = link.get("from")
            if r and r.x0 <= pdf_pt.x() <= r.x1 and r.y0 <= pdf_pt.y() <= r.y1:
                # 1. Internal page jump destination
                target_page = link.get("page")
                if target_is_int := isinstance(target_page, int) and 0 <= target_page < len(self.doc):
                    self.jump_to_page(target_page)
                    return True
                # 2. URI / Web link
                uri = link.get("uri")
                if uri:
                    webbrowser.open(uri)
                    return True
                # 3. Named destination
                dest = link.get("dest")
                if dest is not None and self.doc:
                    try:
                        pno = self.doc.get_page_number(dest)
                        if isinstance(pno, int) and 0 <= pno < len(self.doc):
                            self.jump_to_page(pno)
                            return True
                    except Exception:
                        pass
        return False

    def step_Next(self):
        if not self.doc:
            return
        total = len(self.doc)
        if self.view_mode == "spread":
            anchor = self._normalize_spread_anchor(self.current_page)
            next_p = 1 if anchor == 0 else anchor + 2
        else:
            next_p = self.current_page + 1

        if next_p < total:
            self.jump_to_page(next_p)

    def step_prev(self):
        if not self.doc:
            return
        if self.view_mode == "spread":
            anchor = self._normalize_spread_anchor(self.current_page)
            if anchor <= 0:
                return
            prev_p = 0 if anchor == 1 else anchor - 2
        else:
            prev_p = self.current_page - 1

        if prev_p >= 0:
            self.jump_to_page(prev_p)

    # -----------------------------------------------------------------
    # MOUSE DRAG-TO-SELECT & LINK CLICK HANDLING
    # -----------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.doc:
            self.selection_popup.hide()
            page_idx, pdf_pt = self._viewport_point_to_pdf(event.position().toPoint())
            if page_idx is not None and pdf_pt is not None:
                # First check if user clicked an internal reference link
                if self._check_and_handle_link_click(page_idx, pdf_pt):
                    event.accept()
                    return

                w_idx = self._find_word_index_at(page_idx, pdf_pt, max_dist=16.0)
                if w_idx is not None:
                    self._is_selecting = True
                    self._sel_page = page_idx
                    self._sel_anchor_idx = w_idx
                    self._sel_current_idx = w_idx
                    self._recompute_selection_geometry()
                    self.viewport().update()
                    event.accept()
                    return
            self.clear_selection()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._is_selecting and self._sel_page is not None and self.doc:
            rect = self._page_target_rect(self._sel_page)
            pdf_x = (pos.x() - rect.x()) / self.zoom
            pdf_y = (pos.y() - rect.y()) / self.zoom
            w_idx = self._find_word_index_at(self._sel_page, QPointF(pdf_x, pdf_y), max_dist=45.0)
            if w_idx is not None and w_idx != self._sel_current_idx:
                self._sel_current_idx = w_idx
                self._recompute_selection_geometry()
                self.viewport().update()
            event.accept()
            return

        page_idx, pdf_pt = self._viewport_point_to_pdf(pos)
        if page_idx is not None and pdf_pt is not None:
            # Check if hovering over a link or text
            links = self._get_page_links(page_idx)
            over_link = any(l.get("from") and l["from"].x0 <= pdf_pt.x() <= l["from"].x1 and l["from"].y0 <= pdf_pt.y() <= l["from"].y1 for l in links)
            if over_link:
                self.viewport().setCursor(Qt.PointingHandCursor)
            else:
                hit = self._find_word_index_at(page_idx, pdf_pt, max_dist=3.0)
                self.viewport().setCursor(Qt.IBeamCursor if hit is not None else Qt.ArrowCursor)
        else:
            self.viewport().setCursor(Qt.ArrowCursor)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_selecting:
            self._is_selecting = False
            if self._sel_text.strip() and self._sel_rects_pdf and self._sel_page is not None:
                self._show_selection_popup()
            else:
                self.clear_selection()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.doc:
            page_idx, pdf_pt = self._viewport_point_to_pdf(event.position().toPoint())
            if page_idx is not None and pdf_pt is not None:
                w_idx = self._find_word_index_at(page_idx, pdf_pt, max_dist=10.0)
                if w_idx is not None:
                    self._sel_page = page_idx
                    self._sel_anchor_idx = w_idx
                    self._sel_current_idx = w_idx
                    self._recompute_selection_geometry()
                    self.viewport().update()
                    self._show_selection_popup()
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        pos = event.pos()
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1E1E2E; color: #CDD6F4;
                border: 1px solid #45475A; border-radius: 6px; padding: 4px;
            }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: #313244; color: #89B4FA; }
        """)

        if self._sel_text.strip():
            act_copy = menu.addAction("Copy Selected Text (Ctrl+C)")
            act_copy.triggered.connect(self.copy_selected_text)
            act_note = menu.addAction("Highlight & Add Note...")
            act_note.triggered.connect(self._request_note_from_selection)
            act_look = menu.addAction("🔍 Lookup Dictionary / Wikipedia / Translate")
            act_look.triggered.connect(self._request_lookup_from_selection)
            menu.addSeparator()
            for key, info in HIGHLIGHT_COLORS.items():
                act_hl = menu.addAction(f"Highlight {info['label']}")
                act_hl.triggered.connect(lambda _=False, k=key: self._create_highlight_from_selection(k))
            menu.addSeparator()
            act_tts = menu.addAction("▶ Read Aloud from This Page")
            act_tts.triggered.connect(self._request_tts_from_selection)

        hit_hl_idx = self._find_highlight_at_pos(pos)

        if hit_hl_idx is not None:
            if not menu.isEmpty():
                menu.addSeparator()
            act_edit_note = menu.addAction("Add / Edit Note...")
            act_edit_note.triggered.connect(
                lambda _=False, idx=hit_hl_idx: self.edit_highlight_note_requested.emit(idx)
            )
            change_menu = menu.addMenu("Change Highlight Color")
            for key, info in HIGHLIGHT_COLORS.items():
                act_c = change_menu.addAction(info["label"])
                act_c.triggered.connect(
                    lambda _=False, idx=hit_hl_idx, k=key: self.highlight_color_changed.emit(idx, k)
                )
            act_del = menu.addAction("Remove Highlight")
            act_del.triggered.connect(lambda _=False, idx=hit_hl_idx: self.highlight_deleted.emit(idx))

        if not menu.isEmpty():
            menu.exec(event.globalPos())

    def _find_highlight_at_pos(self, pos: QPoint) -> int | None:
        page_idx, pdf_pt = self._viewport_point_to_pdf(pos)
        if page_idx is None or pdf_pt is None or not self.doc:
            return None
        px, py = pdf_pt.x(), pdf_pt.y()
        page = self.doc.load_page(page_idx)

        for idx, h in enumerate(self.book_highlights):
            if h.get("page") != page_idx:
                continue
            rects = (
                [pymupdf.Rect(*r) for r in h["rects"]]
                if h.get("rects")
                else PageRenderer._find_text_rects(page, h.get("text", ""))
            )
            for r in rects:
                if r.x0 <= px <= r.x1 and r.y0 <= py <= r.y1:
                    return idx
        return None

    def _recompute_selection_geometry(self):
        self._sel_rects_pdf.clear()
        self._sel_text = ""
        if self._sel_page is None or self._sel_anchor_idx is None or self._sel_current_idx is None:
            return

        words = self._get_page_words(self._sel_page)
        if not words:
            return

        i0 = min(self._sel_anchor_idx, self._sel_current_idx)
        i1 = max(self._sel_anchor_idx, self._sel_current_idx)
        selected = words[i0 : i1 + 1]
        if not selected:
            return

        text_parts = []
        line_rects = []
        cur_key = None
        cur_box = None

        for w in selected:
            x0, y0, x1, y1, word_str, b_no, l_no, _ = w
            text_parts.append(word_str)
            key = (b_no, l_no)
            if key == cur_key and cur_box is not None:
                cur_box = (
                    min(cur_box[0], x0),
                    min(cur_box[1], y0),
                    max(cur_box[2], x1),
                    max(cur_box[3], y1),
                )
            else:
                if cur_box is not None:
                    line_rects.append(cur_box)
                cur_key = key
                cur_box = (float(x0), float(y0), float(x1), float(y1))

        if cur_box is not None:
            line_rects.append(cur_box)

        self._sel_text = " ".join(text_parts)
        self._sel_rects_pdf = line_rects

    def _show_selection_popup(self):
        if self._sel_page is None or not self._sel_rects_pdf:
            return
        first_vp_rect = self._pdf_rect_to_viewport(self._sel_page, self._sel_rects_pdf[0])
        last_vp_rect = self._pdf_rect_to_viewport(self._sel_page, self._sel_rects_pdf[-1])

        self.selection_popup.adjustSize()
        pw = self.selection_popup.width()
        ph = self.selection_popup.height()

        cx = int(first_vp_rect.center().x() - pw / 2)
        cx = max(8, min(self.viewport().width() - pw - 8, cx))

        cy = int(first_vp_rect.top() - ph - 8)
        if cy < 8:
            cy = int(last_vp_rect.bottom() + 8)

        self.selection_popup.move(cx, cy)
        self.selection_popup.show()
        self.selection_popup.raise_()

    def clear_selection(self):
        self._is_selecting = False
        self._sel_page = None
        self._sel_anchor_idx = None
        self._sel_current_idx = None
        self._sel_rects_pdf.clear()
        self._sel_text = ""
        self.selection_popup.hide()
        self.viewport().update()

    def copy_selected_text(self):
        if self._sel_text.strip():
            QApplication.clipboard().setText(self._sel_text.strip())
            self.selection_popup.hide()

    def _create_highlight_from_selection(self, color_key: str):
        if self._sel_page is not None and self._sel_text.strip():
            rects_copy = [list(r) for r in self._sel_rects_pdf]
            self.highlight_added.emit(
                self._sel_page, self._sel_text.strip(), color_key, rects_copy
            )
            self.clear_selection()
    
    def _request_note_from_selection(self):
        if self._sel_page is not None and self._sel_text.strip():
            page = self._sel_page
            txt = self._sel_text.strip()
            rects_copy = [list(r) for r in self._sel_rects_pdf]
            self.clear_selection()
            self.note_requested_for_selection.emit(page, txt, rects_copy)

    def _request_tts_from_selection(self):
        if self._sel_text.strip():
            txt = self._sel_text.strip()
            self.selection_popup.hide()
            self.tts_requested.emit(txt)

    def _request_lookup_from_selection(self):
        if self._sel_text.strip():
            txt = self._sel_text.strip()
            self.selection_popup.hide()
            self.lookup_requested.emit(txt)

    def keyPressEvent(self, event):
        if event.type() == QEvent.KeyPress:
            if (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_C:
                if self._sel_text.strip():
                    self.copy_selected_text()
                    event.accept()
                    return
            elif event.key() == Qt.Key_Escape and self._sel_text:
                self.clear_selection()
                event.accept()
                return

            if event.key() in (Qt.Key_Right, Qt.Key_PageDown) or (
                event.key() == Qt.Key_Space and not (event.modifiers() & Qt.ShiftModifier)
                and self.view_mode != "continuous"
            ):
                self.step_Next()
                event.accept()
                return
            elif event.key() in (Qt.Key_Left, Qt.Key_PageUp) or (
                event.key() == Qt.Key_Space and (event.modifiers() & Qt.ShiftModifier)
                and self.view_mode != "continuous"
            ):
                self.step_prev()
                event.accept()
                return
            elif self.view_mode != "continuous":
                vbar = self.verticalScrollBar()
                if event.key() == Qt.Key_Down and vbar.value() >= vbar.maximum():
                    self.step_Next()
                    event.accept()
                    return
                elif event.key() == Qt.Key_Up and vbar.value() <= vbar.minimum():
                    self.step_prev()
                    event.accept()
                    return

        super().keyPressEvent(event)

    def set_search_overlays(
        self,
        matches_by_page: dict[int, list[pymupdf.Rect]],
        active_match: tuple[int, pymupdf.Rect] | None,
    ):
        self.search_matches_by_page = matches_by_page
        self.active_search_match = active_match
        self.viewport().update()

    def clear_search_overlays(self):
        self.search_matches_by_page.clear()
        self.active_search_match = None
        self.viewport().update()

    def ensure_page_rect_visible(self, page_idx: int, pdf_rect: pymupdf.Rect):
        if not (0 <= page_idx < len(self.page_y)):
            return
        if self.view_mode == "continuous":
            self.current_page = page_idx
            match_doc_y = self.page_y[page_idx] + int(pdf_rect.y0 * self.zoom)
            target_scroll_y = max(0, match_doc_y - (self.viewport().height() // 3))
            self.verticalScrollBar().setValue(target_scroll_y)
            self.page_changed.emit(self.current_page, len(self.doc), self.zoom)
            self.viewport().update()
        else:
            self.jump_to_page(page_idx)

    def _visible_pages_list(self) -> list[int]:
        if not self.page_y:
            return []
        if self.view_mode != "continuous":
            return self.get_active_paginated_pages()

        scroll_y = self.verticalScrollBar().value()
        vp_h = self.viewport().height()
        top_y = scroll_y - 400
        bot_y = scroll_y + vp_h + 400

        start_idx = max(0, bisect.bisect_right(self.page_y, top_y) - 1)
        end_idx = min(len(self.page_y) - 1, bisect.bisect_right(self.page_y, bot_y))
        return list(range(start_idx, end_idx + 1))

    def _on_scroll(self, _value: int):
        self.selection_popup.hide()
        if self.view_mode == "continuous" and self.page_y and self.doc:
            center_y = self.verticalScrollBar().value() + (self.viewport().height() // 2)
            idx = max(0, bisect.bisect_right(self.page_y, center_y) - 1)
            if idx + 1 < len(self.page_y):
                if abs(self.page_y[idx + 1] - center_y) < abs(
                    (self.page_y[idx] + self.page_h[idx] // 2) - center_y
                ):
                    idx += 1
            self.current_page = idx
            self.page_changed.emit(self.current_page, len(self.doc), self.zoom)
            self._prefetch_timer.start()  # <--- Added: schedule idle page pre-fetch
        self.viewport().update()

    def _on_hscroll(self, _value: int):
        self.selection_popup.hide()
        self.viewport().update()

    def _prefetch_nearby_pages(self):
        """Pre-renders 2 pages ahead and 1 page behind during scroll idle for butter-smooth EPUB reading."""
        if not self.doc or not self.page_y or self.view_mode != "continuous":
            return
        vis = self._visible_pages_list()
        if not vis:
            return
        screen = QApplication.primaryScreen()
        dpr = min(1.25, max(1.0, screen.devicePixelRatio() if screen else 1.0))

        candidates = [vis[-1] + 1, vis[-1] + 2, vis[0] - 1]
        for p in candidates:
            if 0 <= p < len(self.doc) and p not in self._pixmap_cache:
                self._pixmap_cache[p] = (self.zoom, self._render_page_pixmap(p, dpr))
                break  # Pre-render 1 page per idle tick so UI event loop stays 100% responsive

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.viewport().rect(), QColor("#11111B"))

        if not self.doc or not self.page_y:
            painter.end()
            return

        visible_pages = self._visible_pages_list()
        if not visible_pages:
            painter.end()
            return

        min_vis, max_vis = min(visible_pages), max(visible_pages)
        for cached_page in list(self._pixmap_cache.keys()):
            # <--- Changed from ±2 to ±6 so pre-fetched pages aren't immediately evicted
            if cached_page < min_vis - 6 or cached_page > max_vis + 6:
                del self._pixmap_cache[cached_page]

        screen = QApplication.primaryScreen()
        dpr = min(1.25, max(1.0, screen.devicePixelRatio() if screen else 1.0))

        for i in visible_pages:
            target_rect = self._page_target_rect(i)

            cached = self._pixmap_cache.get(i)
            if cached is None:
                pix = self._render_page_pixmap(i, dpr)
                self._pixmap_cache[i] = (self.zoom, pix)
            else:
                pix = cached[1]

            painter.save()
            painter.setClipRect(target_rect)
            painter.drawPixmap(target_rect, pix)

            page_matches = self.search_matches_by_page.get(i, [])
            for m_rect in page_matches:
                vp_r = self._pdf_rect_to_viewport(i, m_rect).adjusted(-2, -2, 2, 2)
                is_active = (
                    self.active_search_match is not None
                    and self.active_search_match[0] == i
                    and self.active_search_match[1] == m_rect
                )
                if is_active:
                    painter.fillRect(vp_r, QColor(250, 179, 135, 135))
                    pen = QPen(QColor("#FAB387"), 2.2)
                    painter.setPen(pen)
                    painter.drawRoundedRect(vp_r, 3, 3)
                else:
                    painter.fillRect(vp_r, QColor(249, 226, 175, 85))
                    pen = QPen(QColor("#F9E2AF"), 1.0)
                    painter.setPen(pen)
                    painter.drawRoundedRect(vp_r, 2, 2)

            if self._sel_page == i and self._sel_rects_pdf:
                sel_fill = QColor(137, 180, 250, 95)
                sel_border = QPen(QColor(137, 180, 250, 190), 1.0)
                painter.setPen(sel_border)
                for r_tuple in self._sel_rects_pdf:
                    vp_r = self._pdf_rect_to_viewport(i, r_tuple).adjusted(-1, -1, 1, 1)
                    painter.fillRect(vp_r, sel_fill)
                    painter.drawRect(vp_r)

            painter.setPen(QColor("#313244"))
            painter.drawRect(target_rect.adjusted(0, 0, -1, -1))
            painter.restore()

        painter.end()

    def _render_page_pixmap(self, page_index: int, dpr: float) -> QPixmap:
        page_highlights = [
            h for h in self.book_highlights if h.get("page") == page_index
        ]
        active_tts = (
            self.active_tts_sentence if self.active_tts_page == page_index else None
        )
        return PageRenderer.render_page(
            self.doc,
            page_index,
            self.zoom,
            self.theme_name,
            page_highlights,
            active_tts_text=active_tts,
            dpr=dpr,
        )

    def invalidate_page(self, page_index: int):
        self._pixmap_cache.pop(page_index, None)
        self.viewport().update()

    def invalidate_all(self):
        self._pixmap_cache.clear()
        self.viewport().update()

    def jump_to_page(self, page_index: int):
        if not self.doc or not (0 <= page_index < len(self.page_y)):
            return

        if self.view_mode == "spread":
            self.current_page = self._normalize_spread_anchor(page_index)
        else:
            self.current_page = page_index

        if self.view_mode == "continuous":
            self.verticalScrollBar().setValue(max(0, self.page_y[self.current_page] - 12))
        else:
            self._recalculate_layout()
            self.verticalScrollBar().setValue(0)
            self.horizontalScrollBar().setValue(0)

        self.page_changed.emit(self.current_page, len(self.doc), self.zoom)
        self.viewport().update()

    def apply_zoom_factor(self, factor: float, anchor_pos: QPointF):
        if not self.doc or not self.page_y:
            return

        old_zoom = self.zoom
        new_zoom = max(0.5, min(2.5, self.zoom * factor))
        if abs(new_zoom - old_zoom) < 1e-3:
            return

        ratio = new_zoom / old_zoom
        self.zoom = new_zoom
        self.selection_popup.hide()

        old_scroll_y = self.verticalScrollBar().value()
        old_scroll_x = self.horizontalScrollBar().value()
        ay = anchor_pos.y()
        ax = anchor_pos.x()

        self._recalculate_layout()

        self.verticalScrollBar().setValue(int((old_scroll_y + ay) * ratio - ay))
        self.horizontalScrollBar().setValue(int((old_scroll_x + ax) * ratio - ax))

        self.page_changed.emit(self.current_page, len(self.doc), self.zoom)
        self.viewport().update()
        self._sharp_timer.start()

    def _rerender_sharp(self):
        for page_idx, (cached_zoom, _) in list(self._pixmap_cache.items()):
            if abs(cached_zoom - self.zoom) > 1e-3:
                del self._pixmap_cache[page_idx]
        self.viewport().update()

    def viewportEvent(self, event: QEvent) -> bool:
        etype = event.type()
        if etype == QEvent.NativeGesture and isinstance(event, QNativeGestureEvent):
            if event.gestureType() == Qt.ZoomNativeGesture:
                factor = 1.0 + float(event.value())
                if 0.5 < factor < 2.0 and abs(factor - 1.0) > 1e-4:
                    pos = self.viewport().mapFromGlobal(QCursor.pos())
                    self.apply_zoom_factor(factor, QPointF(pos))
                event.accept()
                return True
        elif etype == QEvent.Gesture and isinstance(event, QGestureEvent):
            pinch = event.gesture(Qt.PinchGesture)
            if isinstance(pinch, QPinchGesture):
                if pinch.changeFlags() & QPinchGesture.ScaleFactorChanged:
                    factor = float(pinch.scaleFactor())
                    if 0.5 < factor < 2.0 and abs(factor - 1.0) > 1e-4:
                        pos = self.viewport().mapFromGlobal(QCursor.pos())
                        self.apply_zoom_factor(factor, QPointF(pos))
                event.accept()
                return True
        return super().viewportEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            pixel_dy = event.pixelDelta().y()
            angle_dy = event.angleDelta().y()
            if pixel_dy != 0:
                factor = 1.0 + (pixel_dy * 0.004)
            elif angle_dy != 0:
                factor = 1.0 + (angle_dy / 1200.0)
            else:
                factor = 1.0
            factor = max(0.80, min(1.25, factor))
            if abs(factor - 1.0) > 1e-4:
                self.apply_zoom_factor(factor, event.position())
            event.accept()
            return

        if self.view_mode != "continuous":
            vbar = self.verticalScrollBar()
            dy = event.angleDelta().y() or event.pixelDelta().y()
            if dy < 0 and vbar.value() >= vbar.maximum():
                if not self._flip_cooldown.isActive():
                    self._flip_cooldown.start()
                    self.step_Next()
                event.accept()
                return
            elif dy > 0 and vbar.value() <= vbar.minimum():
                if not self._flip_cooldown.isActive():
                    self._flip_cooldown.start()
                    self.step_prev()
                event.accept()
                return

        super().wheelEvent(event)

    def _request_lookup_from_selection(self):
        if self._sel_text.strip():
            txt = self._sel_text.strip()
            self.selection_popup.hide()
            self.lookup_requested.emit(txt)

    def _prefetch_nearby_pages(self):
        """Pre-renders 2 pages ahead and 1 page behind during scroll idle for butter-smooth EPUB reading."""
        if not self.doc or not self.page_y or self.view_mode != "continuous":
            return
        vis = self._visible_pages_list()
        if not vis:
            return
        screen = QApplication.primaryScreen()
        dpr = min(1.25, max(1.0, screen.devicePixelRatio() if screen else 1.0))

        candidates = [vis[-1] + 1, vis[-1] + 2, vis[0] - 1]
        for p in candidates:
            if 0 <= p < len(self.doc) and p not in self._pixmap_cache:
              self._pixmap_cache[p] = (self.zoom, self._render_page_pixmap(p, dpr))
              break  # Pre-render 1 page per idle tick so UI event loop stays 100% responsive


class BookCardWidget(QFrame):
    """Clickable book card with outer-only hover border, truncated title, and remove action."""

    clicked = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, book: dict):
        super().__init__()
        self.book_path = book["path"]
        self.full_title = book.get("title", "Untitled")

        self.setObjectName("BookCard")
        self.setFixedSize(215, 348)
        self.setCursor(QCursor(Qt.PointingHandCursor))

        self.setStyleSheet("""
            QFrame#BookCard {
                background-color: #1E1E2E;
                border: 2px solid #313244;
                border-radius: 10px;
            }
            QFrame#BookCard:hover {
                border: 2px solid #89B4FA;
                background-color: #1E1E2E;
            }
            QLabel {
                border: none;
                background: transparent;
                color: #CDD6F4;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(5)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        fmt_badge = QLabel(book.get("format", "BOOK"))
        fmt_badge.setStyleSheet(
            "color: #89B4FA; font-size: 10px; font-weight: bold; background: transparent; border: none;"
        )
        top_row.addWidget(fmt_badge)
        top_row.addStretch(1)

        btn_remove = QPushButton("✕")
        btn_remove.setFixedSize(22, 22)
        btn_remove.setToolTip(f"Remove '{self.full_title}' from library")
        btn_remove.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #6C7086;
                border: none;
                border-radius: 11px;
                font-size: 12px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #F38BA8;
                color: #11111B;
            }
        """)
        btn_remove.clicked.connect(lambda: self.remove_requested.emit(self.book_path))
        top_row.addWidget(btn_remove)
        layout.addLayout(top_row)

        cover_lbl = QLabel()
        cover_lbl.setFixedSize(187, 205)
        cover_lbl.setAlignment(Qt.AlignCenter)
        cover_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if os.path.exists(book.get("cover", "")):
            pix = QPixmap(book["cover"]).scaled(
                187, 205, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            cover_lbl.setPixmap(pix)
        else:
            cover_lbl.setText(book.get("format", "BOOK"))
        layout.addWidget(cover_lbl)

        display_title = self._truncate_title(self.full_title, max_words=6, max_chars=40)
        title_lbl = QLabel(display_title)
        title_lbl.setFont(QFont("Sans Serif", 10, QFont.Bold))
        title_lbl.setWordWrap(True)
        title_lbl.setFixedHeight(36)
        title_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        title_lbl.setToolTip(self.full_title)
        layout.addWidget(title_lbl)

        raw_author = book.get("author", "Unknown Author")
        display_author = raw_author if len(raw_author) <= 28 else raw_author[:25] + "..."
        author_lbl = QLabel(display_author)
        author_lbl.setStyleSheet("color: #A6ADC8; font-size: 11px; border: none; background: transparent;")
        author_lbl.setFixedHeight(16)
        author_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(author_lbl)

        cur = book.get("current_page", 0) + 1
        tot = max(1, book.get("total_pages", 1))
        pct = int((cur / tot) * 100) if cur > 1 else 0

        prog = QProgressBar()
        prog.setRange(0, 100)
        prog.setValue(pct)
        prog.setFixedHeight(6)
        prog.setTextVisible(False)
        prog.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        prog.setStyleSheet("""
            QProgressBar { background-color: #313244; border-radius: 3px; border: none; }
            QProgressBar::chunk { background-color: #A6E3A1; border-radius: 3px; }
        """)
        layout.addWidget(prog)

        pct_lbl = QLabel(f"{pct}%  (Page {cur} of {tot})")
        pct_lbl.setStyleSheet("color: #9399B2; font-size: 11px; border: none; background: transparent;")
        pct_lbl.setFixedHeight(16)
        pct_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(pct_lbl)

    @staticmethod
    def _truncate_title(title: str, max_words: int = 6, max_chars: int = 40) -> str:
        cleaned = " ".join(title.replace("_", " ").split())
        words = cleaned.split(" ")
        truncated = False
        if len(words) > max_words:
            cleaned = " ".join(words[:max_words])
            truncated = True
        if len(cleaned) > max_chars:
            cleaned = cleaned[: max_chars - 3].rstrip()
            truncated = True
        return f"{cleaned}..." if truncated else cleaned

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.book_path)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1E1E2E; color: #CDD6F4;
                border: 1px solid #45475A; border-radius: 6px; padding: 4px;
            }
            QMenu::item { padding: 6px 18px; border-radius: 4px; }
            QMenu::item:selected { background-color: #F38BA8; color: #11111B; font-weight: bold; }
        """)
        act_rem = menu.addAction("Remove from Library")
        act_rem.triggered.connect(lambda: self.remove_requested.emit(self.book_path))
        menu.exec(event.globalPos())