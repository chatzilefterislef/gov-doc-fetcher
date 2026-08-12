#!/usr/bin/env python3
"""
Έλεγχοι για τη λογική εντοπισμού στοιχείων στα portals της ΑΑΔΕ.

    python3 tests/test_portal_logic.py

Κάθε έλεγχος αντιστοιχεί σε ΠΡΑΓΜΑΤΙΚΟ bug που εμφανίστηκε και κόστισε χρόνο.
Τρέξ' τους πριν αλλάξεις οτιδήποτε στο _action_cells / _rows_with_action /
_pick_declaration — εκεί συγκεντρώνονται όλες οι παγίδες.

Δεν χρειάζεται σύνδεση στο portal: το DOM στήνεται τοπικά και αναπαράγει τη
δομή που είδαμε στις πραγματικές σελίδες.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright          # noqa: E402
from automation.base import gr_norm, label_norm            # noqa: E402
from automation.myaade import MyAADEAutomation             # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, title: str, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {title}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(title)


class Probe(MyAADEAutomation):
    """MyAADEAutomation χωρίς browser lifecycle — μόνο η λογική εντοπισμού."""

    def __init__(self, page):
        self.page = page
        self.logs: list[str] = []

    def log(self, msg, level="info"):
        self.logs.append(msg)

    async def _settle(self):
        pass


# ── Ελληνικό κείμενο ────────────────────────────────────────────────────────

def test_greek_text() -> None:
    # Το upper() ΚΡΑΤΑΕΙ τους τόνους — είχε σπάσει φίλτρο σιωπηλά
    check("ΥΠΟΒΛΗΘΕΙ" not in "Δεν έχει υποβληθεί".upper(),
          "το upper() κρατά τους τόνους (γι' αυτό υπάρχει το gr_norm)")
    check("ΥΠΟΒΛΗΘΕΙ" in gr_norm("Δεν έχει υποβληθεί"),
          "το gr_norm αφαιρεί τόνους")

    # Λατινικά ομοιογράμματα: το portal γράφει «Ε3» και «E3», «ν.4172» και «v.4172»
    check(label_norm("E3 ΥΠΟΧΡΕΟΥ") == label_norm("Ε3 ΥΠΟΧΡΕΟΥ"),
          "λατινικό E ταιριάζει με ελληνικό Ε")
    check(label_norm("Υπόχρεου") == label_norm("ΥΠΟΧΡΕΟΥ"),
          "πεζά με τόνο ταιριάζουν με κεφαλαία")

    # Η άρνηση περιέχει τη θετική λέξη
    def submitted(t: str) -> bool:
        n = gr_norm(t)
        return "ΔΕΝ ΕΧΕΙ ΥΠΟΒΛΗΘΕΙ" not in n and "ΥΠΟΒΛΗΘΕΙ" in n

    check(submitted("Έχει Υποβληθεί Δήλωση") and
          not submitted("Δεν έχει υποβληθεί δήλωση"),
          "η άρνηση «Δεν έχει υποβληθεί» δεν περνά για υποβληθείσα")


# ── Δομή πίνακα ─────────────────────────────────────────────────────────────

PERIODS_ROWS = "".join(f"""
  <tr><td>{q}ο Τρίμηνο 2025</td><td>περίοδος</td><td>Έχει Υποβληθεί Δήλωση</td>
      <td><table><tr><td><div onclick="g()">Επεξεργασία Δηλώσεων</div></td>
          </tr></table></td></tr>""" for q in (1, 2, 3, 4))

# Πίνακας layout που περιτυλίγει τα πάντα + κουμπί σε μονοκύτταρη γραμμή:
# αυτό έκανε το «includes» να διαλέγει λάθος πίνακα.
PERIODS_PAGE = f"""
<table><tr><td>Έχετε 2 νέα μηνύματα. Πατήστε <a href="#">προβολή</a>
  για να μεταβείτε στα εισερχόμενα μηνύματα σας.</td></tr></table>
<table><tr><td>
  <table>
    <tr><th>Φορολογική Περίοδος</th><th>Ημερολογιακή Περίοδος</th>
        <th>Κατάσταση Υποχρέωσης</th><th>Ενέργειες</th></tr>
    {PERIODS_ROWS}
  </table>
  <table><tr><td><input type="button" value="Δηλώσεις"></td></tr></table>
