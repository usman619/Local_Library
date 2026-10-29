import os
import sys
import stat
import shutil
import subprocess
from pathlib import Path


def install_desktop_entry():
    repo_dir = Path(__file__).resolve().parent
    main_script = repo_dir / "main.py"
    python_exe = Path(sys.executable).resolve()
    # If current Python interpreter is missing PySide6, search for pydev environment
    try:
        import PySide6  # noqa: F401
    except ImportError:
        for cand in (
            Path.home() / "anaconda3" / "envs" / "pydev" / "bin" / "python",
            Path.home() / "miniconda3" / "envs" / "pydev" / "bin" / "python",
            python_exe.parent.parent / "envs" / "pydev" / "bin" / "python",
        ):
            if cand.exists():
                python_exe = cand.resolve()
                break

    python_bin_dir = python_exe.parent
    conda_prefix = python_bin_dir.parent

    apps_dir = Path.home() / ".local" / "share" / "applications"
    icons_base = Path.home() / ".local" / "share" / "icons"
    icons_dir = icons_base / "hicolor" / "256x256" / "apps"
    bin_dir = Path.home() / ".local" / "bin"

    apps_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    # 1. Use images/app_icon.png as the primary application icon
    icon_src = repo_dir / "images" / "app_icon.png"
    if not (icon_src.exists() and icon_src.stat().st_size > 0):
        for candidate in (
            repo_dir / "assets" / "icon.png",
            repo_dir / "icon.png",
            repo_dir / "app_icon.png",
        ):
            if candidate.exists() and candidate.stat().st_size > 0:
                icon_src = candidate
                break

    # 2. Export multi-resolution icons across standard XDG hicolor directories
    try:
        subprocess.run(
            [
                str(python_exe),
                "-c",
                f"from pathlib import Path; from app.ui.icon import export_icon_files; export_icon_files(Path(r'{icons_base}'))",
            ],
            cwd=str(repo_dir),
            check=False,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    icon_target = icons_dir / "local-library.png"
    if icon_src.exists() and not icon_target.exists():
        icon_target.write_bytes(icon_src.read_bytes())

    root_icon = icons_base / "local-library.png"
    if icon_src.exists() and not root_icon.exists():
        root_icon.write_bytes(icon_src.read_bytes())

    icon_value = str(icon_target) if icon_target.exists() else (str(root_icon) if root_icon.exists() else "local-library")

    # 3. Create dedicated launcher script that preserves Conda/venv PATH
    launcher_path = bin_dir / "local-library-launcher"
    launcher_content = f"""#!/usr/bin/env bash
export PATH="{python_bin_dir}:$HOME/.local/bin:$PATH"
export CONDA_PREFIX="{conda_prefix}"
export _LOCAL_LIBRARY_BOOTSTRAPPED="0"
cd "{repo_dir}" || exit 1
exec "{python_exe}" "{main_script}" "$@"
"""
    launcher_path.write_text(launcher_content, encoding="utf-8")
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # 4. Write .desktop launcher file
    desktop_file = apps_dir / "local-library.desktop"
    desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=LocalLibrary
GenericName=eBook & PDF Reader
Comment=Linux eBook & PDF Reader with Neural TTS and Smart Dark Mode
Exec="{launcher_path}" %F
Path={repo_dir}
Icon={icon_value}
Terminal=false
StartupNotify=true
StartupWMClass=local-library
Categories=Office;Viewer;Literature;Education;
MimeType=application/pdf;application/epub+zip;application/x-fictionbook+xml;application/x-mobipocket-ebook;application/vnd.comicbook+zip;
"""
    desktop_file.write_text(desktop_content, encoding="utf-8")
    desktop_file.chmod(desktop_file.stat().st_mode | stat.S_IEXEC)

    # Remove any old duplicates
    for old_desktop in (apps_dir / "locallibrary.desktop", apps_dir / "local_library.desktop"):
        if old_desktop.exists() and old_desktop != desktop_file:
            try:
                old_desktop.unlink()
            except OSError:
                pass

    # 5. Refresh system desktop and icon databases
    if shutil.which("gtk-update-icon-cache"):
        try:
            subprocess.run(
                ["gtk-update-icon-cache", "-f", "-t", str(icons_base / "hicolor")],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(
                ["update-desktop-database", str(apps_dir)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    print(f"✔ Installed launcher script: {launcher_path}")
    print(f"✔ Installed icon to: {icon_target}")
    print(f"✔ Installed desktop entry : {desktop_file}")
    print(f"✔ Bound Python environment: {python_exe}")


if __name__ == "__main__":
    install_desktop_entry()