#!/usr/bin/env python3
"""English-only gate for the Star-Office-UI fork (SOU-79, Phase 7).

The dashboard is English-only by decision: there is no runtime language
choice, and non-English strings are a defect, not a feature. This script is
the machine check for that contract:

1. Zero CJK / Hangul characters in every *tracked text file*, with exactly
   two exemptions:
     - ``docs/i18n/**``  — the archived translations (SOU-79 AC5: archived,
       not blindly deleted).
     - ``dist/**``       — frozen legacy release artefacts.
2. Forbidden markers are gone — the language switcher (AC1), the memo feature
   (AC4), the forced fake agent in the desktop copy of game.js, and the
   Tauri UI-language sync.
3. Forbidden files no longer exist (``backend/memo_utils.py``,
   ``frontend/memo-bg.webp``).

Usage::

    python3 scripts/check_english_only.py            # full repo sweep
    python3 scripts/check_english_only.py -q         # only the violation summary
    python3 scripts/check_english_only.py frontend/index.html ...  # scope to paths

Exit code 0 = English-only; 1 = violations (each printed with file, line
numbers and counts). Stdlib only; run from anywhere inside the checkout.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Han, kana (incl. hangul Compatibility Jammo), Hangul syllables. Covers the
# issue's proof range [\u4e00-\u9fff] plus Japanese and Korean runs.
CJK_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")

TEXT_EXTS = {
    ".html", ".js", ".py", ".md", ".txt", ".json", ".sh", ".yml", ".yaml",
    ".css", ".svg", ".toml", ".cfg", ".ini", ".sql", ".ts", ".env.example",
}

# The only paths allowed to contain non-English text: archived translations
# (SOU-79 AC5) and frozen legacy release artefacts.
EXEMPT_PREFIXES = ("docs/i18n/", "dist/")

# Per-file literal markers that must not appear once Phase 7 is done.
FORBIDDEN_MARKERS: dict[str, list[str]] = {
    "frontend/index.html": [
        "lang-toggle-group", "setUILanguage", "uiLang = localStorage",
        "localStorage.setItem('uiLang'", "localStorage.getItem('uiLang'",
        "yesterday-memo", "loadMemo", "memo-content", "memo-date", "memo-bg",
    ],
    "frontend/electron-standalone.html": [
        "lang-toggle-group", "setUILanguage", "uiLang = localStorage",
        "localStorage.setItem('uiLang'", "localStorage.getItem('uiLang'",
        "yesterday-memo", "loadMemo", "memo-content", "memo-date", "memo-bg",
        "syncDesktopUiLanguage", "set_ui_lang",
    ],
    "frontend/game.js": ["yesterday-memo", "loadMemo", "memo-content", "memo-date"],
    "electron-shell/standalone-assets/game.js": [
        "yesterday-memo", "loadMemo", "memo-content", "memo-date", "testNika",
    ],
    "backend/app.py": ["yesterday-memo", "memo_utils", "MEMORY_DIR"],
    "scripts/smoke_test.py": ["yesterday-memo"],
}

# Files that must not exist (AC4: no dangling memo remnants).
FORBIDDEN_FILES = ["backend/memo_utils.py", "frontend/memo-bg.webp"]


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return out.splitlines()


def is_text(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in TEXT_EXTS


def sweep(paths: list[str]) -> tuple[list[str], int]:
    """Return (violation lines, total CJK count found)."""
    violations: list[str] = []
    total = 0
    for rel in paths:
        if rel.startswith(EXEMPT_PREFIXES) or not is_text(rel):
            continue
        full = os.path.join(REPO_ROOT, rel)
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            violations.append(f"{rel}: unreadable as utf-8 text ({exc})")
            continue
        hits = CJK_RE.findall(text)
        if hits:
            total += len(hits)
            lines_with_cjk = [
                i + 1 for i, line in enumerate(text.splitlines()) if CJK_RE.search(line)
            ]
            shown = ", ".join(str(n) for n in lines_with_cjk[:5])
            more = f" (+{len(lines_with_cjk) - 5} more)" if len(lines_with_cjk) > 5 else ""
            violations.append(
                f"{rel}: {len(hits)} non-English chars on lines {shown}{more}"
            )
    return violations, total


def check_markers(paths: list[str]) -> list[str]:
    violations = []
    wanted = {os.path.normpath(p) for p in paths}
    for rel, markers in FORBIDDEN_MARKERS.items():
        if os.path.normpath(rel) not in wanted:
            continue
        full = os.path.join(REPO_ROOT, rel)
        if not os.path.exists(full):
            continue
        with open(full, encoding="utf-8") as fh:
            text = fh.read()
        for m in markers:
            if m in text:
                count = text.count(m)
                violations.append(f"{rel}: forbidden marker {m!r} present ({count}x)")
    return violations


def check_forbidden_files(paths: list[str]) -> list[str]:
    violations = []
    for rel in FORBIDDEN_FILES:
        if os.path.exists(os.path.join(REPO_ROOT, rel)):
            violations.append(f"{rel}: file must not exist (memo feature removed, AC4)")
    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-q", "--quiet", action="store_true", help="print summary only")
    ap.add_argument("paths", nargs="*", help="optional subset of repo-relative paths")
    args = ap.parse_args(argv)

    all_files = tracked_files()
    if args.paths:
        subset = {os.path.normpath(p) for p in args.paths}
        files = [f for f in all_files if os.path.normpath(f) in subset]
        files += [f for f in (os.path.normpath(p) for p in args.paths) if f not in files]
    else:
        files = all_files

    violations, total = sweep(files)
    violations += check_markers(files)
    violations += check_markers(list(FORBIDDEN_MARKERS.keys()))  # markers always enforced
    violations += check_forbidden_files(files)
    violations += check_forbidden_files([])

    # de-dup, keep order
    seen = set()
    uniq = [v for v in violations if not (v in seen or seen.add(v))]

    if not uniq:
        print(f"english-only gate PASS ({len(files)} tracked paths swept, 0 non-English chars)")
        return 0

    if args.quiet:
        print(f"english-only gate FAIL: {len(uniq)} violation(s), {total} non-English chars")
        for v in uniq:
            print("  " + v)
    else:
        print(f"english-only gate FAIL: {len(uniq)} violation file(s), {total} non-English chars")
        for v in uniq:
            print("  " + v)
    return 1


if __name__ == "__main__":
    sys.exit(main())
