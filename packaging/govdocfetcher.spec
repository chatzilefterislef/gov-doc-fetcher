# PyInstaller spec — κοινό για Windows και macOS.
#
# Χτίζεται σε ΦΑΚΕΛΟ (onedir) και όχι σε ένα αρχείο (onefile). Ο λόγος είναι
# πραγματικός: με τον Chromium μέσα το πακέτο είναι ~500 MB, και το onefile
# ξεπακετάρει ΟΛΟΚΛΗΡΟ αυτό το μέγεθος σε προσωρινό φάκελο σε ΚΑΘΕ εκκίνηση —
# μισό λεπτό αναμονής κάθε φορά, και απαίτηση για 500 MB ελεύθερου χώρου. Το
# onedir ξεπακετάρεται μία φορά (unzip) και μετά ανοίγει ακαριαία.
#
# Ο Chromium ΔΕΝ μπαίνει μέσα στο bundle, μπαίνει ΔΙΠΛΑ του (φάκελος browsers/)
# από το packaging/build.py. Δύο λόγοι, και οι δύο πραγματικοί:
#   • Το PyInstaller προσπαθεί να επεξεργαστεί κάθε Mach-O που μαζεύει και
#     σκάει στο Chromium.app: «Failed to process binary … MacOS/Chromium».
#   • Ακόμη κι αν περνούσε, τα αρχεία data χάνουν το bit εκτέλεσης και ο
#     Chromium δεν θα ξεκινούσε.
# Το entry.py δείχνει στο runtime τον φάκελο browsers/ που βρίσκεται δίπλα.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent          # noqa: F821  (SPECPATH δίνεται από PyInstaller)

datas = [
    (str(ROOT / "templates"), "templates"),
]
binaries = []
hiddenimports = []

def _not_browser(entry):
    """Αφήνει έξω ό,τι ανήκει στους κατεβασμένους browsers."""
    src = str(entry[0])
    return ".local-browsers" not in src


# Η playwright κουβαλά τον node driver ως data/binaries — αυτόν τον θέλουμε
# μέσα. Τους browsers τους κρατάμε ΕΞΩ (δες σχόλιο παραπάνω).
for pkg in ("playwright",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += [d for d in pkg_datas if _not_browser(d)]
    binaries += [b for b in pkg_binaries if _not_browser(b)]
    hiddenimports += pkg_hidden

# Ο uvicorn φορτώνει δυναμικά τα protocol/loop implementations, οπότε το
# PyInstaller δεν τα βλέπει με στατική ανάλυση.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "automation", "automation.base", "automation.myaade",
    "anyio", "h11", "click",
]

a = Analysis(                          # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)                      # noqa: F821

exe = EXE(                             # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GovDocFetcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                         # UPX χαλάει υπογεγραμμένα binaries
    console=True,                      # το παράθυρο δείχνει την πρόοδο
)

coll = COLLECT(                        # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GovDocFetcher",
)

# Δεν φτιάχνουμε .app bundle: ο φάκελος browsers/ πρέπει να είναι δίπλα στο
# εκτελέσιμο, και μέσα σε .app θα έπρεπε να μπει στο Contents/Resources με
# επιπλέον post-processing — χωρίς κέρδος, αφού η εφαρμογή ανοίγει browser και
# δεν έχει δικό της παράθυρο. Ο φάκελος onedir λειτουργεί και με διπλό κλικ.