</td></tr></table>"""


async def test_periods(probe: Probe) -> None:
    await probe.page.set_content(PERIODS_PAGE)
    rows = await probe._action_cells("Ενέργειες",
                                     ["Επεξεργασία Δηλώσεων", "Επεξεργασία"])
    check(len(rows) == 4, "4 γραμμές περιόδων, παρότι τα κουμπιά είναι <div> σε "
                          "φωλιασμένο πίνακα", f"βρέθηκαν {len(rows)}")
    check(all("Τρίμηνο" in r["text"] for r in rows),
          "η γραμμή δίνει το κείμενο ΔΕΔΟΜΕΝΩΝ, όχι του φωλιασμένου κελιού")
    check(all(r["label"] == "Επεξεργασία Δηλώσεων" for r in rows),
          "δεν πιάστηκε το κουμπί «Δηλώσεις» της μονοκύτταρης γραμμής")

    # Ο παλιός τρόπος (labels των a/button/input) δεν βλέπει τα <div>
    old = await probe._rows_with_action(["Επεξεργασία Δηλώσεων"])
    check(len(old) == 0,
          "ο εντοπισμός με labels ΔΕΝ βλέπει κουμπιά <div> (γι' αυτό η στήλη)")


# ── Πολλές ενέργειες στο ίδιο κελί (γραμμή δήλωσης Ν) ───────────────────────

N_ACTIONS = """
  <input type="button" value="Υποβολή τροπ/κής">
  <input type="button" value="Προβολή">
  <input type="button" value="Προβολή Ε2">
  <input type="button" value="Προβολή Ε3">
  <input type="button" value="Δεδομένα myDATA">
  <input type="button" value="Προβολή TAXISNet">
  <input type="button" value="Κατάσταση">"""

N_PAGE = f"""
<table>
  <tr><th>Πηγή</th><th>Φορολογικό Έτος</th><th>Είδος</th><th>Ενέργειες</th></tr>
  <tr><td>TAXISnet</td><td>01/01/2025 - 31/12/2025</td><td>Αρχική</td>
      <td>{N_ACTIONS}</td></tr>
