"""
Automation για TaxisNet / AADE portal.

Login:
  income portal → redirect → login.gsis.gr → back to aade.gr

Portals:
  income (Ν):                     www1.aade.gr/taxisnet/income
  webtax (Ε1, Ε3, Εκκαθαριστικό): www1.aade.gr/webtax/incomefp/
  vat (ΦΠΑ):                      www1.aade.gr/taxisnet/vat

Τα income/vat portals παρεμβάλλουν σελίδα «Επιλογή Νομικού Προσώπου» — δες
_select_taxpayer(). Τα labels των κουμπιών αλλάζουν ανά φορολογούμενο
("Ε3 ΥΠΟΧΡΕΟΥ" vs "Ε3 ΣΥΖΥΓΟΥ/ΜΣΣ"), γι' αυτό η επιλογή γίνεται διαβάζοντας
τα πραγματικά labels της σελίδας — δες _click_labeled().
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Callable, List, Optional

from playwright.async_api import TimeoutError as PwTimeout

from .base import BaseAutomation, gr_norm, label_norm

# ------------------------------------------------------------------
# URLs
# ------------------------------------------------------------------
INCOME_ENTRY    = "https://www1.aade.gr/taxisnet/income"
WEBTAX_ENTRY    = "https://www1.aade.gr/webtax/incomefp/"
VAT_ENTRY       = "https://www1.aade.gr/taxisnet/vat"

# login.gsis.gr selectors
SEL_USER = "input[name='username'], #username"
SEL_PASS = "input[name='password'], #password"
SEL_SUB  = (
    "a[onclick*='submit' i], a[onclick*='login' i], "
    "input[type='submit'], button[type='submit'], "
    "a:has-text('Είσοδος'), a:has-text('Σύνδεση'), button:has-text('Είσοδος')"
)

DOCUMENT_LABELS = {
    "e1":             "Ε1",
    "e3":             "Ε3",
    "n":              "Ν",
    "ekkatharistiko": "Εκκαθαριστικό",
    "fpa":            "ΦΠΑ",
}

DEBUG_SHOT = Path("/tmp/gov_debug.png")


class MyAADEAutomation(BaseAutomation):

    def __init__(self, log_callback: Callable, ready_event: Optional[asyncio.Event] = None):
        super().__init__(log_callback)
        self._ready = ready_event
        # Ατομική = φυσικό πρόσωπο· δες _select_taxpayer(). Ορίζεται στο run().
        self.is_atomiki = True

    # ------------------------------------------------------------------
    # Login μέσω login.gsis.gr
    # ------------------------------------------------------------------
    async def login(self, username: str, password: str):
        self.log("↗ Σύνδεση στο TaxisNet (μέσω login.gsis.gr)…")
        await self.page.goto(INCOME_ENTRY, wait_until="domcontentloaded", timeout=30_000)
        await self.page.wait_for_load_state("networkidle", timeout=20_000)

        try:
            await self.page.wait_for_selector(SEL_USER, timeout=15_000)
        except PwTimeout:
            await self.page.screenshot(path=str(DEBUG_SHOT))
            raise RuntimeError(
                f"Δεν βρέθηκε φόρμα login. URL: {self.page.url}\n"
                f"Screenshot: {DEBUG_SHOT}"
            )

        self.log(f"  Φόρμα στο: {self.page.url}")
        self.log("🔑 Εισαγωγή κωδικών…")
        await self.page.fill(SEL_USER, username)
        await self.page.fill(SEL_PASS, password)

        # Στο gsis.gr το submit μπορεί να είναι anchor με onclick
        submitted = False
        try:
            sub = await self.page.wait_for_selector(SEL_SUB, timeout=5_000)
            await sub.click()
            submitted = True
        except PwTimeout:
            pass

        if not submitted:
            await self.page.evaluate(
                "() => { const f = document.querySelector('form'); if(f) f.submit(); }"
            )

        await self.page.wait_for_load_state("networkidle", timeout=30_000)

        # Ελέγχουμε αν επιστρέψαμε στο aade.gr
        if "aade.gr" not in self.page.url:
            err = await self.page.query_selector(".error, #errorDiv, span[class*='error' i]")
            if err:
                raise RuntimeError(f"Λάθος κωδικοί: {(await err.inner_text()).strip()}")
            await self.page.screenshot(path=str(DEBUG_SHOT))
            raise RuntimeError(
                f"Απρόσμενο URL μετά login: {self.page.url}\nScreenshot: {DEBUG_SHOT}"
            )

        self.log(f"✅ Σύνδεση επιτυχής! ({self.page.url})", "success")

    # ------------------------------------------------------------------
    # Βοηθητικές
    # ------------------------------------------------------------------
    async def _goto(self, url: str):
        await self.page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await self.page.wait_for_load_state("networkidle", timeout=15_000)

    async def _click_and_follow(self, el):
        """
        Κάνει κλικ και ακολουθεί είτε πλοήγηση στην ίδια σελίδα είτε άνοιγμα σε νέο
        tab/popup (συχνό στα gov portals για viewPdf-type links) — μεταθέτει το
        self.page στο νέο tab όταν χρειάζεται, ώστε τα επόμενα βήματα να δουλέψουν
        στη σωστή σελίδα.
        """
        ctx = self.page.context
        popup_page = None

        def on_page(p):
            nonlocal popup_page
            popup_page = p

        ctx.on("page", on_page)
        try:
            await el.click()
            for _ in range(15):
                if popup_page is not None:
                    break
                await self.page.wait_for_timeout(200)
            if popup_page is not None:
                await popup_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                self.page = popup_page
                self.log(f"  ↗ Άνοιξε νέο tab: {self.page.url}")
            else:
                await self.page.wait_for_load_state("networkidle", timeout=20_000)
        finally:
            ctx.remove_listener("page", on_page)

    # Όλα τα clickable στοιχεία (a / button / input) της σελίδας, μαζί με το
    # πραγματικό τους label. Το χρησιμοποιούμε γιατί τα labels του portal
    # αλλάζουν ανά φορολογούμενο ("Ε3 ΥΠΟΧΡΕΟΥ" vs "Ε3 ΣΥΖΥΓΟΥ/ΜΣΣ"), οπότε
    # τα hardcoded selectors σπάνε — καλύτερα να διαβάζουμε τι υπάρχει όντως.
    CLICKABLE_CSS = "a, button, input[type='button'], input[type='submit']"

    async def _settle(self):
        """Περιμένει να ησυχάσει η σελίδα μετά από (πιθανή) πλοήγηση."""
        for state in ("domcontentloaded", "networkidle"):
            try:
                await self.page.wait_for_load_state(state, timeout=15_000)
            except Exception:
                pass

    LABELS_JS = """(css) => [...document.querySelectorAll(css)].map((el, i) => ({
                       i,
                       label: (el.value || el.innerText || el.textContent || '')
                                .trim().replace(/\\s+/g, ' '),
                       disabled: !!el.disabled,
                   })).filter(o => o.label)"""

    LABELS_IN_JS = """(el, css) => [...el.querySelectorAll(css)].map((e, i) => ({
                          i,
                          label: (e.value || e.innerText || e.textContent || '')
                                   .trim().replace(/\\s+/g, ' '),
                          disabled: !!e.disabled,
                      })).filter(o => o.label)"""

    async def _clickables(self, scope=None) -> List[dict]:
        """
        Τα clickable στοιχεία της σελίδας — ή, αν δοθεί `scope` (locator),
        μόνο όσα βρίσκονται ΜΕΣΑ σε αυτό (π.χ. σε μια συγκεκριμένη γραμμή).
        """
        # Η επιλογή έτους πυροδοτεί πλοήγηση· αν το evaluate πέσει πάνω σε
        # πλοήγηση εν εξελίξει, το context καταστρέφεται — ξαναδοκιμάζουμε.
        last_err = None
        for _ in range(3):
            await self._settle()
            try:
                if scope is None:
                    return await self.page.evaluate(self.LABELS_JS, self.CLICKABLE_CSS)
                return await scope.evaluate(self.LABELS_IN_JS, self.CLICKABLE_CSS)
            except Exception as e:
                last_err = e
                if "Execution context was destroyed" not in str(e):
                    raise
                await self.page.wait_for_timeout(800)
        raise last_err

    async def _click_labeled(self, preferences: List[str], what: str,
                             avoid: Optional[List[str]] = None,
                             scope=None) -> Optional[str]:
        """
        Κάνει κλικ στο πρώτο στοιχείο που ταιριάζει με τη σειρά προτίμησης και
        επιστρέφει το label που πατήθηκε (ή None). Ο caller χρειάζεται να ξέρει
        ΠΟΙΟ πατήθηκε, γιατί π.χ. το Ε3 ΣΥΖΥΓΟΥ/ΜΣΣ σώζεται με άλλο όνομα.

        Κάθε προτίμηση δοκιμάζεται πρώτα ως ακριβές label, μετά ως υποσύνολο.
        Το `avoid` αποκλείει labels που δεν είναι ποτέ το ζητούμενο έγγραφο
        (π.χ. "Ε3 - myDATA", "ΣΥΝΟΨΗ ..."), για να μη κατέβει λάθος αρχείο.
        Το `scope` περιορίζει την αναζήτηση μέσα σε ένα στοιχείο (π.χ. γραμμή Φ2).
        """
        # ΟΛΕΣ οι συγκρίσεις γίνονται σε label_norm() μορφή: το portal γράφει
        # άλλοτε "ΥΠΟΧΡΕΟΥ", άλλοτε "Υπόχρεου" και άλλοτε με λατινικό "E" στο
        # "E3" — χωρίς κανονικοποίηση η σύγκριση αποτυγχάνει σιωπηλά.
        avoid = [label_norm(a) for a in (avoid or [])]
        items = await self._clickables(scope=scope)
        base = scope if scope is not None else self.page
        for pref in preferences:
            pref_n = label_norm(pref)
            for exact in (True, False):
                for it in items:
                    label_n = label_norm(it["label"])
                    hit = label_n == pref_n if exact else pref_n in label_n
                    if not hit:
                        continue
                    if any(a in label_n for a in avoid):
                        continue
                    if it["disabled"]:
                        self.log(f"  ⚠️ Το '{it['label']}' είναι ανενεργό — παρακάμπτεται", "error")
                        continue
                    self.log(f"  → Κλικ στο '{it['label']}'")
                    el = base.locator(self.CLICKABLE_CSS).nth(it["i"])
                    await self._click_and_follow(el)
                    return it["label"]
        self.log(
            f"  ⚠️ Δεν βρέθηκε κουμπί για {what}. Ζητήθηκαν: {preferences}. "
            f"Διαθέσιμα στη σελίδα: {[i['label'] for i in items]}",
            "error",
        )
        return None

    async def _row_locator(self, code: str):
        """
        Locator της γραμμής της οποίας το ΠΡΩΤΟ κελί είναι ακριβώς `code`.

        Το `tr:has-text('Φ2')` έπιανε γραμμή-περιτύλιγμα (φωλιασμένοι πίνακες),
        οπότε το dropdown/κουμπί που έβρισκε ανήκε στο Φ1 — γι' αυτό κατέληγε
        στο vatF1&year=2010. Το ακριβές πρώτο κελί λύνει το πρόβλημα.
        """
        await self._settle()
        idx = await self.page.evaluate(
            """(code) => {
                   const rows = [...document.querySelectorAll('tr')];
                   for (let i = 0; i < rows.length; i++) {
                       const cell = rows[i].querySelector('td, th');
                       if (cell && cell.innerText.trim() === code) return i;
                   }
                   return -1;
               }""",
            code,
        )
        if idx < 0:
            return None
        return self.page.locator("tr").nth(idx)

    async def _select_year_in(self, scope, year: str) -> bool:
        """Επιλέγει έτος στο dropdown που βρίσκεται ΜΕΣΑ στο `scope` (π.χ. γραμμή Φ2)."""
        sel = scope.locator("select").first
        try:
            await sel.wait_for(timeout=5_000)
        except Exception:
            self.log("  ⚠️ Δεν βρέθηκε dropdown έτους στη γραμμή", "error")
            return False
        options = await sel.evaluate(
            "(el) => [...el.options].map(o => ({value: o.value, text: o.text.trim()}))"
        )
        await sel.click()
        for opt in [{"value": year}, {"label": year}]:
            try:
                await sel.select_option(**opt)
                self.log(f"  📅 Επιλέχθηκε έτος {year} στο dropdown της γραμμής")
                return True
            except Exception:
                continue
        self.log(
            f"  ⚠️ Το έτος {year} δεν υπάρχει στο dropdown. "
            f"Διαθέσιμα: {[o['text'] for o in options]}",
            "error",
        )
        return False

    # Γραμμές που ανήκουν στο "σκελετό" του TaxisNet και υπάρχουν σε ΚΑΘΕ σελίδα.
    # Δεν είναι ποτέ γραμμές δεδομένων, αλλά περιέχουν clickables με τα ίδια
    # labels που ψάχνουμε — π.χ. η κίτρινη μπάρα «Έχετε N νέα μηνύματα. Πατήστε
    # προβολή …» έχει link «προβολή» και, καθώς είναι ψηλά στο DOM, γινόταν
    # rows[0] και πατιόταν αντί της δήλωσης, οδηγώντας στα εισερχόμενα μηνύματα.
    CHROME_ROW_PATTERNS = [
        "ΝΕΑ ΜΗΝΥΜΑΤΑ", "ΕΙΣΕΡΧΟΜΕΝΑ", "ΑΠΟΣΥΝΔΕΣΗ", "ΑΛΛΕΣ ΕΦΑΡΜΟΓΕΣ",
        "Ο ΛΟΓΑΡΙΑΣΜΟΣ ΜΟΥ",
    ]

    async def _rows_with_action(self, actions: List[str]) -> List[dict]:
        """
        Γραμμές πίνακα που περιέχουν κουμπί/link με ένα από τα `actions`.
        Επιστρέφει [{idx, text}] — το idx δείχνει σε self.page.locator('tr').
        Χρησιμοποιείται και για τη λίστα ΠΕΡΙΟΔΩΝ («Επεξεργασία Δηλώσεων»)
        και για τη λίστα ΔΗΛΩΣΕΩΝ μιας περιόδου («Προβολή»).

        Οι γραμμές του σκελετού της σελίδας (CHROME_ROW_PATTERNS) αποκλείονται.
        """
        await self._settle()
        # Ξεκινάμε από τα ΚΟΥΜΠΙΑ και ανεβαίνουμε στη γραμμή τους (closest('tr')),
        # αντί να διατρέχουμε γραμμές και να τις φιλτράρουμε.
        # ΓΙΑΤΙ: η προηγούμενη έκδοση πετούσε κάθε γραμμή που περιέχει <table>
        # («γραμμή-περιτύλιγμα»). Στη σελίδα υποχρεώσεων ΦΠΑ το κελί «Ενέργειες»
        # έχει το κουμπί μέσα σε φωλιασμένο πίνακα, οπότε ΟΛΕΣ οι γραμμές
        # περιόδων πετάγονταν και έβγαινε «Δεν βρέθηκαν περίοδοι» — παρότι τα
        # 4 κουμπιά «Επεξεργασία Δηλώσεων» ήταν εμφανώς εκεί.
        # Επιστρέφουμε και το `btn` (δείκτης του κουμπιού σε ΟΛΗ τη σελίδα), ώστε
        # ο caller να πατάει κατευθείαν το σωστό κουμπί χωρίς scope σε γραμμή.
        return await self.page.evaluate(
            """([css, actions, chrome]) => {
                   const norm = s => s.toUpperCase()
                       .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
                   const allTr = [...document.querySelectorAll('tr')];
                   const out = [], seen = new Set();
                   [...document.querySelectorAll(css)].forEach((el, btn) => {
                       const label = ((el.value || el.innerText || el.textContent || '')
                                       .replace(/\\s+/g, ' ')).trim();
                       if (!label) return;
                       if (norm(label).includes('ΥΠΟΒΟΛΗ ΤΡΟΠΟΠΟΙΗΤΙΚΗΣ')) return;
                       if (!actions.some(a => label.includes(a))) return;

                       // Το closest('tr') δίνει τον ΕΣΩΤΕΡΙΚΟ tr του φωλιασμένου
                       // πίνακα του κελιού, που δεν έχει κείμενο δεδομένων (και
                       // έτσι χανόταν το «Τροποποιητική»). Ανεβαίνουμε προς τα έξω
                       // ώσπου να βρεθεί γραμμή με περιεχόμενο ΠΕΡΑ από το label.
                       let chosen = null, rowText = '';
                       for (let node = el.closest('tr'); node;
                            node = node.parentElement
                                   ? node.parentElement.closest('tr') : null) {
                           const t = node.innerText.trim().replace(/\\s+/g, ' ');
                           if (norm(t).replace(norm(label), '').trim().length > 0) {
                               chosen = node; rowText = t; break;
                           }
                       }
                       // Καμία γραμμή με δεδομένα: σύνδεσμος μενού («2.Προβολή»)
                       if (!chosen) return;
                       // Σκελετός σελίδας (μπάρα μηνυμάτων κ.λπ.) — ποτέ δεδομένα
                       if (chrome.some(c => norm(rowText).includes(c))) return;

                       const idx = allTr.indexOf(chosen);
                       if (seen.has(idx)) return;   // ένα κουμπί ανά γραμμή
                       seen.add(idx);
                       out.push({idx, btn, label, text: rowText});
                   });
                   return out;
               }""",
            [self.CLICKABLE_CSS, actions, self.CHROME_ROW_PATTERNS],
        )

    # Ό,τι μπορεί να είναι κλικαρίσιμο μέσα σε κελί. Πολύ ευρύτερο από το
    # CLICKABLE_CSS επίτηδες: στη σελίδα υποχρεώσεων ΦΠΑ τα κουμπιά «Επεξεργασία
    # Δηλώσεων» ΔΕΝ είναι a/button/input[submit|button] — δεν εμφανίζονταν καθόλου
    # στα clickables — γι' αυτό εδώ δεν υποθέτουμε τύπο στοιχείου.
    CELL_CLICKABLE_CSS = ("a, button, input, [onclick], [href], "
                          "[role='button'], img, span, div")

    # Ενέργειες που ΔΕΝ πατάμε ΠΟΤΕ: αλλάζουν κατάσταση στο portal. Στο κελί
    # «Ενέργειες» της λίστας δηλώσεων το «Υποβολή τροπ/κής» είναι ΠΡΩΤΟ, πριν το
    # «Προβολή» — παίρνοντας «το πρώτο κλικαρίσιμο» θα ξεκινούσαμε υποβολή
    # τροποποιητικής δήλωσης. Ο έλεγχος γίνεται σε normalized μορφή, γιατί το
    # portal γράφει άλλοτε «Υποβολή τροπ/κής» και άλλοτε «Υποβολή Τροποποιητικής».
    NEVER_CLICK = ["ΥΠΟΒΟΛΗ", "ΟΡΙΣΤΙΚΟΠΟΙΗΣΗ", "ΔΙΑΓΡΑΦΗ", "ΑΚΥΡΩΣΗ",
                   "ΠΛΗΡΩΜΗ", "ΑΠΟΣΤΟΛΗ"]

    # Το print-to-PDF έσωζε ό,τι σελίδα βρισκόταν μπροστά (π.χ. το μενού) με
    # όνομα σωστού εγγράφου. Για λογιστική χρήση αυτό είναι χειρότερο από καθαρή
    # αποτυχία, γι' αυτό μένει κλειστό ακόμη και σε headless που το υποστηρίζει.
    ALLOW_PRINT_TO_PDF = False

    async def _action_cells(self, header: str, actions: Optional[List[str]] = None,
                            avoid: Optional[List[str]] = None) -> List[dict]:
        """
        Εντοπίζει τη στήλη με κεφαλίδα `header` (π.χ. «Ενέργειες») και επιστρέφει
        για κάθε γραμμή δεδομένων το κλικαρίσιμο στοιχείο ΑΥΤΗΣ της στήλης.

        Δουλεύει με τη ΔΟΜΗ του πίνακα (κεφαλίδα → δείκτης στήλης → κελί), όχι με
        labels ή τύπους στοιχείων, γι' αυτό είναι ανθεκτικό σε φωλιασμένους
        πίνακες και σε κουμπιά που δεν είναι κανονικά <button>/<input>.

        Κάθε στόχος σημαδεύεται με data-gdf-click="k" ώστε το κλικ να γίνεται
        ακριβώς σε αυτό το στοιχείο, χωρίς δείκτες που μπορεί να μετακινηθούν.
        """
        await self._settle()
        return await self.page.evaluate(
            """([header, cellCss, actions, avoid, never]) => {
                   const norm = s => s.toUpperCase()
                       .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
                   const H = norm(header);
                   document.querySelectorAll('[data-gdf-click]')
                       .forEach(e => e.removeAttribute('data-gdf-click'));

                   // Το κελί κεφαλίδας πρέπει να είναι ΤΟ ΙΔΙΟ το «Ενέργειες»,
                   // όχι κελί που το περιέχει κάπου μέσα του: τα κελιά-περιτυλίγματα
                   // του layout περιέχουν ΟΛΟ το κείμενο της σελίδας, άρα και τη
                   // λέξη «Ενέργειες», και τότε διαλέγαμε λάθος πίνακα (κατέληγε
                   // να πατά το κουμπί «Δηλώσεις» και να γυρίζει στα έντυπα).
                   const isHeader = td => {
                       const t = norm(td.innerText.trim());
                       return t === H || (t.includes(H) && t.length <= H.length + 5);
                   };

                   let best = [];
                   for (const table of document.querySelectorAll('table')) {
                       const rows = [...table.rows];
                       let hdrRow = -1, col = -1;
                       for (let r = 0; r < rows.length && col < 0; r++) {
                           const cells = [...rows[r].cells];
                           if (cells.length < 2) continue;   // μονοκύτταρη = περιτύλιγμα
                           const c = cells.findIndex(isHeader);
                           if (c >= 0) { hdrRow = r; col = c; }
                       }
                       if (col < 0) continue;
                       const found = [];
                       for (let r = hdrRow + 1; r < rows.length; r++) {
                           const cells = [...rows[r].cells];
                           if (col >= cells.length) continue;
                           // Γραμμή δεδομένων: έχει κείμενο. Οι γραμμές που έχουν
                           // ΜΟΝΟ κουμπί δίνουν κενό innerText (τα value των input
                           // δεν μετρούν) — αυτές είναι περιτυλίγματα, όχι δεδομένα.
                           const rowText = rows[r].innerText.trim()
                                               .replace(/\\s+/g, ' ');
                           if (!rowText) continue;
                           const cell = cells[col];
                           // ΟΛΑ τα υποψήφια του κελιού, όχι το πρώτο: το κελί
                           // «Ενέργειες» έχει πολλά κουμπιά («Υποβολή τροπ/κής»,
                           // «Προβολή», «Κατάσταση») και θέλουμε ΣΥΓΚΕΚΡΙΜΕΝΟ.
                           const cands = [...cell.querySelectorAll(cellCss)];
                           if (!cands.length && cell.getAttribute('onclick'))
                               cands.push(cell);
                           // Το κουμπί μπορεί να είναι εικόνα — τότε δεν έχει
                           // κείμενο, οπότε πέφτουμε σε alt/title.
                           const labelOf = el => ((el.value || el.innerText ||
                                       el.textContent || el.getAttribute('alt') ||
                                       el.getAttribute('title') || '')
                                      .replace(/\\s+/g, ' ')).trim();
                           let pool = cands.map(el => ({el, label: labelOf(el)}))
                               // Ποτέ ενέργειες που αλλάζουν κατάσταση
                               .filter(c => !never.some(
                                   n => norm(c.label).includes(n)))
                               .filter(c => !avoid.some(
                                   a => norm(c.label).includes(norm(a))));
                           let target = null, label = '';
                           if (actions.length) {
                               // Ακριβές label πρώτα, μετά υποσύνολο· και από τα
                               // ταιριαστά το ΠΙΟ ΣΥΝΤΟΜΟ, ώστε να μη διαλέγεται
                               // ένα div-περιτύλιγμα που περιέχει όλα τα κουμπιά.
                               for (const exact of [true, false]) {
                                   const hits = pool.filter(c => actions.some(a =>
                                       exact ? norm(c.label) === norm(a)
                                             : norm(c.label).includes(norm(a))));
                                   if (hits.length) {
                                       hits.sort((x, y) =>
                                           x.label.length - y.label.length);
                                       target = hits[0].el; label = hits[0].label;
                                       break;
                                   }
                               }
                               // Ζητήθηκε συγκεκριμένη ενέργεια και δεν υπάρχει:
                               // ΔΕΝ πατάμε τυχαίο κουμπί.
                               if (!target) continue;
                           } else {
                               const first = pool.find(c => c.label) || pool[0];
                               if (!first) continue;
                               target = first.el; label = first.label;
                           }
                           found.push({el: target, label, text: rowText,
                                       tag: target.tagName.toLowerCase(),
                                       type: target.getAttribute('type') || ''});
                       }
                       // Ο πίνακας με τις ΠΕΡΙΣΣΟΤΕΡΕΣ γραμμές, όχι ο πρώτος που
                       // έδωσε κάτι — αλλιώς ένας τυχαίος πίνακας 1 γραμμής νικά.
                       if (found.length > best.length) best = found;
                   }
                   return best.map((f, k) => {
                       f.el.setAttribute('data-gdf-click', String(k));
                       return {k, label: f.label || '(χωρίς ετικέτα)',
                               text: f.text, tag: f.tag, type: f.type};
                   });
               }""",
            [header, self.CELL_CLICKABLE_CSS, actions or [], avoid or [],
             self.NEVER_CLICK],
        )

    async def _action_cells_wait(self, header: str, what: str,
                                 actions: Optional[List[str]] = None,
                                 avoid: Optional[List[str]] = None,
                                 attempts: int = 8) -> List[dict]:
        """Σαν το _action_cells, με αναμονή να φορτώσει η σελίδα."""
        for attempt in range(1, attempts + 1):
            cells = await self._action_cells(header, actions, avoid)
            if cells:
                if attempt > 1:
                    self.log(f"  ⏳ {what}: εμφανίστηκαν στην προσπάθεια {attempt}")
                return cells
            await self.page.wait_for_timeout(1_000)
        return []

    async def _click_row_action(self, item: dict, what: str) -> bool:
        """
        Πατάει το κουμπί μιας γραμμής, ανεξάρτητα από ποιον εντοπισμό προήλθε:
        `k` → από τη στήλη «Ενέργειες» (data-gdf-click), `btn` → από labels.
        """
        if "k" in item:
            return await self._click_marked(item["k"], what)
        return await self._click_button_index(item["btn"], what)

    async def _find_row_actions(self, header: str, actions: List[str],
                                what: str) -> List[dict]:
        """
        Εντοπισμός γραμμών με ενέργεια, με δύο στρατηγικές:
          1. Από τη ΣΤΗΛΗ `header` (π.χ. «Ενέργειες») — δουλεύει ακόμη κι όταν τα
             κουμπιά δεν είναι a/button/input, που είναι η περίπτωση του ΦΠΑ.
          2. Fallback: από τα labels των clickables.
        """
        cells = await self._action_cells_wait(header, what, actions=actions)
        if cells:
            kinds = {f"{c['tag']}[{c['type']}]" if c["type"] else c["tag"]
                     for c in cells}
            self.log(
                f"  🧭 {what}: {len(cells)} γραμμές από τη στήλη «{header}» "
                f"(στοιχεία: {', '.join(sorted(kinds))})"
            )
            return cells
        self.log(
            f"  ↩️ {what}: η στήλη «{header}» δεν έδωσε γραμμές — "
            f"δοκιμή με labels", "error",
        )
        return await self._rows_with_action_wait(actions, what)

    async def _click_marked(self, k: int, what: str) -> bool:
        """Κλικ στο στοιχείο που σημαδεύτηκε με data-gdf-click="k"."""
        try:
            el = self.page.locator(f'[data-gdf-click="{k}"]')
            await self._click_and_follow(el)
            return True
        except Exception as e:
            self.log(f"  ⚠️ Απέτυχε το κλικ για {what}: {e}", "error")
            return False

    async def _dump_table_html(self, header: str, tag: str):
        """
        Διαγνωστικό: το HTML του πίνακα που περιέχει την κεφαλίδα `header`.
        Χρειάζεται όταν δεν αναγνωρίζουμε τι είδους στοιχεία είναι τα κουμπιά.
        """
        try:
            html = await self.page.evaluate(
                """(header) => {
                       const norm = s => s.toUpperCase()
                           .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
                       const H = norm(header);
                       for (const t of document.querySelectorAll('table'))
                           if (norm(t.innerText).includes(H))
                               return t.outerHTML;
                       return '(δεν βρέθηκε πίνακας με ' + header + ')';
                   }""",
                header,
            )
            path = DEBUG_SHOT.with_name(f"gov_debug_{tag}.html")
            path.write_text(html[:60_000], encoding="utf-8")
            self.log(f"  🧩 HTML πίνακα «{header}»: {path}", "error")
        except Exception:
            pass

    async def _rows_with_action_wait(self, actions: List[str], what: str,
                                     attempts: int = 12) -> List[dict]:
        """
        Σαν το _rows_with_action, αλλά ΠΕΡΙΜΕΝΕΙ να εμφανιστούν οι γραμμές.

        ΓΙΑΤΙ: το _settle() μπορεί να επιστρέψει ενώ η πλοήγηση δεν έχει
        ολοκληρωθεί, οπότε το evaluate έτρεχε πάνω στο ΠΑΛΙΟ document. Στο ΦΠΑ
        αυτό διάβαζε ακόμη την αρχική σελίδα συντομεύσεων του TaxisNet και
        έβγαζε «Δεν βρέθηκαν περίοδοι», παρότι η σελίδα υποχρεώσεων φόρτωνε
        κανονικά ένα κλάσμα του δευτερολέπτου αργότερα.
        """
        for attempt in range(1, attempts + 1):
            rows = await self._rows_with_action(actions)
            if rows:
                if attempt > 1:
                    self.log(f"  ⏳ {what}: εμφανίστηκαν στην προσπάθεια {attempt}")
                return rows
            if attempt in (1, attempts // 2):
                try:
                    self.log(
                        f"  ⏳ {what}: ακόμη τίποτα (προσπάθεια {attempt}) — "
                        f"σελίδα '{await self.page.title()}' στο {self.page.url}"
                    )
                except Exception:
                    pass
            await self.page.wait_for_timeout(1_000)
        return []

    async def _click_button_index(self, btn: int, what: str) -> bool:
        """
        Πατάει το κουμπί με δείκτη `btn` στη λίστα CLICKABLE_CSS όλης της σελίδας
        — τον δείκτη τον δίνει το _rows_with_action. Αποφεύγει το scope-σε-γραμμή,
        που έσπαγε όταν το κουμπί ήταν σε φωλιασμένο πίνακα μέσα στο κελί.
        """
        try:
            el = self.page.locator(self.CLICKABLE_CSS).nth(btn)
            await self._click_and_follow(el)
            return True
        except Exception as e:
            self.log(f"  ⚠️ Απέτυχε το κλικ για {what}: {e}", "error")
            return False

    def _pick_declaration(self, rows: List[dict]) -> Optional[dict]:
        """
        Μέσα στη λίστα δηλώσεων ΜΙΑΣ περιόδου: επιστρέφει την ΤΡΟΠΟΠΟΙΗΤΙΚΗ αν
        υπάρχει (την πιο πρόσφατη), αλλιώς την πρώτη (αρχική).
        """
        if not rows:
            return None
        for r in rows:
            r["is_tropo"] = "ΤΡΟΠΟΠΟΙΗΤΙΚ" in gr_norm(r["text"])
        amendments = [r for r in rows if r["is_tropo"]]
        if amendments:
            # Το «Προβολή» που πατιέται είναι αυτό ΤΗΣ ΓΡΑΜΜΗΣ της τροποποιητικής
            # (ο caller κάνει scope στο tr), δηλαδή το πιο κοντινό σε αυτήν.
            if len(amendments) > 1:
                self.log(
                    f"  🔁 {len(amendments)} τροποποιητικές — κατεβαίνει η πιο πρόσφατη "
                    f"(τελευταία στη λίστα)"
                )
            else:
                self.log("  🔁 Υπάρχει τροποποιητική — κατεβαίνει αυτή αντί της αρχικής")
            return amendments[-1]
        # Χωρίς τροποποιητική αναμένεται ΜΙΑ μόνο δήλωση/«Προβολή» στην οθόνη.
        if len(rows) > 1:
            self.log(
                f"  ⚠️ Καμία τροποποιητική, αλλά βρέθηκαν {len(rows)} δηλώσεις — "
                f"κατεβαίνει η πρώτη (αρχική)", "error",
            )
        return rows[0]

    async def _back_to(self, list_page, list_url: str):
        """
        Επιστροφή στη λίστα δηλώσεων μετά την προβολή ενός PDF: αν άνοιξε νέο
        tab το κλείνουμε, αλλιώς γυρίζουμε πίσω στην ίδια καρτέλα.
        """
        if self.page is not list_page:
            try:
                await self.page.close()
            except Exception:
                pass
            self.page = list_page
            return
        try:
            await self.page.go_back()
            await self._settle()
        except Exception:
            pass
        if self.page.url != list_url:
            await self._goto(list_url)

    async def _own_afm(self) -> Optional[str]:
        """Το ΑΦΜ του συνδεδεμένου χρήστη, από την κεφαλίδα της σελίδας."""
        try:
            text = await self.page.inner_text("body")
        except Exception:
            return None
        m = re.search(r"Α\.?Φ\.?Μ\.?\s*:?\s*(\d{9})", text)
        return m.group(1) if m else None

    async def _on_entity_page(self) -> bool:
        if "LegalEntities" in self.page.url:
            return True
        try:
            return "Επιλογή Νομικού Προσώπου" in await self.page.content()
        except Exception:
            return False

    async def _select_taxpayer(self, is_atomiki: bool):
        """
        Τα portals income/vat παρεμβάλλουν σελίδα «Επιλογή Νομικού Προσώπου»,
        γιατί ο λογαριασμός μπορεί να εκπροσωπεί ΚΑΙ άλλες οντότητες.

        ΚΡΙΣΙΜΟ: σε ΑΤΟΜΙΚΗ επιχείρηση ο φορολογούμενος είναι ο ΙΔΙΟΣ ο χρήστης.
        Αν επιλεγεί εδώ νομικό πρόσωπο, κατεβαίνουν τα έγγραφα ΑΛΛΗΣ οντότητας
        (το είχαμε δει: ΤΡΙΚΚΑ ΑΛΙΚΗ «για λογαριασμό του» ΚΠΤΑ ΚΑΤΑΣΚΕΥΑΣΤΙΚΗ,
        με μηνιαίο ΦΠΑ αντί τριμηνιαίου).
        """
        if not await self._on_entity_page():
            return  # δεν υπάρχει τέτοιο βήμα — προχωράμε κανονικά

        own = await self._own_afm()
        afm_links = [it for it in await self._clickables()
                     if re.fullmatch(r"\d{9}", it["label"])]

        if is_atomiki:
            # Δεκτό ΜΟΝΟ το ίδιο ΑΦΜ του χρήστη· ποτέ άλλη οντότητα.
            mine = [a for a in afm_links if own and a["label"] == own]
            if mine:
                self.log(f"  👤 Ατομική: επιλογή του ίδιου ΑΦΜ {own}")
                await self._click_and_follow(
                    self.page.locator(self.CLICKABLE_CSS).nth(mine[0]["i"]))
                return
            others = [a["label"] for a in afm_links]
            raise RuntimeError(
                f"Δηλώθηκε ΑΤΟΜΙΚΗ επιχείρηση (ΑΦΜ χρήστη {own}), αλλά το portal "
                f"ζητά επιλογή νομικού προσώπου και προσφέρει μόνο: {others}. "
                "Δεν επιλέγω άλλη οντότητα — θα κατέβαιναν έγγραφα άλλου "
                "φορολογούμενου. Αν ο πελάτης είναι ΝΟΜΙΚΟ πρόσωπο, σβήσε το "
                "toggle «Ατομική Επιχείρηση»."
            )

        if not afm_links:
            raise RuntimeError(
                f"Σελίδα επιλογής νομικού προσώπου χωρίς επιλέξιμο ΑΦΜ: {self.page.url}"
            )
        if len(afm_links) > 1:
            raise RuntimeError(
                "Ο λογαριασμός εκπροσωπεί πολλές οντότητες: "
                f"{[a['label'] for a in afm_links]}. Πες μου ποιο ΑΦΜ θέλεις "
                "για να προσθέσω πεδίο επιλογής."
            )
        self.log(f"  🏢 Επιλογή νομικού προσώπου ΑΦΜ {afm_links[0]['label']}")
        el = self.page.locator(self.CLICKABLE_CSS).nth(afm_links[0]["i"])
        await self._click_and_follow(el)

    async def _click_first(self, selectors: List[str], timeout: int = 6_000, label: str = "",
                            optional: bool = False) -> bool:
        for sel in selectors:
            try:
                el = await self.page.wait_for_selector(sel, timeout=timeout // max(len(selectors), 1))
                if el:
                    await self._click_and_follow(el)
                    return True
            except PwTimeout:
                continue
        if label and not optional:
            self.log(
                f"  ⚠️ Δεν βρέθηκε κανένα από τα κουμπιά/links για '{label}' "
                f"(δοκιμάστηκαν: {selectors}) στη σελίδα {self.page.url}",
                "error",
            )
        return False

    async def _select_year(self, year: str):
        # Σκόπευση του select ΚΟΝΤΑ στην ετικέτα έτους — όχι τυχαίο select της σελίδας
        # (το γενικό fallback "select" έπιανε λάθος dropdown σε κάποιες σελίδες).
        candidates = [
            "select:near(:text('ΕΤΟΥΣ'))",
            "select:near(:text('Έτος'))",
            "select:near(:text('έτος'))",
            "select[name*='year' i]",
            "select[name*='etos' i]",
            "select[id*='year' i]",
        ]
        sel = None
        for css in candidates:
            try:
                sel = await self.page.wait_for_selector(css, timeout=2_500)
                if sel:
                    break
            except PwTimeout:
                continue

        if not sel:
            self.log(f"  ⚠️ Δεν βρέθηκε dropdown επιλογής έτους στη σελίδα {self.page.url}", "error")
            return

        # Πρώτα κλικ πάνω στο dropdown (όπως θα έκανε ο χρήστης) ώστε να «ανοίξει»,
        # και μετά επιλογή έτους — κάποιες σελίδες δεν αντιδρούν σε καθαρό select_option.
        try:
            await sel.click()
        except Exception:
            pass

        for opt in [{"value": year}, {"label": year}]:
            try:
                await sel.select_option(**opt)
            except Exception:
                continue
            # Η επιλογή έτους συνήθως κάνει submit/reload. Δίνουμε χρόνο να
            # ΞΕΚΙΝΗΣΕΙ η πλοήγηση πριν περιμένουμε να τελειώσει — αλλιώς το
            # wait_for_load_state επιστρέφει αμέσως και η πλοήγηση σκάει μετά,
            # καταστρέφοντας το context της επόμενης ενέργειας.
            await self.page.wait_for_timeout(600)
            await self._settle()
            self.log(f"  📅 Επιλέχθηκε έτος {year}")
            return
        self.log(f"  ⚠️ Βρέθηκε dropdown έτους αλλά δεν δέχτηκε την τιμή '{year}'", "error")

    async def _pdf(self, filepath: Path, download_sel: Optional[str] = None, doc_label: str = "doc"):
        """
        Σειρά προτεραιότητας:
          1. Πραγματικό PDF (download event ή re-fetch του viewer URL)
          2. Κουμπί λήψης/εκτύπωσης — και ξανά έλεγχος για PDF μετά το κλικ
          3. print-to-PDF (τελευταία λύση — βγάζει σωστό αποτέλεσμα μόνο για HTML σελίδες)
        """
        url = await self.save_real_pdf(filepath)
        if url:
            self.log(f"  ✅ Αποθηκεύτηκε το πραγματικό PDF: {url}", "success")
            return

        if download_sel:
            try:
                btn = await self.page.wait_for_selector(download_sel, timeout=5_000)
                await self._click_and_follow(btn)
            except PwTimeout:
                self.log(
                    f"  ⚠️ Δεν βρέθηκε κουμπί λήψης/εκτύπωσης για {doc_label} "
                    f"(δοκιμάστηκε: {download_sel})",
                    "error",
                )
            else:
                url = await self.save_real_pdf(filepath)
                if url:
                    self.log(f"  ✅ Αποθηκεύτηκε το πραγματικό PDF: {url}", "success")
                    return

        title = await self.page.title()
        shot_path = DEBUG_SHOT.with_name(f"gov_debug_{doc_label}.png")
        try:
            await self.page.screenshot(path=str(shot_path), full_page=True)
        except Exception:
            pass

        # ΔΕΝ σώζουμε print-to-PDF ως fallback. Δύο ξεχωριστοί λόγοι:
        #  (α) σε ορατό browser το page.pdf() κλείνει τον browser και χάνονται όλα
        #      τα επόμενα έγγραφα,
        #  (β) ΚΑΙ ΣΕ HEADLESS, όπου τεχνικά δουλεύει, έσωζε τη ΛΑΘΟΣ σελίδα
        #      (π.χ. το μενού αντί για το Ε3) με όνομα που έμοιαζε σωστό.
        # Ο λόγος (β) ισχύει ανεξάρτητα από το headless, γι' αυτό ο έλεγχος ΔΕΝ
        # γίνεται με το _headless: αλλιώς, περνώντας σε headless, θα επέστρεφε
        # σιωπηλά η αποθήκευση λάθος εγγράφων.
        if not self.ALLOW_PRINT_TO_PDF:
            raise RuntimeError(
                f"Δεν εντοπίστηκε πραγματικό PDF για {doc_label}: η σελίδα "
                f"'{title}' ({self.page.url}) δεν έδωσε αρχείο και δεν βρέθηκε "
                f"κουμπί λήψης/εκτύπωσης. Δεν αποθηκεύεται τίποτα, για να μη "
                f"σωθεί λάθος έγγραφο. Screenshot: {shot_path}"
            )

        self.log("  ⚠️ Δεν εντοπίστηκε πραγματικό PDF — fallback σε print-to-PDF", "error")
        self.log(
            f"  🖨️ Print-to-PDF fallback: σελίδα '{title}' στο {self.page.url} — "
            f"ΠΡΟΣΟΧΗ: αν αυτή δεν είναι η σελίδα με τα πραγματικά στοιχεία, το PDF θα βγει άδειο/λάθος.",
            "error",
        )
        await self.save_as_pdf(filepath)

    # ------------------------------------------------------------------
    # Έντυπο Ν  (νομικά πρόσωπα)
    # ------------------------------------------------------------------
    async def download_n(self, client_name: str, year: str, dl_dir: Path) -> str:
        self.log(f"📄 Έντυπο Ν ({year})…")

        # Τα hardcoded URLs με query params έδιναν HTTP 500 — πάμε από την αρχή
        # του portal και πλοηγούμαστε όπως ο χρήστης.
        await self._goto(INCOME_ENTRY)
        await self._select_taxpayer(self.is_atomiki)

        # Πλαϊνό μενού: «2.Προβολή» → «Δήλωσης»
        await self._click_labeled(["Δήλωσης", "Προβολή"], "Προβολή Δήλωσης (μενού)")
        await self._select_year(year)

        # Κλικ στη «Προβολή»/«Συνέχεια» της δήλωσης
        await self._click_labeled(
            ["Προβολή", "Συνέχεια", "Εκτύπωση"], f"προβολή δήλωσης Ν ({year})"
        )

        # Αποθήκευση PDF
        fname = self.safe_filename(client_name, year, "Ν")
        # Ψάχνουμε download button — αλλιώς print-to-PDF
        await self._pdf(
            dl_dir / fname,
            "a[href*='.pdf'], button:has-text('Λήψη'), a:has-text('Λήψη PDF')",
            doc_label="N",
        )
        self.log(f"✅ {fname}", "success")
        return fname

    # ------------------------------------------------------------------
    # Ε1  (φυσικά πρόσωπα — webtax portal)
    # ------------------------------------------------------------------
    async def download_e1(self, client_name: str, year: str, dl_dir: Path) -> str:
        self.log(f"📄 Ε1 ({year})…")
        await self._goto(WEBTAX_ENTRY)
        # Ενδιάμεση σελίδα καλωσορίσματος με κουμπί "Είσοδος στην εφαρμογή" (όχι πάντα παρούσα)
        await self._click_first([
            "button:has-text('Είσοδος στην εφαρμογή')", "a:has-text('Είσοδος στην εφαρμογή')"
        ], timeout=4_000, optional=True)
        await self._select_year(year)
        # Το μπλε κουμπί «Ε1» στη στήλη «Ψηφιακό Αρχείο Δήλωσης». Παλιότερα εδώ
        # γινόταν has-text('Ε1'), που είναι substring match: έπιανε και το
        # "Ε1 - ΣΥΝΟΨΗ" ή ανενεργά κουμπιά (έτη χωρίς δήλωση) και μετά έσκαγε
        # αργότερα στο PDF. Το _click_labeled δοκιμάζει ΠΡΩΤΑ ακριβές label και
        # παρακάμπτει τα disabled.
        found = await self._click_labeled(
            ["Ε1"],
            f"Ε1 ({year})",
            avoid=["ΣΥΝΟΨΗ", "myDATA", "Ε2", "Ε3"],
        )
        if not found:
            raise RuntimeError(
                f"Δεν βρέθηκε ενεργό κουμπί Ε1 για το έτος {year} στη σελίδα {self.page.url} "
                f"(πιθανόν δεν έχει υποβληθεί δήλωση για το έτος αυτό)"
            )
        fname = self.safe_filename(client_name, year, "Ε1")
        await self._pdf(dl_dir / fname,
                        "a:has-text('PDF'), a:has-text('Εκτύπωση'), button:has-text('Εκτύπωση')",
                        doc_label="E1")
        self.log(f"✅ {fname}", "success")
        return fname

    # ------------------------------------------------------------------
    # Ε3  (webtax portal)
    # ------------------------------------------------------------------
    async def download_e3(self, client_name: str, year: str, dl_dir: Path) -> str:
        self.log(f"📄 Ε3 ({year})…")
        await self._goto(WEBTAX_ENTRY)
        # Ενδιάμεση σελίδα καλωσορίσματος με κουμπί "Είσοδος στην εφαρμογή" (όχι πάντα παρούσα)
        await self._click_first([
            "button:has-text('Είσοδος στην εφαρμογή')", "a:has-text('Είσοδος στην εφαρμογή')"
        ], timeout=4_000, optional=True)
        await self._select_year(year)
        # Προτεραιότητα στο Ε3 ΤΟΥ ΥΠΟΧΡΕΟΥ. Αν δεν υπάρχει, παίρνουμε το
        # ΣΥΖΥΓΟΥ/ΜΣΣ αλλά το σώζουμε ως "Ε3_ΣΥΖΥΓΟΥ" ώστε να μη μπερδεύεται με
        # το Ε3 του πελάτη. Το "Ε3 - myDATA" (στοιχεία myDATA, όχι η δήλωση) και
        # οι "ΣΥΝΟΨΗ ..." αποκλείονται πάντα.
        # Τα λατινικά "E3" δεν χρειάζονται πια ξεχωριστά: το label_norm() μέσα
        # στο _click_labeled τα κανονικοποιεί σε ελληνικά.
        clicked = await self._click_labeled(
            ["Ε3 ΥΠΟΧΡΕΟΥ/ΜΣΣ", "Ε3 ΥΠΟΧΡΕΟΥ", "Ε3 ΣΥΖΥΓΟΥ/ΜΣΣ"],
            f"Ε3 ({year})",
            avoid=["myDATA", "ΣΥΝΟΨΗ"],
        )
        if not clicked:
            raise RuntimeError(
                f"Δεν βρέθηκε Ε3 (ούτε ΥΠΟΧΡΕΟΥ ούτε ΣΥΖΥΓΟΥ/ΜΣΣ) για το έτος {year} "
                f"στη σελίδα {self.page.url} — δες τα διαθέσιμα labels παραπάνω."
            )

        doc_type = "Ε3"
        if "ΣΥΖΥΓΟΥ" in label_norm(clicked):
            doc_type = "Ε3_ΣΥΖΥΓΟΥ"
            self.log(
                "  ℹ️ Δεν υπάρχει «Ε3 ΥΠΟΧΡΕΟΥ» για αυτόν τον φορολογούμενο — "
                "λήφθηκε το «Ε3 ΣΥΖΥΓΟΥ/ΜΣΣ» και αποθηκεύεται ως Ε3_ΣΥΖΥΓΟΥ.",
                "error",
            )
        fname = self.safe_filename(client_name, year, doc_type)
        await self._pdf(dl_dir / fname,
                        "a:has-text('PDF'), a:has-text('Εκτύπωση'), button:has-text('Εκτύπωση')",
                        doc_label="E3")
        self.log(f"✅ {fname}", "success")
        return fname

    # ------------------------------------------------------------------
    # Εκκαθαριστικό  (income portal)
    # ------------------------------------------------------------------
    async def download_ekkatharistiko(self, client_name: str, year: str, dl_dir: Path) -> str:
        self.log(f"📄 Εκκαθαριστικό / Πράξη Προσδιορισμού Φόρου ({year})…")
        await self._goto(WEBTAX_ENTRY)
        # Ενδιάμεση σελίδα καλωσορίσματος με κουμπί "Είσοδος στην εφαρμογή" (όχι πάντα παρούσα)
        await self._click_first([
            "button:has-text('Είσοδος στην εφαρμογή')", "a:has-text('Είσοδος στην εφαρμογή')"
        ], timeout=4_000, optional=True)
        await self._select_year(year)

        # Από το φορολογικό έτος 2014 και μετά δεν λέγεται πια "Εκκαθαριστικό" αλλά
        # "Πράξη Διοικητικού Προσδιορισμού Φόρου": στη στήλη «Ψηφιακό Αρχείο Πράξης
        # Προσδιορισμού Φόρου» το κουμπί λέγεται σκέτο "ΥΠΟΧΡΕΟΥ" (ή "ΣΥΖΥΓΟΥ/ΜΣΣ").
        # Το ακριβές label "ΥΠΟΧΡΕΟΥ" είναι μοναδικό — το "Ε2 ΥΠΟΧΡΕΟΥ" δεν ταιριάζει.
        # Τα "ΣΥΝΟΨΗ ..." είναι περίληψη, όχι το έγγραφο, γι' αυτό δεν τα ζητάμε.
        clicked = await self._click_labeled(
            ["ΥΠΟΧΡΕΟΥ", "ΣΥΖΥΓΟΥ/ΜΣΣ", "Εκκαθαριστικό"],
            f"Εκκαθαριστικό / Πράξη Προσδιορισμού Φόρου ({year})",
            avoid=["ΣΥΝΟΨΗ", "Ε2", "Ε1", "Ε3"],
        )
        if not clicked:
            raise RuntimeError(
                f"Δεν βρέθηκε Εκκαθαριστικό/Πράξη Προσδιορισμού Φόρου για το έτος {year} "
                f"στη σελίδα {self.page.url}"
            )

        # Ίδια λογική με το Ε3: αν πήραμε το ΣΥΖΥΓΟΥ/ΜΣΣ, φαίνεται στο όνομα.
        doc_type = "Εκκαθαριστικό"
        if "ΣΥΖΥΓΟΥ" in label_norm(clicked):
            doc_type = "Εκκαθαριστικό_ΣΥΖΥΓΟΥ"
            self.log(
                "  ℹ️ Δεν υπάρχει Πράξη Προσδιορισμού «ΥΠΟΧΡΕΟΥ» — λήφθηκε το "
                "«ΣΥΖΥΓΟΥ/ΜΣΣ» και αποθηκεύεται ως Εκκαθαριστικό_ΣΥΖΥΓΟΥ.",
                "error",
            )
        fname = self.safe_filename(client_name, year, doc_type)
        await self._pdf(dl_dir / fname,
                        "a:has-text('PDF'), a:has-text('Εκτύπωση'), button:has-text('Εκτύπωση')",
                        doc_label="ekkatharistiko")
        self.log(f"✅ {fname}", "success")
        return fname

    # ------------------------------------------------------------------
    # ΦΠΑ
    # ------------------------------------------------------------------
    async def download_fpa(self, client_name: str, year: str, dl_dir: Path) -> List[str]:
        """
        Ροή ΦΠΑ (Περιοδική Δήλωση = έντυπο Φ2):
          1. Επιλογή νομικού προσώπου (αν ζητηθεί)
          2. Στη ΓΡΑΜΜΗ Φ2: επιλογή έτους στο dropdown της στήλης «Έτος» —
             αυστηρά αυτό της γραμμής Φ2, όχι κάποιο άλλο της σελίδας
          3. Κλικ «Συνέχεια»
          4. Μέτρηση καταχωρήσεων της σελίδας: 4 για ατομική (τρίμηνα),
             12 για τα υπόλοιπα (μήνες) — προειδοποίηση αν διαφέρει
          5. Για ΚΑΘΕ καταχώρηση: κλικ «Επεξεργασία Δηλώσεων», και μετά
             – αν υπάρχει «Τροποποιητική»: κλικ στο «Προβολή» ΤΗΣ γραμμής της
             – αλλιώς: κλικ στο μοναδικό «Προβολή» της οθόνης
             → PDF, αριθμημένο ΦΠΑ_1 … ΦΠΑ_ν (με σήμανση ΤΡΟΠΟΠΟΙΗΤΙΚΗ)
        """
        self.log(f"📄 ΦΠΑ ({year})…")
        await self._goto(VAT_ENTRY)
        await self._select_taxpayer(self.is_atomiki)

        # Η σελίδα δείχνει πίνακα εντύπων (Φ1, Φ2, Φ4, Φ5…) με ΞΕΧΩΡΙΣΤΟ dropdown
        # έτους και κουμπί ανά γραμμή. Δουλεύουμε αυστηρά μέσα στη γραμμή Φ2.
        row = await self._row_locator("Φ2")
        if row is None:
            labels = [i["label"] for i in await self._clickables()]
            raise RuntimeError(
                f"Δεν βρέθηκε γραμμή Φ2 (Περιοδική Δήλωση ΦΠΑ) στη σελίδα {self.page.url}. "
                f"Διαθέσιμα: {labels}"
            )

        if not await self._select_year_in(row, year):
            raise RuntimeError(
                f"Δεν μπόρεσε να επιλεγεί το έτος {year} στο dropdown της γραμμής Φ2 "
                f"(δες τα διαθέσιμα έτη παραπάνω)"
            )

        # Στη γραμμή Φ2, μετά την επιλογή έτους, το κουμπί είναι «Συνέχεια».
        # (Το «Επεξεργασία Δηλώσεων» έρχεται ΑΡΓΟΤΕΡΑ, ανά περίοδο, στην επόμενη
        # σελίδα — γι' αυτό δεν το ζητάμε εδώ: παλιότερα ήταν πρώτο στη λίστα
        # προτίμησης και μπορούσε να πατηθεί λάθος κουμπί άλλης γραμμής.)
        clicked = await self._click_labeled(
            ["Συνέχεια", "Επεξεργασία Δηλώσεων", "Επεξεργασία"],
            "Συνέχεια (γραμμή Φ2)",
            scope=row,
        )
        if not clicked:
            # Μπορεί το κουμπί να είναι έξω από τη γραμμή — δοκιμή σε όλη τη σελίδα
            clicked = await self._click_labeled(
                ["Συνέχεια", "Επεξεργασία Δηλώσεων", "Επεξεργασία"], "Συνέχεια"
            )
        if not clicked:
            raise RuntimeError("Δεν βρέθηκε κουμπί «Συνέχεια» για τη γραμμή Φ2")

        # ── Σελίδα «Υποχρεώσεις Φορολογουμένου»: μία γραμμή ΑΝΑ ΠΕΡΙΟΔΟ
        # (π.χ. «1ος Μήνας 2026» ή «1ο Τρίμηνο»), καθεμία με το ΔΙΚΟ ΤΗΣ κουμπί
        # «Επεξεργασία Δηλώσεων». Οι δηλώσεις είναι ένα επίπεδο πιο βαθιά.
        periods_page = self.page
        periods_url = self.page.url
        periods = await self._find_row_actions(
            "Ενέργειες", ["Επεξεργασία Δηλώσεων", "Επεξεργασία"], "περίοδοι ΦΠΑ"
        )
        if not periods:
            # Διαγνωστικά: τι υπάρχει όντως στη σελίδα, για να μη ψάχνουμε στα τυφλά
            labels = [i["label"] for i in await self._clickables()]
            shot = DEBUG_SHOT.with_name("gov_debug_fpa_periods.png")
            try:
                await self.page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            await self._dump_table_html("Ενέργειες", "fpa_periods")
            raise RuntimeError(
                f"Δεν βρέθηκαν περίοδοι με «Επεξεργασία Δηλώσεων» στη σελίδα "
                f"{self.page.url}. Clickables που βρέθηκαν: {labels}. "
                f"Screenshot: {shot}"
            )

        # Επιβεβαίωση ότι ΟΝΤΩΣ βρέθηκε ο πίνακας περιόδων και όχι τυχαίος άλλος:
        # κάθε γραμμή περιόδου γράφει «… Τρίμηνο …» ή «… Μήνας …». Χωρίς αυτόν τον
        # έλεγχο, μια λάθος γραμμή πατιόταν στα τυφλά και η ροή έφευγε σε άσχετη
        # σελίδα, με τελικό μήνυμα «δεν κατέβηκε καμία δήλωση» που έκρυβε την αιτία.
        if not any("ΤΡΙΜΗΝ" in gr_norm(p["text"]) or "ΜΗΝΑ" in gr_norm(p["text"])
                   for p in periods):
            shot = DEBUG_SHOT.with_name("gov_debug_fpa_periods.png")
            try:
                await self.page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            await self._dump_table_html("Ενέργειες", "fpa_periods")
            raise RuntimeError(
                f"Βρέθηκαν {len(periods)} γραμμές στη στήλη «Ενέργειες», αλλά "
                f"καμία δεν μοιάζει με περίοδο (Τρίμηνο/Μήνας) — μάλλον "
                f"εντοπίστηκε λάθος πίνακας. Γραμμές: "
                f"{[p['text'][:40] for p in periods]}. Σελίδα: {self.page.url}. "
                f"Screenshot: {shot}"
            )

        # ── Έλεγχος πλήθους καταχωρήσεων: η ατομική δηλώνει ΦΠΑ ανά τρίμηνο (4
        # τον χρόνο), τα υπόλοιπα (διπλογραφικά) ανά μήνα (12). Αν ο αριθμός δεν
        # είναι ο αναμενόμενος, συνήθως σημαίνει λάθος έτος/γραμμή ή ημιτελές
        # φορτωμένη σελίδα. Είναι ΠΡΟΕΙΔΟΠΟΙΗΣΗ και όχι σφάλμα, γιατί υπάρχουν
        # νόμιμες εξαιρέσεις (έναρξη/διακοπή μέσα στο έτος, αλλαγή καθεστώτος,
        # τρέχον έτος που δεν έχει ακόμη όλες τις περιόδους).
        # Το καθεστώς ΔΕΝ προκύπτει από το is_atomiki: το portal έδειξε υποκείμενο
        # με Β΄/Γ΄ κατ. βιβλία που δηλώνει ΤΡΙΜΗΝΙΑΙΑ. Το διαβάζουμε από τα labels
        # των περιόδων («1ο Τρίμηνο» vs «1ος Μήνας»).
        # Ο αριθμός είναι ΑΝΩΤΑΤΟ ΟΡΙΟ, όχι ακριβής τιμή: στο τρέχον έτος
        # εμφανίζονται μόνο οι περίοδοι που έχουν λήξει (π.χ. τον Ιούλιο 2026
        # μόνο 1ο και 2ο τρίμηνο), και υπάρχουν έναρξη/διακοπή μέσα στο έτος.
        joined = gr_norm(" ".join(p["text"] for p in periods))
        if "ΤΡΙΜΗΝ" in joined:          # έλεγχος ΠΡΙΝ το «ΜΗΝΑ» — το «ΤΡΙΜΗΝΟ» το περιέχει
            regime, max_periods = "τριμηνιαία", 4
        elif "ΜΗΝΑ" in joined:
            regime, max_periods = "μηνιαία", 12
        else:
            regime, max_periods = None, None

        if max_periods is None:
            self.log(f"  🔢 {len(periods)} καταχωρήσεις (το καθεστώς δεν αναγνωρίστηκε)")
        elif len(periods) > max_periods:
            self.log(
                f"  ⚠️ Βρέθηκαν {len(periods)} καταχωρήσεις, ενώ σε {regime} δήλωση "
                f"δεν μπορούν να υπάρχουν πάνω από {max_periods} στο έτος. Πιθανόν "
                f"μπερδεύτηκε γραμμή/έτος — συνεχίζω με ό,τι βρέθηκε.",
                "error",
            )
        else:
            self.log(
                f"  🔢 {len(periods)}/{max_periods} καταχωρήσεις ({regime} δήλωση)"
            )

        # Περίοδοι χωρίς υποβληθείσα δήλωση δεν έχουν τι να κατεβάσουν.
        # ΠΡΟΣΟΧΗ: το «Δεν έχει υποβληθεί» περιέχει επίσης «υποβληθεί», άρα
        # πρέπει να αποκλειστεί ρητά — και με gr_norm(), γιατί οι τόνοι
        # χαλάνε τη σύγκριση (δες gr_norm στο base.py).
        def has_submitted(text: str) -> bool:
            n = gr_norm(text)
            if "ΔΕΝ ΕΧΕΙ ΥΠΟΒΛΗΘΕΙ" in n or "ΔΕΝ ΥΠΟΒΛΗΘΗΚΕ" in n:
                return False
            return "ΥΠΟΒΛΗΘΕΙ" in n

        submitted = [p for p in periods if has_submitted(p["text"])]
        if submitted:
            skipped = [p for p in periods if p not in submitted]
            for p in skipped:
                self.log(f"  ⏭️ Παραλείπεται (χωρίς δήλωση): {p['text'][:80]}")
            periods = submitted

        self.log(f"  📋 Βρέθηκαν {len(periods)} περίοδοι:")
        for p in periods:
            self.log(f"     • {p['text'][:110]}")

        # Τα σημάδια data-gdf-click ΧΑΝΟΝΤΑΙ σε κάθε πλοήγηση, γιατί η σελίδα
        # ξαναφορτώνει όταν γυρίζουμε από μια περίοδο. Άρα δεν κρατάμε δείκτες
        # από την πρώτη σάρωση (οι περίοδοι 2+ έσκαγαν): κρατάμε το ΚΕΙΜΕΝΟ κάθε
        # περιόδου ως ταυτότητα και ξανασαρώνουμε τη σελίδα σε κάθε επανάληψη.
        period_texts = [p["text"] for p in periods]

        saved: List[str] = []
        for n, ptext in enumerate(period_texts, start=1):
            self.log(f"  ── Περίοδος {n}/{len(period_texts)}: {ptext[:90]}")
            self.reset_pdf_captures()

            fresh = await self._find_row_actions(
                "Ενέργειες", ["Επεξεργασία Δηλώσεων", "Επεξεργασία"],
                f"περίοδος {n}",
            )
            period = next((f for f in fresh if f["text"] == ptext), None)
            if period is None:
                self.log(
                    f"  ⚠️ Η περίοδος {n} δεν βρέθηκε ξανά στη σελίδα — "
                    f"παραλείπεται. Διαθέσιμες: {[f['text'][:40] for f in fresh]}",
                    "error",
                )
                await self._back_to(periods_page, periods_url)
                continue

            if not await self._click_row_action(
                period, f"Επεξεργασία Δηλώσεων (περίοδος {n})"
            ):
                self.log(f"  ⚠️ Παραλείπεται η περίοδος {n}", "error")
                await self._back_to(periods_page, periods_url)
                continue

            # Λίστα δηλώσεων ΤΗΣ περιόδου: αρχική και (ίσως) τροποποιητικές.
            # ΔΙΑΓΝΩΣΤΙΚΟ: κρατάμε screenshot ΚΑΙ url αυτής της σελίδας. Είναι το
            # μόνο σημείο της ροής που δεν φαινόταν πουθενά όταν κάτι χαλούσε,
            # γιατί το _back_to() γυρίζει στη λίστα περιόδων πριν το τελικό σφάλμα.
            decls = await self._find_row_actions(
                "Ενέργειες", ["Προβολή", "Ανάκτηση"], f"δηλώσεις περιόδου {n}"
            )
            # Το screenshot ΜΕΤΑ την αναμονή, αλλιώς αποτύπωνε τη σελίδα πριν
            # ολοκληρωθεί η πλοήγηση και έδειχνε λάθος περιεχόμενο.
            self.log(f"     ↪ σελίδα δηλώσεων: {self.page.url}")
            shot = DEBUG_SHOT.with_name(f"gov_debug_fpa_period{n}.png")
            try:
                await self.page.screenshot(path=str(shot), full_page=True)
                self.log(f"     📷 {shot}")
            except Exception:
                pass

            if not decls:
                labels = [i["label"] for i in await self._clickables()]
                self.log(
                    f"  ⚠️ Περίοδος {n}: δεν βρέθηκε «Προβολή». "
                    f"Διαθέσιμα: {labels}", "error",
                )
                if n == 1:   # μία φορά αρκεί για διάγνωση
                    await self._dump_table_html("Ενέργειες", "fpa_declarations")
                await self._back_to(periods_page, periods_url)
                continue
            for d in decls:
                self.log(f"       – {d['text'][:100]}")

            pick = self._pick_declaration(decls)
            suffix = f"ΦΠΑ_{n}_ΤΡΟΠΟΠΟΙΗΤΙΚΗ" if pick["is_tropo"] else f"ΦΠΑ_{n}"
            # shift_year=False: το ΦΠΑ του 2025 αφορά περιόδους ΤΟΥ 2025
            fname = self.safe_filename(client_name, year, suffix, shift_year=False)

            # Το «Προβολή» ΤΗΣ γραμμής που επιλέχθηκε (τροποποιητική αν υπάρχει,
            # αλλιώς η μοναδική) — πατιέται με τον δείκτη του κουμπιού.
            self.log(f"     → «{pick['label']}» στη γραμμή: {pick['text'][:70]}")
            if not await self._click_row_action(
                pick, f"Προβολή δήλωσης περιόδου {n}"
            ):
                await self._back_to(periods_page, periods_url)
                continue

            # Αν μια περίοδος δεν δώσει PDF, συνεχίζουμε με τις επόμενες αντί να
            # χαθεί όλο το ΦΠΑ — το _pdf() πλέον πετάει σφάλμα αντί να σώζει
            # λάθος αρχείο.
            try:
                await self._pdf(dl_dir / fname,
                                "a:has-text('PDF'), a:has-text('Εκτύπωση'), button:has-text('Εκτύπωση')",
                                doc_label=f"fpa_{n}")
            except Exception as e:
                self.log(f"  ⚠️ Περίοδος {n}: {e}", "error")
                await self._back_to(periods_page, periods_url)
                continue
            self.log(f"✅ {fname}", "success")
            saved.append(fname)

            # Πίσω στη λίστα περιόδων για την επόμενη περίοδο
            await self._back_to(periods_page, periods_url)
            if self.page.url != periods_url:
                await self._goto(periods_url)

        if not saved:
            raise RuntimeError(f"Δεν κατέβηκε καμία δήλωση ΦΠΑ για το {year}")
        return saved

    # ------------------------------------------------------------------
    # Κεντρική
    # ------------------------------------------------------------------
    async def run(self, username: str, password: str, client_name: str,
                  year: str, documents: List[str], dl_dir: Path,
                  is_atomiki: bool = True) -> List[str]:
        self.is_atomiki = is_atomiki
        self.log(f"👤 Τύπος: {'Ατομική επιχείρηση' if is_atomiki else 'Νομικό πρόσωπο'}")
        await self.setup(headless=False)

        handlers = {
            "e1":             self.download_e1,
            "e3":             self.download_e3,
            "n":              self.download_n,
            "ekkatharistiko": self.download_ekkatharistiko,
            "fpa":            self.download_fpa,
        }

        downloaded: List[str] = []
        try:
            # Το interception ενεργοποιείται ΜΕΤΑ το login, για να μην επηρεαστεί
            # η αλυσίδα redirects του SSO (login.gsis.gr).
            await self.login(username, password)
            await self.start_pdf_interception()

            for doc in documents:
                if doc not in handlers:
                    continue
                try:
                    # Καθαρίζουμε ό,τι PDF πιάστηκε από το προηγούμενο έγγραφο,
                    # ώστε να μην αποθηκευτεί λάθος αρχείο.
                    self.reset_pdf_captures()
                    # Το ΦΠΑ επιστρέφει λίστα (μία δήλωση ανά περίοδο),
                    # τα υπόλοιπα ένα όνομα αρχείου.
                    result = await handlers[doc](client_name, year, dl_dir)
                    if isinstance(result, list):
                        downloaded.extend(result)
                    else:
                        downloaded.append(result)
                except Exception as e:
                    self.log(f"⚠️ {DOCUMENT_LABELS.get(doc, doc)}: {e}", "error")
                    # Ξεχωριστό screenshot ανά έγγραφο — αλλιώς το ένα σφάλμα
                    # έσβηνε το screenshot του προηγούμενου.
                    shot = DEBUG_SHOT.with_name(f"gov_debug_{doc}_error.png")
                    try:
                        await self.page.screenshot(path=str(shot), full_page=True)
                        self.log(f"  📸 Screenshot: {shot}", "error")
                    except Exception:
                        pass
        finally:
            await self.cleanup()

        return downloaded
