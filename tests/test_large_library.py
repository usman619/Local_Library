import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from app.ui.library_view import LibraryView


class LargeLibraryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = LibraryView()
        self.view.resize(1400, 900)
        self.view.show()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()

    @staticmethod
    def books(count):
        return {
            str(index): {
                "path": f"/tmp/local-library-regression-{index}.pdf",
                "title": f"Book {index:04d}",
                "author": f"Author {index % 17:02d}",
                "format": ("EPUB", "PDF", "TXT")[index % 3],
                "category": f"Category {index % 11}",
                "current_page": index % 8,
                "total_pages": 20,
                "last_opened": f"2026-01-{(index % 28) + 1:02d}",
            }
            for index in range(count)
        }

    def populate(self, count):
        self.view.populate(
            self.books(count),
            window_width=1400,
            folders=[f"Category {index}" for index in range(11)],
        )
        QTest.qWait(30)
        self.app.processEvents()

    def test_large_library_sizes_and_scroll_ranges(self):
        for count in (100, 500, 1000):
            with self.subTest(count=count):
                self.populate(count)
                self.assertEqual(len(self.view._filtered_books), count)
                self.assertEqual(len(self.view._visible_cards), count)
                self.assertGreater(self.view.scroll.verticalScrollBar().maximum(), 0)
                self.assertGreater(
                    self.view.grid_container.minimumHeight(),
                    self.view.scroll.viewport().height(),
                )

    def test_large_library_filters_and_sorting(self):
        self.populate(1000)

        self.view.search_input.setText("Book 0007")
        self.app.processEvents()
        self.assertEqual(len(self.view._filtered_books), 1)

        self.view.search_input.clear()
        self.view.combo_format.setCurrentText("EPUB")
        self.app.processEvents()
        self.assertEqual(
            len(self.view._filtered_books),
            sum(1 for index in range(1000) if index % 3 == 0),
        )

        self.view.combo_format.setCurrentText("All Formats")
        self.view.combo_sort.setCurrentText("Title (A–Z)")
        self.app.processEvents()
        titles = [book["title"] for book in self.view._filtered_books]
        self.assertEqual(titles, sorted(titles))

    def test_repeated_identical_refresh_does_not_recreate_cards(self):
        self.populate(500)
        original_cards = dict(self.view._visible_cards)
        original_layout_count = self.view.grid_layout.count()

        self.view._rebuild_grid(force=True)
        self.app.processEvents()

        self.assertEqual(self.view.grid_layout.count(), original_layout_count)
        self.assertEqual(
            {path: id(card) for path, card in self.view._visible_cards.items()},
            {path: id(card) for path, card in original_cards.items()},
        )


if __name__ == "__main__":
    unittest.main()
