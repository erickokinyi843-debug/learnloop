from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "dist"
STATIC_FILES = ("index.html", "styles.css", "config.js", "app.js")
PAGES_FILES = ("_headers", "_routes.json")


def copy_static_files(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in STATIC_FILES:
        shutil.copyfile(ROOT / filename, output_dir / filename)


def build_site(output_dir: Path = OUTPUT_DIR) -> None:
    copy_static_files(output_dir)
    for filename in PAGES_FILES:
        shutil.copyfile(ROOT / "cloudflare" / filename, output_dir / filename)


if __name__ == "__main__":
    build_site()
