# LocalLibrary

<p align="center">
  <img src="images/app_icon.png" alt="LocalLibrary App Icon" width="128" height="128">
</p>

---

## About the Project

**LocalLibrary** is a high-performance, modern local library manager and document reader built natively for Linux using Python, **PySide6 (Qt6)**, and **PyMuPDF**. Inspired by modern reading tools like Foliate and Evince, LocalLibrary prioritizes a responsive, clean dark-themed UI, smooth high-performance rendering for heavy EPUB and PDF documents, multi-color annotations, Markdown exports, and local/cloud neural text-to-speech (TTS) integration.

![Main Library View](images/main_page.png)

---

## Key Features

* **Responsive Library Homepage:** Auto-reflowing responsive book grid with real-time window resizing, live search filtering by title/author, format filters, and completion status sorting.
* **Virtualized Document Canvas:** Renders multi-hundred page documents effortlessly, bypassing standard widget height limits to completely eliminate page-overlapping bugs at high zoom levels.
* **Trackpad Pinch-to-Zoom & View Modes:** Smooth 60 FPS cursor-anchored trackpad pinch-to-zoom (up to 250%) and `Ctrl + Scroll`, with support for Continuous Scroll, Single Page, and Two-Column Spread view modes.
* **Evince-Style Mouse Selection & Multi-Color Highlights:** Smooth reading-order word selection with a floating action popover and right-click context menu supporting Yellow, Green, Blue, and Pink highlight swatches.
* **Annotation Notes & Markdown Export:** Attach custom Markdown study notes to any highlight, track visual note indicators on the page and sidebar, and export all your notes and annotations to a clean `.md` file.
* **In-Book Search (`Ctrl+F`):** Slide-open search bar with live match counters, instant keyboard navigation (`Enter` / `Shift+Enter`), orange highlight overlays, and sidebar snippet previews.
* **Foliate-Style Popup Lookup:** One-click dictionary definitions, Wikipedia encyclopedic summaries, and multi-language neural translation.
* **TOC Scroll Tracking:** Automatically highlights, bolds, and scrolls to the active chapter in the Contents tree sidebar as you scroll through the book.
* **GPU-Accelerated Neural TTS:** Background daemon supporting Edge-TTS and Kokoro local models, complete with transport controls (`⏮`, `▶`/`⏸`, `⏹`, `⏭`), live speed/pitch sliders, and selection-based speech starting from exact sentences.
* **Reading Statistics & Metadata Inspector:** Live session tracker logging reading time, word count, completion percentage, and estimated time remaining.

---

## Prerequisites and Tech Stack

To run LocalLibrary on your Linux system, ensure you have the following installed:

* **Python 3.10+**
* **PySide6** (Qt6 UI Framework)
* **PyMuPDF** (`pymupdf` for high-speed PDF/EPUB rendering and text extraction)
* **NumPy** (for color lookup table image filters)
* *(Optional)* `edge-tts` or local audio backends for Neural Text-to-Speech.

### Installation and Environment Setup

```bash
# Clone the repository
git clone https://github.com/usman619/Local_Library.git
cd local_library

# Create and activate a virtual environment (or conda env)
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install PySide6 pymupdf numpy edge-tts

# Install requirements
pip install -r requirements.txt
```

## System Architecture

