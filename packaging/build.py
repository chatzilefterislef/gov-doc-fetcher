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


MAC_LAUNCHER = """#!/bin/bash
# Διπλό κλικ σε αυτό το αρχείο για εκκίνηση.
#
# ΓΙΑΤΙ ΧΡΕΙΑΖΕΤΑΙ: η εφαρμογή δεν είναι υπογεγραμμένη από την Apple, οπότε το
# macOS βάζει «quarantine» σε ό,τι κατεβαίνει από το internet και ΣΚΟΤΩΝΕΙ την
# εφαρμογή στην εκκίνηση (SIGKILL) — φαίνεται σαν να μην κάνει τίποτα ή σαν
# malware. Αυτό το script αφαιρεί το quarantine και μετά την ξεκινά.
#
# Το script ΤΟ ΙΔΙΟ εκτελείται από το bash της Apple, που είναι υπογεγραμμένο,
# γι' αυτό δεν μπλοκάρεται όπως το εκτελέσιμο.
cd "$(dirname "$0")" || exit 1

echo "Προετοιμασία πρώτης εκκίνησης…"
chmod -R u+rw GovDocFetcher 2>/dev/null
xattr -dr com.apple.quarantine GovDocFetcher 2>/dev/null
xattr -dr com.apple.quarantine "$0" 2>/dev/null

echo "Εκκίνηση…"
echo
exec ./GovDocFetcher/GovDocFetcher
"""


MAC_STOPPER = """#!/bin/bash
# Διπλό κλικ σε αυτό το αρχείο για διακοπή — εναλλακτικά, κλείσε απλώς το
# παράθυρο του «Εκκίνηση.command».
if pkill -x GovDocFetcher 2>/dev/null; then
    echo "Ο GovDocFetcher σταμάτησε."
else
    echo "Δεν έτρεχε GovDocFetcher."
fi
read -n 1 -s -r -p "Πάτησε ένα πλήκτρο για να κλείσει αυτό το παράθυρο…"
"""


WIN_LAUNCHER = """@echo off
rem Διπλό κλικ σε αυτό το αρχείο για εκκίνηση.
rem Τα Windows εμφανίζουν προειδοποίηση SmartScreen την πρώτη φορά, γιατί η
rem εφαρμογή δεν είναι υπογεγραμμένη: πάτα «More info» και μετά «Run anyway».
cd /d "%~dp0"
echo Εκκίνηση...
echo.
"GovDocFetcher\\GovDocFetcher.exe"
if errorlevel 1 (
  echo.
  echo Κατι πηγε λαθος. Στειλε αυτο το παραθυρο για βοηθεια.
  pause
)
"""


WIN_STOPPER = """@echo off
rem Διπλό κλικ σε αυτό το αρχείο για διακοπή — εναλλακτικά, κλείσε απλώς το
rem παράθυρο του «Εκκίνηση.bat».
taskkill /IM GovDocFetcher.exe /F >nul 2>&1
if errorlevel 1 (
  echo Δεν έτρεχε GovDocFetcher.
) else (
  echo Ο GovDocFetcher σταμάτησε.
)
pause
"""

