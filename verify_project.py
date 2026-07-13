import subprocess
import sys
from pathlib import Path

from reality.paths import HTML_PATH, ROOT

PRESERVED_GENERATED_FILES = [
    HTML_PATH,
]


def run_command(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True, text=True)


def snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    snapshots = {}
    for path in paths:
        snapshots[path] = path.read_bytes() if path.exists() else None
    return snapshots


def restore_files(snapshots: dict[Path, bytes | None]) -> None:
    for path, content in snapshots.items():
        if content is None:
            if path.exists():
                path.unlink()
            continue
        path.write_bytes(content)


def main() -> None:
    python = sys.executable
    snapshots = snapshot_files(PRESERVED_GENERATED_FILES)
    try:
        run_command([python, "build_html.py", "--ads-only"])
        restore_files(snapshots)
        run_command([python, "validate_real_estate_data.py"])
        run_command([python, "-m", "pytest", "-q", "--ignore=tests/test_drawer_ui.py"])
        run_command([python, "-m", "ruff", "check", "."])
        run_command([python, "tests/test_drawer_ui.py"])
        run_command(["git", "diff", "--check"])
    finally:
        restore_files(snapshots)


if __name__ == "__main__":
    main()
