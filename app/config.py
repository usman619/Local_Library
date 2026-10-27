import os
from pathlib import Path

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

APP_DATA_DIR = DATA_HOME / "local_library"
COVERS_CACHE_DIR = CACHE_HOME / "local_library" / "covers"
SANITIZED_EPUB_CACHE_DIR = CACHE_HOME / "local_library" / "sanitized_epubs"
OCR_CACHE_DIR = CACHE_HOME / "local_library" / "ocr_cache"
LIBRARY_DB_FILE = APP_DATA_DIR / "library.json"
SETTINGS_FILE = APP_DATA_DIR / "settings.json"

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
COVERS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
SANITIZED_EPUB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)

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