import os
import shutil
from pathlib import Path


def uninstall_local_library():
    print("Uninstalling LocalLibrary from your Linux system...")

    apps_dir = Path.home() / ".local/share/applications"
    icons_dir = Path.home() / ".local/share/icons"
    local_bin = Path.home() / ".local/bin/locallibrary"
    cache_dir = Path.home() / ".cache/local_library"
    data_dir = Path.home() / ".local/share/local_library"

    targets = [
        apps_dir / "local-library.desktop",
        apps_dir / "locallibrary.desktop",
        apps_dir / "local_library.desktop",
        icons_dir / "local-library.png",
        icons_dir / "locallibrary.png",
        icons_dir / "locallibrary.svg",
        Path.home() / ".local/bin/local-library-launcher",
        local_bin,
    ]
    for sz in (16, 24, 32, 48, 64, 128, 256, 512):
        targets.append(icons_dir / "hicolor" / f"{sz}x{sz}" / "apps" / "local-library.png")
        targets.append(icons_dir / "hicolor" / f"{sz}x{sz}" / "apps" / "locallibrary.png")

    removed_count = 0
    for path in targets:
        if path.exists():
            try:
                path.unlink()
                print(f"✔ Removed: {path}")
                removed_count += 1
            except Exception as e:
                print(f"⚠ Could not remove {path}: {e}")

    if shutil.which("update-desktop-database"):
        os.system(f'update-desktop-database "{apps_dir}" 2>/dev/null')

    print(f"\nSuccessfully removed {removed_count} system integration file(s).")
    print("-" * 50)
    choice = input(
        "Do you also want to delete your local library database, bookmarks, "
        "annotations, and cached EPUBs (~/.cache/local_library & ~/.local/share/local_library)? [y/N]: "
    ).strip().lower()

    if choice == "y":
        for target_dir in (cache_dir, data_dir):
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
                print(f"✔ Deleted data directory: {target_dir}")
        print("✔ Complete data wipe finished.")
    else:
        print("✔ Kept your library database and annotations safe.")

    print("\nLocalLibrary uninstallation complete.")


if __name__ == "__main__":
    uninstall_local_library()