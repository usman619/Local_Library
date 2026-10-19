import os
import sys
import ctypes

# Configure Linux environment BEFORE importing PySide6, PyMuPDF, or audio libraries.
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
os.environ["QT_QUICK_BACKEND"] = "software"


def _set_os_process_identity():
    """
    Ensures LocalLibrary appears as 'LocalLibrary' with its custom icon
    in Linux Mission Center / System Monitor and Windows Task Manager.
    """
    sys.argv[0] = "locallibrary"

    # 1. Linux: Set kernel process comm name (/proc/self/comm) & setproctitle if available
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL("libc.so.6")
            PR_SET_NAME = 15
            libc.prctl(PR_SET_NAME, ctypes.c_char_p(b"locallibrary"), 0, 0, 0)
        except Exception:
            pass
        try:
            import setproctitle
            setproctitle.setproctitle("locallibrary")
        except ImportError:
            pass

    # 2. Windows: Set explicit AppUserModelID so Task Manager & Taskbar show LocalLibrary icon/name
    elif sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "LocalLibrary.eBookReader.1.0"
            )
        except Exception:
            pass


_set_os_process_identity()

from PySide6.QtWidgets import QApplication
from app.ui.icon import get_app_icon
from app.ui.main_window import LocalLibraryWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("locallibrary")
    app.setApplicationDisplayName("LocalLibrary")
    app.setOrganizationName("LocalLibrary")
    app.setDesktopFileName("locallibrary")

    app_icon = get_app_icon()
    app.setWindowIcon(app_icon)

    window = LocalLibraryWindow()
    window.setWindowIcon(app_icon)

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window.open_book(os.path.abspath(sys.argv[1]))

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()