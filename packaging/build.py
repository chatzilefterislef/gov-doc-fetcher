#!/usr/bin/env python3
"""
Χτίζει το πακέτο διανομής για την πλατφόρμα στην οποία τρέχει.

    python packaging/build.py

Παράγει `dist/GovDocFetcher-<πλατφόρμα>.zip` που περιέχει:
    GovDocFetcher/
        GovDocFetcher(.exe)     ← το εκτελέσιμο
        browsers/               ← ο Chromium, ΔΙΠΛΑ στο εκτελέσιμο
        _internal/…             ← ό,τι μάζεψε το PyInstaller

Δεν κάνει cross-compile: για Windows .exe πρέπει να τρέξει σε Windows (βλ. το
GitHub Actions workflow, που το κάνει αυτόματα και για τα δύο λειτουργικά).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BROWSERS = ROOT / "build" / "browsers"      # εδώ κατεβαίνει ο Chromium για το build
DIST_APP = ROOT / "dist" / "GovDocFetcher"


def step(msg: str) -> None:
    print(f"\n{'=' * 60}\n▶ {msg}\n{'=' * 60}", flush=True)


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("  $", " ".join(map(str, cmd)), flush=True)
    result = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})})
    if result.returncode != 0:
        sys.exit(f"❌ Απέτυχε: {' '.join(map(str, cmd))}")


def platform_tag() -> str:
    system = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(
        platform.system(), platform.system().lower())
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine)
    return f"{system}-{arch}"


def main() -> None:
    step("Λήψη Chromium για το πακέτο")
    # Ξεχωριστός φάκελος και ΟΧΙ PLAYWRIGHT_BROWSERS_PATH=0: το «0» τον βάζει
    # μέσα στο site-packages της playwright, όπου το PyInstaller τον μαζεύει ως
    # Mach-O και σκάει («Failed to process binary … MacOS/Chromium»).
    BROWSERS.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "playwright", "install", "chromium"],
        env={"PLAYWRIGHT_BROWSERS_PATH": str(BROWSERS)})

    step("PyInstaller")
    for stale in (ROOT / "dist", ROOT / "build" / "GovDocFetcher"):
        shutil.rmtree(stale, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         str(ROOT / "packaging" / "govdocfetcher.spec")])

    if not DIST_APP.is_dir():
        sys.exit(f"❌ Δεν βρέθηκε το {DIST_APP}")

    step("Αντιγραφή Chromium δίπλα στο εκτελέσιμο")
    target = DIST_APP / "browsers"
    # symlinks=True: ο Chromium του macOS έχει symlinks μέσα στο .app και η
    # αντιγραφή τους ως κανονικά αρχεία διπλασιάζει το μέγεθος και σπάει
    # υπογραφές. Τα δικαιώματα εκτέλεσης διατηρούνται από το copytree.
    shutil.copytree(BROWSERS, target, symlinks=True, dirs_exist_ok=True)
    print(f"  → {target}", flush=True)

    step("Συμπίεση")
    archive = ROOT / "dist" / f"GovDocFetcher-{platform_tag()}"
    zip_path = shutil.make_archive(str(archive), "zip",
                                   root_dir=DIST_APP.parent,
                                   base_dir=DIST_APP.name)
    size_mb = Path(zip_path).stat().st_size / 1024 / 1024
    print(f"\n✅ Έτοιμο: {zip_path}  ({size_mb:.0f} MB)")


if __name__ == "__main__":
    main()
