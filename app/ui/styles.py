GLOBAL_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #11111B;
    color: #CDD6F4;
    font-family: "Inter", "Segoe UI", "Ubuntu", "Cantarell", sans-serif;
    font-size: 13px;
}

/* ---- Top Main Toolbar ---- */
QToolBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    padding: 5px 8px;
    spacing: 4px;
}
QToolBar#MainToolbar QPushButton {
    padding: 5px 9px;
    border-radius: 6px;
}
QToolBar#MainToolbar QComboBox {
    padding: 4px 8px;
    border-radius: 6px;
}
QToolBar::separator {
    background-color: #313244;
    width: 1px;
    margin: 4px 6px;
}
QFrame#TTSBar {
    background-color: #181825;
    border-top: 1px solid #313244;
}
QLabel#TTSStatus {
    color: #A6ADC8;
    padding: 2px 6px 4px 6px;
}

/* ---- Buttons ---- */
QPushButton {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 7px 13px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #313244;
    border-color: #89B4FA;
    color: #FFFFFF;
}
QPushButton:pressed {
    background-color: #45475A;
}
QPushButton:checked {
    background-color: #313244;
    border: 1px solid #89B4FA;
    color: #89B4FA;
}
QPushButton:disabled {
    background-color: #181825;
    color: #585B70;
    border-color: #1E1E2E;
}
QPushButton:focus, QComboBox:focus, QLineEdit:focus,
QTreeWidget:focus, QListWidget:focus, QTabBar::tab:focus {
    border: 1px solid #89B4FA;
    outline: none;
}
QLabel#PageStatus {
    color: #A6ADC8;
    background-color: #11111B;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 600;
}

/* ---- ComboBoxes ---- */
QComboBox {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #313244;
    border-radius: 7px;
    padding: 5px 11px;
    font-weight: 500;
}
QComboBox:hover {
    border-color: #89B4FA;
    background-color: #252538;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #45475A;
    selection-background-color: #313244;
    selection-color: #89B4FA;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}

QLineEdit {
    selection-background-color: #89B4FA;
    selection-color: #11111B;
}
QLineEdit::placeholder {
    color: #6C7086;
}

/* ---- Sidebar Tabs & Trees ---- */
QTabWidget::pane {
    border: 1px solid #313244;
    background-color: #181825;
    border-radius: 6px;
}
QTabBar::tab {
    background-color: #11111B;
    color: #A6ADC8;
    padding: 8px 14px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
    margin-right: 2px;
}
QTabBar::tab:hover {
    background-color: #1E1E2E;
    color: #CDD6F4;
}
QTabBar::tab:selected {
    background-color: #181825;
    color: #89B4FA;
    border-bottom: 2px solid #89B4FA;
}

QTreeWidget, QListWidget {
    background-color: #181825;
    color: #CDD6F4;
    border: none;
    padding: 6px;
    outline: none;
    show-decoration-selected: 0;
}
QTreeWidget::branch {
    background: transparent;
}
QTreeWidget::item, QListWidget::item {
    padding: 7px 10px;
    border-radius: 6px;
    margin-bottom: 2px;
    border: none;
}
QTreeWidget::item {
    min-height: 28px;
}
QTreeWidget::item:hover, QListWidget::item:hover {
    background-color: #252538;
    color: #CDD6F4;
}
QTreeWidget::item:selected, QListWidget::item:selected {
    background-color: rgba(49, 50, 68, 0.6);
    color: #89B4FA;
}
QListWidget::item:selected {
    background-color: #313244;
    font-weight: 600;
}

/* ---- Sliders (TTS Speed & Pitch) ---- */
QSlider::groove:horizontal {
    border: none;
    height: 6px;
    background: #313244;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #89B4FA;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #CDD6F4;
    border: 2px solid #89B4FA;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover {
    background: #FFFFFF;
    border-color: #B4BEFE;
}

/* ---- Modern Minimal ScrollBars ---- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0px;
}
QScrollBar::handle:vertical {
    background: #313244;
    min-height: 36px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #585B70;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0px 2px 2px 2px;
}
QScrollBar::handle:horizontal {
    background: #313244;
    min-width: 36px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: #585B70;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* ---- Splitter ---- */
QSplitter::handle {
    background-color: #1E1E2E;
    width: 2px;
}
QSplitter::handle:hover {
    background-color: #89B4FA;
}

/* ---- ToolTips ---- */
QToolTip {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #89B4FA;
    border-radius: 5px;
    padding: 5px 8px;
}
"""