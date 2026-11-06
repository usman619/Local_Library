from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtGui import QImage, QImageReader


def _read_cover(path: str, size: tuple[int, int]) -> QImage:
    if not Path(path).is_file():
        return QImage()
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return image
    return image.scaled(
        *size,
        aspectMode=Qt.KeepAspectRatio,
        mode=Qt.SmoothTransformation,
    )


class CoverLoader(QObject):
    """Loads and scales cover images off the GUI thread with a bounded cache."""

    loaded = Signal(str, QImage)

    def __init__(self, max_items: int = 128, parent=None):
        super().__init__(parent)
        self._cache: OrderedDict[str, QImage] = OrderedDict()
        self._pending: dict[str, Future] = {}
        self._max_items = max_items
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cover")
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(30)
        self._poll_timer.timeout.connect(self._collect_finished)

    def request(self, path: str, size: tuple[int, int] = (190, 206)):
        if not path:
            return
        cached = self._cache.get(path)
        if cached is not None:
            self._cache.move_to_end(path)
            self.loaded.emit(path, cached)
            return
        if path not in self._pending:
            self._pending[path] = self._executor.submit(_read_cover, path, size)
        self._poll_timer.start()

    def _collect_finished(self):
        for path, future in list(self._pending.items()):
            if not future.done():
                continue
            del self._pending[path]
            try:
                image = future.result()
            except Exception:
                image = QImage()
            if not image.isNull():
                self._cache[path] = image
                self._cache.move_to_end(path)
                while len(self._cache) > self._max_items:
                    self._cache.popitem(last=False)
            self.loaded.emit(path, image)
        if not self._pending:
            self._poll_timer.stop()

    def close(self):
        self._poll_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
