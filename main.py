import os
import sys
import multiprocessing
from pathlib import Path


def _ensure_environment_path():
    """
    When launched from a Linux .desktop icon, ~/.bashrc and `conda activate` are not sourced.
    Ensure the active Python environment's bin/ directory and ~/.local/bin are always in PATH
    so edge-tts, ffplay, ffmpeg, and mpv are found.
    """
    py_bin_dir = str(Path(sys.executable).resolve().parent)
    local_bin_dir = str(Path.home() / ".local" / "bin")
    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []

    updated = False
    for d in (py_bin_dir, local_bin_dir):
        if d and os.path.isdir(d) and d not in path_parts:
            path_parts.insert(0, d)
            updated = True

    if updated:
        os.environ["PATH"] = os.pathsep.join(path_parts)


def _is_child_worker_process() -> bool:
    """Returns True if this process is a multiprocessing or CLI worker rather than the main GUI."""
    if __name__ != "__main__":
        return True
    for arg in sys.argv[1:]:
        if arg in ("--multiprocessing-fork", "-c", "-m") or arg.startswith("--multiprocessing"):
            return True
    if os.environ.get("_LOCAL_LIBRARY_WORKER") == "1":
        return True
    return False


def _bootstrap_conda_runtime():
    """
    Preloads the Conda environment's newer libstdc++.so.6 ONLY once for the main GUI process,
    and NEVER inside a TTS/multiprocessing child process (which would spawn a duplicate window).
    """
    if _is_child_worker_process():
        return
    if os.environ.get("_LOCAL_LIBRARY_BOOTSTRAPPED") == "1":
        return

    os.environ["_LOCAL_LIBRARY_BOOTSTRAPPED"] = "1"

    conda_prefix = os.environ.get("CONDA_PREFIX") or str(
        Path(sys.executable).resolve().parent.parent
    )
    candidate_lib = Path(conda_prefix) / "lib" / "libstdc++.so.6"
    if candidate_lib.exists():
        current_preload = os.environ.get("LD_PRELOAD", "")
        if str(candidate_lib) not in current_preload:
            os.environ["LD_PRELOAD"] = (
                f"{candidate_lib}:{current_preload}" if current_preload else str(candidate_lib)
            )
            try:
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
            except Exception:
                pass


_ensure_environment_path()
_bootstrap_conda_runtime()

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from app.config import SUPPORTED_EXTENSIONS
from app.ui.main_window import LocalLibraryWindow
from app.ui.icon import get_app_icon


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("LocalLibrary")
    app.setOrganizationName("LocalLibrary")
    app.setDesktopFileName("local-library")
    app.setWindowIcon(get_app_icon())

    window = LocalLibraryWindow()
    window.show()

    # If a book file was passed as a CLI argument (e.g. from file manager), open it directly
    for arg in sys.argv[1:]:
        if not arg.startswith("-") and os.path.exists(arg):
            if Path(arg).suffix.lower() in SUPPORTED_EXTENSIONS:
                window.open_book(os.path.abspath(arg))
                break

    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()