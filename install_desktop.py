import os
import sys
import shutil
from pathlib import Path
from PySide6.QtWidgets import QApplication
from app.ui.icon import export_icon_files, APP_ICON_PATH, _load_clean_pixmap


def install_linux_desktop_launcher():
    app = QApplication(sys.argv)
    project_root = Path(__file__).resolve().parent
    main_script = project_root / "main.py"
    python_bin = Path(sys.executable).resolve()

    if not APP_ICON_PATH.exists():
        print(f"⚠ Warning: Could not find icon at {APP_ICON_PATH}")

    # 1. Install into standard XDG hicolor icon directories so Mission Center,
    #    GNOME, KDE, and docks automatically resolve 'Icon=locallibrary'
    icons_base = Path.home() / ".local/share/icons"
    png_path = export_icon_files(icons_base)

    for sz in (48, 64, 128, 256, 512):
        hicolor_dir = icons_base / "hicolor" / f"{sz}x{sz}" / "apps"
        hicolor_dir.mkdir(parents=True, exist_ok=True)
        _load_clean_pixmap(sz).save(str(hicolor_dir / "locallibrary.png"), "PNG")

    # 2. Create ~/.local/bin/locallibrary wrapper executable so process launches as 'locallibrary'
    local_bin_dir = Path.home() / ".local/bin"
    local_bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper_script = local_bin_dir / "locallibrary"
    wrapper_content = f"""#!/usr/bin/env bash
exec -a locallibrary "{python_bin}" "{main_script}" "$@"
"""
    wrapper_script.write_text(wrapper_content, encoding="utf-8")
    os.chmod(wrapper_script, 0o755)

    # 3. Write standard XDG .desktop file matching StartupWMClass=locallibrary
    apps_dir = Path.home() / ".local/share/applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = apps_dir / "locallibrary.desktop"

    desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=LocalLibrary
GenericName=eBook & PDF Reader
Comment=Read PDF, EPUB, FB2, MOBI, and CBZ books with GPU Neural TTS
Exec={wrapper_script} %f
TryExec={wrapper_script}
WorkingDirectory={project_root}
Icon={png_path}
Terminal=false
StartupNotify=true
Categories=Office;Viewer;Literature;Education;
MimeType=application/pdf;application/epub+zip;application/x-fictionbook+xml;application/x-mobipocket-ebook;application/vnd.comicbook+zip;
StartupWMClass=locallibrary
X-GNOME-WMClass=locallibrary
"""
    desktop_file.write_text(desktop_content, encoding="utf-8")
    os.chmod(desktop_file, 0o755)

    #Also remove any old local_library.desktop duplicate if present
    old_dup = apps_dir / "local_library.desktop"
    if old_dup.exists():
        old_dup.unlink()

    if shutil.which("update-desktop-database"):
        os.system(f'update-desktop-database "{apps_dir}" 2>/dev/null')
    if shutil.which("gtk-update-icon-cache"):
        os.system(f'gtk-update-icon-cache -f -t "{icons_base / "hicolor"}" 2>/dev/null')

    print(f"✔ Installed wrapper binary: {wrapper_script}")
    print(f"✔ Installed icons to: {png_path} and hicolor theme")
    print(f"✔ Installed Linux desktop launcher: {desktop_file}")


if __name__ == "__main__":
    install_linux_desktop_launcher()