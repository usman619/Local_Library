import os
from pathlib import Path

APP_NAME = "LocalLibrary"

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

APP_DATA_DIR = DATA_HOME / "local_library"
COVERS_CACHE_DIR = CACHE_HOME / "local_library" / "covers"
SANITIZED_EPUB_CACHE_DIR = CACHE_HOME / "local_library" / "sanitized_epubs"
OCR_CACHE_DIR = CACHE_HOME / "local_library" / "ocr_cache"
LIBRARY_DB_FILE = APP_DATA_DIR / "library.json"
SETTINGS_FILE = APP_DATA_DIR / "settings.json"

# Backwards-compatible aliases if referenced by any other module
BASE_CONFIG_DIR = APP_DATA_DIR
COVERS_DIR = COVERS_CACHE_DIR
EPUB_CACHE_DIR = SANITIZED_EPUB_CACHE_DIR
LIBRARY_FILE = LIBRARY_DB_FILE

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
COVERS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
SANITIZED_EPUB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".fb2", ".mobi", ".cbz", ".txt"}

# Reflowable eBook (EPUB, FB2, MOBI) layout configuration
EPUB_PAGE_WIDTH = 600
EPUB_PAGE_HEIGHT = 820
DEFAULT_EPUB_FONTSIZE = 12.5

EPUB_READER_CSS = """
p, li, blockquote, dd {
    line-height: 1.4 !important;
    text-align: justify;
    hyphens: manual;
    -webkit-hyphens: manual;
    widows: 2;
}
[align="left"] { text-align: left; }
[align="right"] { text-align: right; }
[align="center"] { text-align: center; }
[align="justify"] { text-align: justify; }
pre {
    white-space: pre-wrap !important;
}
img {
    max-width: 100% !important;
    height: auto !important;
}
"""

# Smart Dark Mode & Reading Themes: (Background RGB, Foreground RGB)
THEMES = {
    "Original (Light)": None,
    "Smart Dark (Mocha)": ((24, 24, 37), (205, 214, 244)),
    "Dark (Preserve Images)": "preserve_images",
    "OLED Pitch Black": ((8, 8, 12), (218, 224, 242)),
    "Warm Sepia": ((244, 236, 216), (62, 44, 28)),
    "Gruvbox Dark": ((40, 40, 40), (235, 219, 178)),
    "Nord Night": ((46, 52, 64), (216, 222, 233)),
    "Solarized Dark": ((0, 43, 54), (147, 161, 161)),
}

# Multi-Color Highlight Palette
HIGHLIGHT_COLORS = {
    "yellow": {"label": "Amber Gold", "hex": "#F9E2AF", "rgb": (249, 226, 175)},
    "green": {"label": "Emerald Mint", "hex": "#A6E3A1", "rgb": (166, 227, 161)},
    "blue": {"label": "Sky Cyan", "hex": "#89DCEB", "rgb": (137, 220, 235)},
    "pink": {"label": "Rose Pink", "hex": "#F5C2E7", "rgb": (245, 194, 231)},
    "purple": {"label": "Lavender", "hex": "#CBA6F7", "rgb": (203, 166, 247)},
}

# Expanded Curated Neural TTS Voices (Edge-TTS Cloud + Kokoro Local)
TTS_VOICES = [
    # --- US Natural & Multilingual Studio Voices ---
    "edge:en-US-AvaMultilingualNeural (US — Ava Expressive Female)",
    "edge:en-US-AndrewMultilingualNeural (US — Andrew Warm Narrator Male)",
    "edge:en-US-BrianMultilingualNeural (US — Brian Clear Audiobook Male)",
    "edge:en-US-EmmaMultilingualNeural (US — Emma Conversational Female)",
    "edge:en-US-AriaNeural (US — Aria Crisp Female)",
    "edge:en-US-GuyNeural (US — Guy Deep Male)",
    "edge:en-US-ChristopherNeural (US — Christopher Documentary Male)",
    "edge:en-US-JennyNeural (US — Jenny Soft Female)",
    "edge:en-US-SteffanNeural (US — Steffan Technical Male)",
    # --- UK British Voices ---
    "edge:en-GB-SoniaNeural (UK — Sonia British Female)",
    "edge:en-GB-RyanNeural (UK — Ryan British Narrator Male)",
    "edge:en-GB-LibbyNeural (UK — Libby Warm British Female)",
    # --- International English Voices ---
    "edge:en-AU-NatashaNeural (AU — Natasha Australian Female)",
    "edge:en-AU-WilliamNeural (AU — William Australian Male)",
    "edge:en-CA-ClaraNeural (CA — Clara Canadian Female)",
    "edge:en-IN-NeerjaExpressiveNeural (IN — Neerja Expressive Female)",
    "edge:en-PK-UzmaNeural (PK — Uzma Pakistani English Female)",
    # --- Local Offline Kokoro Voices ---
    "kokoro:af_heart (Local Kokoro — Heart Natural Female)",
    "kokoro:af_bella (Local Kokoro — Bella Studio Female)",
    "kokoro:am_adam (Local Kokoro — Adam Warm Male)",
    "kokoro:am_michael (Local Kokoro — Michael Narrator Male)",
    "kokoro:bf_emma (Local Kokoro — Emma British Female)",
    "kokoro:bm_george (Local Kokoro — George British Male)",
    # --- Local Offline Qwen3-TTS 0.6B CustomVoice ---
    # "qwen:aiden (Local Qwen3 — Aiden Sunny American Male)",
    # "qwen:ryan (Local Qwen3 — Ryan Dynamic English Male)",
    # "qwen:serena (Local Qwen3 — Serena Warm Gentle Female)",
    # "qwen:vivian (Local Qwen3 — Vivian Bright Edgy Female)",
    # "qwen:dylan (Local Qwen3 — Dylan Clear Youthful Male)",
    # "qwen:eric (Local Qwen3 — Eric Lively Husky Male)",
    # "qwen:uncle_fu (Local Qwen3 — Uncle Fu Mellow Deep Male)",
    # "qwen:ono_anna (Local Qwen3 — Ono Anna Playful Japanese Female)",
    # "qwen:sohee (Local Qwen3 — Sohee Warm Korean Female)",
]

GLOBAL_QSS = """
QMainWindow, QWidget {
    background-color: #11111B;
    color: #CDD6F4;
    font-family: "Inter", "Segoe UI", "Ubuntu", "Cantarell", sans-serif;
    font-size: 13px;
}

QToolTip {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #89B4FA;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 12px;
}

QScrollBar:vertical {
    background: #11111B;
    width: 10px;
    margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #313244;
    min-height: 32px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #89B4FA;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: #11111B;
    height: 10px;
    margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #313244;
    min-width: 32px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #89B4FA;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

QComboBox {
    background-color: #1E1E2E;
    color: #CDD6F4;
    border: 1px solid #313244;
    border-radius: 7px;
    padding: 5px 10px;
    font-weight: 500;
}
QComboBox:hover {
    border-color: #89B4FA;
    background-color: #252538;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #1E1E2E;
    color: #CDD6F4;
    selection-background-color: #313244;
    selection-color: #89B4FA;
    border: 1px solid #45475A;
    border-radius: 6px;
    padding: 4px;
}

QSlider::groove:horizontal {
    height: 5px;
    background: #313244;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #89B4FA;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #CDD6F4;
    border: 2px solid #89B4FA;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover {
    background: #FFFFFF;
}
"""