</table>"""


async def test_action_disambiguation(probe: Probe) -> None:
    await probe.page.set_content(N_PAGE)
    for want, expect in [("Προβολή", "Προβολή"),
                         ("Προβολή Ε3", "Προβολή Ε3"),
                         ("Προβολή Ε2", "Προβολή Ε2")]:
        rows = await probe._action_cells("Ενέργειες", [want])
        got = rows[0]["label"] if rows else None
        check(got == expect, f"«{want}» πατά ακριβώς «{expect}»", f"πάτησε {got!r}")

    # Η επικίνδυνη ενέργεια είναι ΠΡΩΤΗ στο κελί — δεν πρέπει να επιλέγεται ποτέ
    for want in ("Υποβολή τροπ/κής", "Υποβολή"):
        rows = await probe._action_cells("Ενέργειες", [want])
        check(len(rows) == 0, f"«{want}» μπλοκάρεται από το NEVER_CLICK")


async def test_allow_is_narrow(probe: Probe) -> None:
    """Η έκδοση ενημερότητας χρειάζεται ρητή εξαίρεση — που δεν πρέπει να διαρρέει."""
    await probe.page.set_content("""
    <table><tr><th>Α</th><th>Ενέργειες</th></tr>
      <tr><td>αίτημα 2025</td><td>
        <input type="button" value="Υποβολή Αιτήματος">
        <input type="button" value="Διαγραφή">
      </td></tr></table>""")

    rows = await probe._action_cells("Ενέργειες", ["Υποβολή Αιτήματος"])
    check(len(rows) == 0, "χωρίς allow, η υποβολή μένει μπλοκαρισμένη")

    rows = await probe._action_cells("Ενέργειες", ["Υποβολή Αιτήματος"],
                                     None, ["Υποβολή Αιτήματος"])
    check(len(rows) == 1 and rows[0]["label"] == "Υποβολή Αιτήματος",
          "με ρητό allow, η ζητούμενη ενέργεια επιτρέπεται")

    rows = await probe._action_cells("Ενέργειες", ["Διαγραφή"],
                                     None, ["Υποβολή Αιτήματος"])
    check(len(rows) == 0, "το allow ΔΕΝ ξεκλειδώνει άλλες επικίνδυνες ενέργειες")

    rows = await probe._action_cells("Ενέργειες", ["Υποβολή"],
                                     None, ["Υποβολή"])
    check(len(rows) == 0, "το allow θέλει ΑΚΡΙΒΕΣ label — χωρίς κλιμάκωση με πρόθεμα")


# ── Επιλογή δήλωσης ─────────────────────────────────────────────────────────

async def test_pick_declaration(probe: Probe) -> None:
    rows = [{"idx": 1, "text": "1ο Τρίμηνο 2025 Αρχική Προβολή"},
            {"idx": 2, "text": "1ο Τρίμηνο 2025 τροποποιητικη δηλωση Προβολή"}]
    pick = probe._pick_declaration(rows)
    check(pick["is_tropo"] and pick["idx"] == 2,
          "επιλέγεται η τροποποιητική (ακόμη και άτονη/πεζή)")

    only = [{"idx": 1, "text": "1ο Τρίμηνο 2025 Αρχική Προβολή"}]
    check(not probe._pick_declaration(only)["is_tropo"],
          "χωρίς τροποποιητική, επιλέγεται η αρχική")

    # Το «Υποβολή τροπ/κής» ΔΕΝ πρέπει να περνά για τροποποιητική δήλωση
    misleading = [{"idx": 1, "text": "Αρχική Οριστική Υποβολή τροπ/κής Προβολή"}]
    check(not probe._pick_declaration(misleading)["is_tropo"],
          "το κουμπί «Υποβολή τροπ/κής» δεν μπερδεύεται με τροποποιητική δήλωση")


# ── Σκελετός σελίδας ────────────────────────────────────────────────────────

async def test_chrome_rows(probe: Probe) -> None:
    await probe.page.set_content("""
    <table><tr><td>Έχετε 2 νέα μηνύματα. Πατήστε <a href="#">προβολή</a>
      για να μεταβείτε στα εισερχόμενα μηνύματα σας.</td></tr></table>
    <table><tr><td><a href="#">2.Προβολή</a></td></tr></table>
    <table>
      <tr><td>1ο Τρίμηνο 2025 ΑΡΧΙΚΗ</td>
          <td><input type="submit" value="Προβολή"></td></tr>
    </table>""")
    rows = await probe._rows_with_action(["Προβολή"])
    texts = " ".join(r["text"] for r in rows)
    check("μηνύματα" not in texts,
          "η μπάρα «νέα μηνύματα» δεν περνά ως γραμμή δήλωσης")
    check(all("2.Προβολή" != r["text"] for r in rows),
          "ο σύνδεσμος μενού «2.Προβολή» δεν περνά ως γραμμή δήλωσης")
    check(len(rows) == 1, "μένει μόνο η πραγματική γραμμή", f"{len(rows)} γραμμές")


# ── Ονόματα αρχείων ─────────────────────────────────────────────────────────

def test_filenames() -> None:
    f = MyAADEAutomation.safe_filename
    check(f("ΠΕΛΑΤΗΣ", "2025", "Ε3") == "2024_ΠΕΛΑΤΗΣ_Ε3.pdf",
          "webtax: «ΔΗΛΩΣΕΙΣ ΕΤΟΥΣ 2025» = φορολογικό έτος 2024")
    check(f("ΠΕΛΑΤΗΣ", "2025", "Ν", shift_year=False) == "2025_ΠΕΛΑΤΗΣ_Ν.pdf",
          "income/ΦΠΑ: το έτος ΔΕΝ μετατοπίζεται")


async def main() -> None:
    test_greek_text()
    test_filenames()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        probe = Probe(await browser.new_page())
        await test_periods(probe)
        await test_action_disambiguation(probe)
        await test_allow_is_narrow(probe)
        await test_pick_declaration(probe)
        await test_chrome_rows(probe)
        await browser.close()

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} αποτυχίες:")
        for f in FAILURES:
            print(f"   • {f}")
        sys.exit(1)
    print("✅ Όλοι οι έλεγχοι πέρασαν")


if __name__ == "__main__":
    asyncio.run(main())
