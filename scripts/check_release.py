"""Check tracked release files for raw EDA inputs, datasets, and credential literals."""
from __future__ import annotations

import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SECRETS = re.compile(rb"AIza[0-9A-Za-z_-]{35}|sk-(?:proj-|or-v1-)?[0-9A-Za-z_-]{30,}|gh[pousr]_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
FORBIDDEN_PARTS = {"datasets", "dist", "output", "__macosx", ".venv", ".test_dependencies"}
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".7z", ".rar", ".brd", ".sch")


def check_file(relative: str, content: bytes) -> str | None:
    path = Path(relative)
    lowered = relative.lower()
    if any(part.lower() in FORBIDDEN_PARTS for part in path.parts):
        return "private data, release archive, or generated directory"
    if lowered.endswith(ARCHIVE_SUFFIXES) or re.search(r"\.(?:brd|sch)(?:\.|$)", lowered):
        return "raw EDA source or archive"
    if path.name.lower() in {"keys", ".env"} or (path.name.lower().startswith(".env.") and path.name != ".env.example"):
        return "credential file"
    if SECRETS.search(content):
        return "credential-like literal"
    return None


def main() -> None:
    # Always inspect the staged index as well as current file contents. This
    # catches a forbidden file even when an ignore rule would hide it later.
    relative_paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    checked = 0
    errors = []
    for relative in filter(None, relative_paths):
        checked += 1
        for content in ((ROOT / relative).read_bytes(), subprocess.check_output(["git", "show", f":{relative}"], cwd=ROOT)):
            problem = check_file(relative, content)
            if problem:
                errors.append(f"{relative}: {problem}")
                break
    if not checked:
        raise SystemExit("No tracked files to inspect")
    if errors:
        raise SystemExit("Release check failed:\n" + "\n".join(errors))
    print(f"Release check passed: {checked} tracked files, no raw EDA inputs or credential literals detected")


if __name__ == "__main__":
    main()
