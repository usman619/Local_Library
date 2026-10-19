import os
from pathlib import Path

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

APP_DATA_DIR = DATA_HOME / "local_library"
COVERS_CACHE_DIR = CACHE_HOME / "local_library" / "covers"
SANITIZED_EPUB_CACHE_DIR = CACHE_HOME / "local_library" / "sanitized_epubs"
LIBRARY_DB_FILE = APP_DATA_DIR / "library.json"

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
COVERS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
SANITIZED_EPUB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Reflowable eBook (EPUB, FB2, MOBI) layout configuration
EPUB_PAGE_WIDTH = 600
EPUB_PAGE_HEIGHT = 820
DEFAULT_EPUB_FONTSIZE = 12.5

EPUB_READER_CSS = """
@page {
    margin: 38pt 36pt 38pt 36pt;
}
html, body {
    margin: 0 !important;
    padding: 0 !important;
    line-height: 1.6 !important;
}
p, dd, dt, blockquote {
    display: block !important;
    line-height: 1.6 !important;
    margin-top: 0.5em !important;
    margin-bottom: 0.6em !important;
    text-align: justify;
}
li {
    line-height: 1.55 !important;
    margin-top: 0.25em !important;
    margin-bottom: 0.25em !important;
}
h1, h2, h3, h4, h5, h6 {
    display: block !important;
    line-height: 1.3 !important;
    margin-top: 1.2em !important;
    margin-bottom: 0.5em !important;
    page-break-after: avoid;
    page-break-inside: avoid;
}
div, section, article, main, aside, header, footer, figure, figcaption {
    display: block !important;
    margin-top: 0.4em;
    margin-bottom: 0.4em;
}
table {
    display: table !important;
    width: 100% !important;
    border-collapse: collapse !important;
    margin-top: 12pt !important;
    margin-bottom: 12pt !important;
    page-break-inside: avoid;
}
thead {
    display: table-header-group !important;
}
tbody {
    display: table-row-group !important;
}
tr {
    display: table-row !important;
    page-break-inside: avoid;
}
th, td {
    display: table-cell !important;
    padding: 5pt 7pt !important;
    border: 1pt solid #6C7086 !important;
    line-height: 1.4 !important;
    text-align: left !important;
    vertical-align: top !important;
}
th {
    font-weight: bold !important;
}
pre, code.block {
    display: block !important;
    margin-top: 8pt !important;
    margin-bottom: 8pt !important;
    padding: 8pt !important;
    border: 1pt solid #585B70 !important;
    white-space: pre-wrap !important;
    word-wrap: break-word !important;
    line-height: 1.35 !important;
    font-size: 0.88em !important;
}
img {
    display: block !important;
    margin-top: 8pt !important;
    margin-bottom: 8pt !important;
    margin-left: auto !important;
    margin-right: auto !important;
    page-break-inside: avoid;
}
"""

# Smart Dark Mode & Reading Themes: (Background RGB, Foreground RGB)
THEMES = {
    "Original (Light)": None,
    "Smart Dark (Mocha)": ((30, 30, 46), (205, 214, 244)),
    "Smart Dark (Keep Images)": "preserve_images",
    "Sepia Warm": ((244, 236, 216), (60, 48, 36)),
    "Nord Dark": ((46, 52, 64), (216, 222, 233)),
}

# Multi-Color Highlight Palette
HIGHLIGHT_COLORS = {
    "yellow": {"label": "Yellow", "rgb": (249, 226, 175), "hex": "#F9E2AF"},
    "green":  {"label": "Green",  "rgb": (166, 227, 161), "hex": "#A6E3A1"},
    "blue":   {"label": "Blue",   "rgb": (137, 220, 235), "hex": "#89DCEB"},
    "pink":   {"label": "Pink",   "rgb": (245, 194, 231), "hex": "#F5C2E7"},
}

# Neural TTS Voices
TTS_VOICES = [
    "edge:en-US-AriaNeural (Edge-TTS Neural Female)",
    "edge:en-US-AndrewMultilingualNeural (Edge-TTS Neural Male)",
    "edge:en-GB-SoniaNeural (Edge-TTS British Female)",
    "kokoro:af_heart (Kokoro Local - Natural Female)",
    "kokoro:am_adam (Kokoro Local - Warm Male)",
]