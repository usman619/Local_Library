GLOBAL_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #11111B;
    color: #CDD6F4;
}
QToolBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    spacing: 8px;
    padding: 6px;
}
QPushButton, QComboBox {
    background-color: #313244;
    color: #CDD6F4;
    border: 1px solid #45475A;
    border-radius: 6px;
    padding: 5px 12px;
    font-weight: 500;
}
QPushButton:hover, QComboBox:hover {
    background-color: #45475A;
    border-color: #89B4FA;
}
QToolTip {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #89B4FA;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}
QTabWidget::pane {
    border: 1px solid #313244;
    background: #181825;
}
QTabBar::tab {
    background: #181825;
    color: #A6ADC8;
    padding: 8px 12px;
}
QTabBar::tab:selected {
    background: #313244;
    color: #89B4FA;
    font-weight: bold;
}
QTreeWidget, QListWidget {
    background-color: #181825;
    border: none;
    color: #CDD6F4;
}
QScrollBar:vertical {
    background: #181825;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #45475A;
    border-radius: 5px;
    min-height: 30px;
}
"""