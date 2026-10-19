import shutil
from pathlib import Path
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPainterPath

# Points directly to /home/usman619/python_code/local_library/images/app_icon.png
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
APP_ICON_PATH = PROJECT_ROOT / "images" / "app_icon.png"


def _load_clean_pixmap(size: int = 256) -> QPixmap:
    """
    Loads images/app_icon.png, scales it smoothly, and clips the outer corners
    to a clean squircle so any baked-in checkerboard background in the corners is removed.
    """
    if not APP_ICON_PATH.exists():
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        return pix

    src = QPixmap(str(APP_ICON_PATH)).scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )

    out = QPixmap(size, size)
    out.fill(Qt.transparent)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    # Clip to the dark squircle boundary (insets ~2% to trim outer checkerboard corners)
    pad = size * 0.022
    rect = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
    radius = size * 0.225

    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, src)
    painter.end()

    return out


def get_app_icon() -> QIcon:
    """Returns a multi-resolution QIcon loaded from local_library/images/app_icon.png."""
    if not APP_ICON_PATH.exists():
        return QIcon()

    icon = QIcon()
    for sz in (32, 64, 128, 256, 512):
        icon.addPixmap(_load_clean_pixmap(sz))
    return icon


def export_icon_files(dest_dir: Path) -> Path:
    """Saves the clean PNG icon to ~/.local/share/icons/locallibrary.png for the Linux desktop launcher."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_png = dest_dir / "locallibrary.png"

    # Remove old generated SVG icon if it exists so the desktop environment uses our PNG
    old_svg = dest_dir / "locallibrary.svg"
    if old_svg.exists():
        try:
            old_svg.unlink()
        except OSError:
            pass

    if APP_ICON_PATH.exists():
        pix = _load_clean_pixmap(512)
        pix.save(str(dest_png), "PNG")
    return dest_png