```mermaid
flowchart TB
    subgraph Entry ["OS & Application Bootstrap"]
        MAIN["main.py\n(OS Process Identity & HiDPI Init)"]
        DESKTOP["install_desktop.py / uninstall.py\n(XDG .desktop & Hicolor Icons)"]
    end

    subgraph UI_Layer ["Presentation Layer (PySide6 / Qt6)"]
        MW["LocalLibraryWindow\n(app/ui/main_window.py)"]
        LV["LibraryView\n(Responsive Book Grid & Filters)"]
        RV["ReaderView\n(TOC, Bookmarks, Notes, Search & TTS Bar)"]
        DCV["DocumentCanvasView\n(Virtualized Viewport & Pinch-to-Zoom)"]
        POP["SelectionPopupBar & LookupDialog\n(Highlights, Notes, Dict/Wiki/Translate)"]
    end

    subgraph Core_Engine ["Document & Rendering Engine"]
        EL["EPUB Loader & Foliate Normalizer\n(app/core/epub_loader.py)"]
        PR["HiDPI PageRenderer & NumPy LUTs\n(app/core/renderer.py)"]
        OCR["HybridOCREngine\n(app/core/ocr_engine.py)"]
        MUPDF["PyMuPDF C-Engine & Native Tesseract Bridge\n(pymupdf + libtesseract)"]
    end

    subgraph TTS_Pipeline ["Neural TTS & Look-Ahead Audio Pipeline"]
        TTSW["NeuralTTSWorker (QThread)\n(app/core/tts_worker.py)"]
        PRE["ThreadPool Look-Ahead Prefetcher\n(3-Sentence Concurrent Queue)"]
        SYNTH["Speech Synthesizers\n(Edge-TTS Cloud / Local Kokoro)"]
        AUDIO["Multi-Backend Linux Audio Player\n(mpv / ffplay / paplay / sounddevice)"]
    end

    subgraph Persistence ["XDG Local Storage & Disk Caches"]
        STORE["LibraryStore\n(app/core/store.py)"]
        DB[("library.json & settings.json\n(~/.local/share/local_library/)")]
        CACHE[("Sanitized EPUBs, Covers & OCR Cache\n(~/.cache/local_library/)")]
    end

    %% Flow Connections
    MAIN --> MW
    MW --> LV
    MW --> RV
    RV --> DCV
    DCV --> POP

    %% Store & Data Connections
    MW --> STORE
    RV --> STORE
    STORE <--> DB
    EL <--> CACHE
    OCR <--> CACHE

    %% Document Loading & Rendering Connections
    RV --> EL
    EL --> MUPDF
    DCV --> PR
    PR --> MUPDF
    PR --> OCR

    %% OCR & Text Extraction Connections
    RV --> OCR
    OCR --> MUPDF

    %% TTS Pipeline Connections
    POP -->|Read Selection| RV
    RV -->|TTSUnits & Live Settings| TTSW
    TTSW --> PRE
    PRE --> SYNTH
    TTSW --> AUDIO
    TTSW -->|unit_started Signal| DCV

    %% Styling Definitions
    classDef ui fill:#1E1E2E,stroke:#89B4FA,stroke-width:2px,color:#CDD6F4;
    classDef core fill:#181825,stroke:#A6E3A1,stroke-width:2px,color:#CDD6F4;
    classDef tts fill:#181825,stroke:#F9E2AF,stroke-width:2px,color:#CDD6F4;
    classDef storage fill:#11111B,stroke:#F5C2E7,stroke-width:2px,color:#CDD6F4;

    class MAIN,DESKTOP,MW,LV,RV,DCV,POP ui;
    class EL,PR,OCR,MUPDF core;
    class TTSW,PRE,SYNTH,AUDIO tts;
    class STORE,DB,CACHE storage;
```

## UI 
| **Feature**                    | **Screenshot Preview** |
|-------------------------------|------------------------|
| **Library Homepage**          | ![Library Homepage](images/main_page.png)    |
| **Batch Add Books**           | ![Batch Add Books](images/add_books.png)    |
| **Multi-Folder Import**       | ![Multi-Folder Import](images/import_folder.png)    |
| **Reader View & Spreads**     | ![Reader View & Spreads](images/reader_view.png)    |
| **Selection Popover & Lookup**| ![Selection Popover & Lookup](images/selection_options.png)    |
| **Neural TTS Bar**            | ![Neural TTS Bar](images/tts.png)    |
| **Markdown Notes Export**     | ![Markdown Notes Export](images/export_notes.png)    |
## Running the Application

To start LocalLibrary from source:

```bash
python main.py
```

To install LocalLibrary into your Linux application launcher menu with the custom app icon:

```bash
python install_desktop.py
```
To uninstall LocalLibrary from your linux application launcher menu:
```bash
python uninstall_desktop.py
```
## License
Distributed under the GPLv3 License. See [LICENSE](LICENSE) for more information.