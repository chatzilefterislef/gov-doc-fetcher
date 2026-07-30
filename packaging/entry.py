"""
Entry point της πακεταρισμένης εφαρμογής (PyInstaller).

Διαφέρει από το run.py: εκείνο φτιάχνει virtual environment και εγκαθιστά
πακέτα, που δεν έχει νόημα μέσα σε bundle — εδώ όλα είναι ήδη μέσα.

Τι κάνει:
  1. Δηλώνει PLAYWRIGHT_BROWSERS_PATH=0 ώστε η Playwright να βρει τον Chromium
     ΜΕΣΑ στο bundle (έτσι τον εγκαθιστά και το build)
  2. Βρίσκει ελεύθερη πόρτα — ο χρήστης μπορεί να έχει κάτι άλλο στο 8000
  3. Ανεβάζει τον server και ανοίγει τον browser
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

def _bundled_browsers() -> Path | None:
    """
    Ο φάκελος browsers/ που το build αντιγράφει ΔΙΠΛΑ στο εκτελέσιμο.

    Χωρίς αυτόν η Playwright ψάχνει στην κρυφή μνήμη του χρήστη, που στο
    μηχάνημα του συναδέλφου είναι άδεια, και σκάει με «Executable doesn't
    exist … Please run the following command to download new browsers».
    """
    if not getattr(sys, "frozen", False):
        return None
    here = Path(sys.executable).resolve().parent
    for candidate in (here / "browsers", here.parent / "browsers"):
        if candidate.is_dir():
            return candidate
    return None


# ΠΡΙΝ από κάθε import της Playwright — το env var διαβάζεται στο import time.
_browsers = _bundled_browsers()
if _browsers is not None:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_browsers))


def free_port(preferred: int = 8000) -> int:
    """Η προτιμώμενη πόρτα αν είναι ελεύθερη, αλλιώς ό,τι δώσει το σύστημα."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def main() -> None:
    # Το bundle ξεπακετάρεται σε προσωρινό φάκελο· τα σχετικά imports θέλουν
    # αυτόν τον φάκελο στο path.
    base = getattr(sys, "_MEIPASS", None)
    if base and base not in sys.path:
        sys.path.insert(0, base)

    import uvicorn
    from main import app, DOWNLOADS_DIR

    port = free_port()
    url = f"http://127.0.0.1:{port}"

    print("=" * 58)
    print("  Gov Document Fetcher")
    print("=" * 58)
    print(f"  Διεύθυνση:  {url}")
    print(f"  Αρχεία:     {DOWNLOADS_DIR}")
    print()
    print("  Άφησε αυτό το παράθυρο ανοιχτό όσο δουλεύει η εφαρμογή.")
    print("  Κλείσε το ή πάτα Ctrl+C για διακοπή.")
    print("=" * 58, flush=True)

    def opener() -> None:
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=opener, daemon=True).start()

    # app object απευθείας, ΟΧΙ "main:app": το string import απαιτεί το module
    # να είναι εντοπίσιμο, που στο bundle δεν ισχύει πάντα.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:                      # noqa: BLE001
        # Σε πακεταρισμένη εφαρμογή ένα σφάλμα θα έκλεινε το παράθυρο ακαριαία
        # και ο χρήστης δεν θα έβλεπε τίποτα. Το κρατάμε ανοιχτό.
        print(f"\n❌ Σφάλμα: {exc}\n")
        import traceback
        traceback.print_exc()
        try:
            input("\nΠάτα Enter για κλείσιμο…")
        except EOFError:
            time.sleep(30)
        sys.exit(1)
