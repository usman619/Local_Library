from pathlib import Path
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen


def create_bookshelf_pixmap(size: int = 256) -> QPixmap:
    """
    Renders the minimalist 3-bar bookshelf icon:
    - Rounded light background (#F5F5F7)
    - Left vertical pill bar
    - Middle tilted pill bar leaning from bottom-left to top-right
    - Right vertical pill bar
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 1. Soft off-white rounded square background
    pad = size * 0.04
    bg_rect = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
    radius = size * 0.22
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#F4F4F6"))
    painter.drawRoundedRect(bg_rect, radius, radius)

    # 2. Three thick round-capped black pill bars
    stroke_w = size * 0.115
    pen = QPen(QColor("#09090B"), stroke_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)

    top_y = size * 0.265
    bot_y = size * 0.735

    # Left vertical bar
    left_x = size * 0.315
    painter.drawLine(QPointF(left_x, top_y), QPointF(left_x, bot_y))

    # Middle tilted bar (leans from bottom-left near left bar to top-right near right bar)
    mid_bot_x = size * 0.440
    mid_top_x = size * 0.545
    painter.drawLine(QPointF(mid_top_x, top_y), QPointF(mid_bot_x, bot_y))

    # Right vertical bar
    right_x = size * 0.675
    painter.drawLine(QPointF(right_x, top_y), QPointF(right_x, bot_y))

    painter.end()
    return pixmap


def get_app_icon() -> QIcon:
    """Returns a multi-resolution QIcon for Qt6 window & taskbar."""
    icon = QIcon()
    for sz in (32, 64, 128, 256):
        icon.addPixmap(create_bookshelf_pixmap(sz))
    return icon


def export_icon_files(dest_dir: Path) -> tuple[Path, Path]:
    """Saves both PNG and SVG versions of the icon for Linux desktop integration."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    png_path = dest_dir / "locallibrary.png"
    svg_path = dest_dir / "locallibrary.svg"

    create_bookshelf_pixmap(256).save(str(png_path), "PNG")

    svg_content = """<?xml version="1.0" encoding="UTF-8"?>
<svg width="256" height="256" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="10" width="236" height="236" rx="56" fill="#F4F4F6"/>
  <g stroke="#09090B" stroke-width="29.5" stroke-linecap="round">
    <line x1="80.6" y1="67.8" x2="80.6" y2="188.2"/>
    <line x1="139.5" y1="67.8" x2="112.6" y2="188.2"/>
    <line x1="172.8" y1="67.8" x2="172.8" y2="188.2"/>
  </g>
</svg>
"""
    svg_path.write_text(svg_content.strip(), encoding="utf-8")
    return png_path, svg_path