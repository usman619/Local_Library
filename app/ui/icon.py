import os
from pathlib import Path
from PySide6.QtCore import Qt, QByteArray, QSize, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPainterPath, QColor, QGuiApplication
from PySide6.QtSvg import QSvgRenderer

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
APP_ICON_PATH = PROJECT_ROOT / "images" / "app_icon.png"


# Crisp 24x24 Lucide/Feather vector paths (no emojis anywhere in the UI)
SVG_PATHS: dict[str, str] = {
    "folder": (
        '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
    ),
    "folder_plus": (
        '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
        '<line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>'
    ),
    "folder_open": (
        '<path d="M5 19a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4l2 2h4a2 2 0 0 1 2 2v1"/>'
        '<path d="M5 19h14a2 2 0 0 0 1.84-1.22l2.16-5A2 2 0 0 0 21.16 10H7.84a2 2 0 0 0-1.84 1.22L3.84 16.22A2 2 0 0 0 5 19z"/>'
    ),
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "book_plus": (
        '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'
        '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>'
        '<line x1="12" y1="7" x2="12" y2="13"/><line x1="9" y1="10" x2="15" y2="10"/>'
    ),
    "edit": (
        '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
        '<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>'
    ),
    "trash": (
        '<polyline points="3 6 5 6 21 6"/>'
        '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
        '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>'
    ),
    "close": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "undo": '<polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/>',
    "external": (
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
        '<polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>'
    ),
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "sidebar": (
        '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>'
        '<line x1="9" y1="3" x2="9" y2="21"/>'
    ),
    "chevron_left": '<polyline points="15 18 9 12 15 6"/>',
    "chevron_right": '<polyline points="9 18 15 12 9 6"/>',
    "chevron_up": '<polyline points="18 15 12 9 6 15"/>',
    "chevron_down": '<polyline points="6 9 12 15 18 9"/>',
    "zoom_in": (
        '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'
        '<line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>'
    ),
    "zoom_out": (
        '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'
        '<line x1="8" y1="11" x2="14" y2="11"/>'
    ),
    "bookmark": '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
    "info": (
        '<circle cx="12" cy="12" r="10"/>'
        '<line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>'
    ),
    "play": '<polygon points="6 4 20 12 6 20 6 4" fill="currentColor"/>',
    "pause": (
        '<rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/>'
        '<rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/>'
    ),
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2" fill="currentColor"/>',
    "skip_back": (
        '<polygon points="19 20 9 12 19 4 19 20"/>'
        '<line x1="5" y1="19" x2="5" y2="5"/>'
    ),
    "skip_forward": (
        '<polygon points="5 4 15 12 5 20 5 4"/>'
        '<line x1="19" y1="5" x2="19" y2="19"/>'
    ),
    "lock": (
        '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>'
        '<path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
    ),
    "unlock": (
        '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>'
        '<path d="M7 11V7a5 5 0 0 1 9.9-1"/>'
    ),
    "rotate_ccw": (
        '<polyline points="1 4 1 10 7 10"/>'
        '<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>'
    ),
    "volume": (
        '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>'
        '<path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>'
        '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>'
    ),
    "copy": (
        '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
    ),
    "note": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<polyline points="14 2 14 8 20 8"/>'
        '<line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'
    ),
    "book_open": (
        '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
        '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>'
    ),
    "layers": (
        '<polygon points="12 2 2 7 12 12 22 7 12 2"/>'
        '<polyline points="2 17 12 22 22 17"/>'
        '<polyline points="2 12 12 17 22 12"/>'
    ),
}


def svg_pixmap(name: str, color: str = "#CDD6F4", size: int = 16, stroke_width: float = 2.0) -> QPixmap:
    """Renders a named vector SVG icon into a crisp HiDPI QPixmap."""
    inner = SVG_PATHS.get(name, SVG_PATHS["book_open"])
    svg_xml = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-linecap="round" stroke-linejoin="round">{inner}</svg>'
    ).encode("utf-8")

    app = QGuiApplication.instance()
    dpr = app.primaryScreen().devicePixelRatio() if app and app.primaryScreen() else 1.0
    px_size = max(1, int(round(size * dpr)))

    pixmap = QPixmap(px_size, px_size)
    pixmap.fill(Qt.transparent)

    renderer = QSvgRenderer(QByteArray(svg_xml))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, px_size, px_size))
    painter.end()

    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def svg_icon(name: str, color: str = "#CDD6F4", size: int = 16, stroke_width: float = 2.0) -> QIcon:
    """Returns a QIcon built from a vector SVG path."""
    return QIcon(svg_pixmap(name, color=color, size=size, stroke_width=stroke_width))


def _load_clean_pixmap(size: int = 64) -> QPixmap:
    """Loads the application logo or falls back to a crisp vector book icon."""
    candidates = [
        APP_ICON_PATH,
        PROJECT_ROOT / "assets" / "icon.png",
        PROJECT_ROOT / "icon.png",
    ]
    for c in candidates:
        if c.exists():
            src = QPixmap(str(c)).scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            if not src.isNull():
                out = QPixmap(size, size)
                out.fill(Qt.transparent)
                painter = QPainter(out)
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                pad = size * 0.022
                rect = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
                radius = size * 0.225
                path = QPainterPath()
                path.addRoundedRect(rect, radius, radius)
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, src)
                painter.end()
                return out

    return svg_pixmap("book_open", color="#89B4FA", size=size, stroke_width=1.7)


def get_app_icon() -> QIcon:
    """Returns a multi-resolution window/application QIcon."""
    icon = QIcon()
    for sz in (32, 64, 128, 256, 512):
        icon.addPixmap(_load_clean_pixmap(sz))
    return icon


def export_icon_files(dest_dir: Path) -> Path:
    """Saves the clean PNG icon to ~/.local/share/icons/locallibrary.png for the Linux desktop launcher."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_png = dest_dir / "locallibrary.png"

    # Remove old generated SVG icon if it exists so the desktop environment uses our PNG
    old_svg = dest_dir / "locallibrary.svg"
    if old_svg.exists():
        try:
            old_svg.unlink()
        except OSError:
            pass

    pix = _load_clean_pixmap(512)
    pix.save(str(dest_png), "PNG")
    return dest_png