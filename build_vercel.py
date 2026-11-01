from pathlib import Path

from build_pages import OUTPUT_DIR, PAGES_FILES, copy_static_files


def build_site(output_dir: Path = OUTPUT_DIR) -> None:
    for filename in PAGES_FILES:
        (output_dir / filename).unlink(missing_ok=True)
    copy_static_files(output_dir)


if __name__ == "__main__":
    build_site()