READ_ME = """Gov Document Fetcher
====================

ΕΚΚΙΝΗΣΗ
--------

  macOS    : διπλό κλικ στο «Εκκίνηση.command»
  Windows  : διπλό κλικ στο «Εκκίνηση.bat»

Ανοίγει ένα παράθυρο με κείμενο και μετά η εφαρμογή στον browser.
ΑΦΗΣΕ ΤΟ ΠΑΡΑΘΥΡΟ ΑΝΟΙΧΤΟ όσο δουλεύεις.


ΔΙΑΚΟΠΗ
-------

  macOS    : διπλό κλικ στο «Διακοπή.command» — ή κλείσε το παράθυρο εκκίνησης
  Windows  : διπλό κλικ στο «Διακοπή.bat» — ή κλείσε το παράθυρο εκκίνησης


ΤΗΝ ΠΡΩΤΗ ΦΟΡΑ ΣΕ macOS  —  ΔΙΑΒΑΣΕ ΤΟ
--------------------------------------

Θα εμφανιστεί παράθυρο που λέει ότι η Apple δεν μπορεί να επιβεβαιώσει την
εφαρμογή, και θα σου δίνει ΜΟΝΟ «Move to Bin» και «Done».
ΜΗΝ πατήσεις «Move to Bin». Πάτα «Done» και κάνε ΕΝΑ από τα δύο:

  Τρόπος Α (πιο γρήγορος, μία εντολή)

    1. Άνοιξε το Terminal (Spotlight: γράψε "Terminal")
    2. Γράψε ΑΚΡΙΒΩΣ αυτό, με ένα κενό στο τέλος:

           xattr -dr com.apple.quarantine

    3. Σύρε τον φάκελο αυτόν μέσα στο παράθυρο του Terminal
       (συμπληρώνεται μόνη της η διαδρομή)
    4. Πάτα Enter
    5. Διπλό κλικ στο «Εκκίνηση.command»

  Τρόπος Β (χωρίς Terminal)

    1. Άνοιξε System Settings > Privacy & Security
    2. Κύλισε κάτω, στην ενότητα Security
    3. Θα δεις μήνυμα ότι μπλοκαρίστηκε το «Εκκίνηση.command»
    4. Πάτα «Open Anyway» και επιβεβαίωσε με κωδικό ή Touch ID
    5. Ξαναπάτα διπλό κλικ στο «Εκκίνηση.command»

Χρειάζεται ΜΟΝΟ την πρώτη φορά. Από εκεί και πέρα ανοίγει κανονικά.

ΣΗΜΕΙΩΣΗ: στα macOS 15 και νεότερα ΔΕΝ δουλεύει πια το παλιό κόλπο
«δεξί κλικ > Open» — η Apple το κατάργησε.


ΤΗΝ ΠΡΩΤΗ ΦΟΡΑ ΣΕ WINDOWS
-------------------------

Στο μήνυμα του SmartScreen πάτα «More info» και μετά «Run anyway».


ΜΗΝ ΤΡΕΞΕΙΣ ΑΠΕΥΘΕΙΑΣ ΤΟ ΕΚΤΕΛΕΣΙΜΟ
------------------------------------

Στο macOS, αν ανοίξεις κατευθείαν το GovDocFetcher μέσα στον φάκελο, το
σύστημα το σκοτώνει χωρίς μήνυμα (φαίνεται σαν να μην κάνει τίποτα).
Χρησιμοποίησε το «Εκκίνηση.command», που φροντίζει αυτό το βήμα.


ΠΟΥ ΠΑΝΕ ΤΑ ΑΡΧΕΙΑ
------------------

  Downloads/GovDocs


ΑΝ ΚΑΤΙ ΧΑΛΑΣΕΙ
---------------

Στείλε το log και τα screenshots:

  macOS    : /tmp/gov_doc_fetcher.log  και  /tmp/gov_debug_*.png
  Windows  : στον προσωρινό φάκελο — η διαδρομή φαίνεται στο παράθυρο


ΠΡΟΣΟΧΗ
-------

Πρόκειται για ζωντανό φορολογικό portal της ΑΑΔΕ. Οι κωδικοί TaxisNet
χρησιμοποιούνται μόνο για τη σύνδεση και δεν αποθηκεύονται πουθενά.
"""


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

    step("Δικαιώματα και υπογραφή")
    # Ο Chromium έχει αρχεία read-only και το copytree τα διατηρεί. Αποτέλεσμα:
    # η εντολή που αφαιρεί το quarantine έσκαγε με «Permission denied …
    # gpu_shader_cache.bin», οπότε ο χρήστης κολλούσε χωρίς διέξοδο.
    for path in DIST_APP.rglob("*"):
        try:
            path.chmod(path.stat().st_mode | 0o600)
        except OSError:
            pass

    if sys.platform == "darwin":
        # Ad-hoc υπογραφή: στο Apple Silicon κάθε binary χρειάζεται έγκυρη
        # υπογραφή για να εκτελεστεί. Best-effort — το Εκκίνηση.command
        # καλύπτει την περίπτωση που δεν αρκεί.
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-",
                        str(DIST_APP / "GovDocFetcher")],
                       cwd=ROOT, capture_output=True)

    step("Στήσιμο πακέτου")
    # Φάκελος-περιτύλιγμα, ώστε η αποσυμπίεση να δίνει ΕΝΑΝ φάκελο με μέσα το
    # εκτελέσιμο και τις οδηγίες — και όχι σκόρπια αρχεία.
    stage = ROOT / "dist" / f"GovDocFetcher-{platform_tag()}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.move(str(DIST_APP), str(stage / "GovDocFetcher"))

    if sys.platform == "darwin":
        launcher = stage / "Εκκίνηση.command"
        launcher.write_text(MAC_LAUNCHER, encoding="utf-8")
        launcher.chmod(0o755)
        print(f"  → {launcher.name}", flush=True)

        stopper = stage / "Διακοπή.command"
        stopper.write_text(MAC_STOPPER, encoding="utf-8")
        stopper.chmod(0o755)
        print(f"  → {stopper.name}", flush=True)
    else:
        launcher = stage / "Εκκίνηση.bat"
        launcher.write_text(WIN_LAUNCHER, encoding="utf-8-sig")
        print(f"  → {launcher.name}", flush=True)

        stopper = stage / "Διακοπή.bat"
        stopper.write_text(WIN_STOPPER, encoding="utf-8-sig")
        print(f"  → {stopper.name}", flush=True)

    (stage / "ΔΙΑΒΑΣΕ ΜΕ.txt").write_text(READ_ME, encoding="utf-8")

    step("Συμπίεση")
    zip_path = shutil.make_archive(str(stage), "zip",
                                   root_dir=stage.parent,
                                   base_dir=stage.name)
    size_mb = Path(zip_path).stat().st_size / 1024 / 1024
    print(f"\n✅ Έτοιμο: {zip_path}  ({size_mb:.0f} MB)")


if __name__ == "__main__":
    main()
