import os
import sys

# Configure Linux environment BEFORE importing PySide6, PyMuPDF, or audio libraries.
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
os.environ["QT_QUICK_BACKEND"] = "software"

from PySide6.QtWidgets import QApplication
from app.ui.icon import get_app_icon
from app.ui.main_window import LocalLibraryWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LocalLibrary")
    app.setDesktopFileName("locallibrary")
    app.setWindowIcon(get_app_icon())

    window = LocalLibraryWindow()
    window.setWindowIcon(get_app_icon())

    # If a file path was passed via command line (e.g. "Open With LocalLibrary" in Linux file manager)
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window.open_book(os.path.abspath(sys.argv[1]))

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()