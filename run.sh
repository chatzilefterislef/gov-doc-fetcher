#!/usr/bin/env bash
# Συντόμευση για macOS/Linux — ίδιο αποτέλεσμα με `python3 run.py`.
#
# Η εγκατάσταση (venv, πακέτα, Chromium) και η εκκίνηση ζουν στο run.py, ώστε
# να μη διαφέρει η συμπεριφορά ανάμεσα σε macOS και Windows. Παλιότερα η λογική
# ήταν διπλή (εδώ σε bash) και ό,τι διορθωνόταν στο ένα έμενε πίσω στο άλλο.
set -e
cd "$(dirname "$0")"
exec python3 run.py "$@"
