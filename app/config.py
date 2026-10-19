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
    margin: 36pt 32pt 36pt 32pt;
}
body {
    margin: 0;
    padding: 0;
    line-height: 1.55;
}
p, li, dd, dt {
    display: block;
    line-height: 1.55;
    margin-top: 0.45em;
    margin-bottom: 0.55em;
}
h1, h2, h3, h4, h5, h6 {
    display: block;
    line-height: 1.3;
    margin-top: 1.0em;
    margin-bottom: 0.45em;
}
div, section, article, figure, figcaption, blockquote {
    display: block;
    margin-top: 0.5em;
    margin-bottom: 0.6em;
}
table {
    display: table;
    width: 100%;
    border-collapse: collapse;
    margin-top: 0.8em;
    margin-bottom: 0.8em;
}
tr {
    display: table-row;
}
th, td {
    display: table-cell;
    padding: 4pt 6pt;
    border: 1pt solid #666666;
    line-height: 1.4;
}
pre, code {
    display: block;
    margin-top: 0.6em;
    margin-bottom: 0.6em;
    padding: 6pt;
    border: 1pt solid #777777;
    white-space: pre-wrap;
    word-wrap: break-word;
}
img {
    display: block;
    margin-top: 6pt;
    margin-bottom: 6pt;
    margin-left: auto;
    margin-right: auto;
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