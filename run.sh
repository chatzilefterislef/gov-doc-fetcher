#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Δημιουργία virtual environment αν δεν υπάρχει
if [ ! -d ".venv" ]; then
  echo "📦 Δημιουργία virtual environment…"
  python3 -m venv .venv
fi

source .venv/bin/activate

# Εγκατάσταση dependencies
echo "📦 Εγκατάσταση πακέτων…"
pip install -q -r requirements.txt

# Εγκατάσταση Playwright browsers (μόνο Chromium)
echo "🌐 Έλεγχος Playwright browsers…"
python -m playwright install chromium

echo ""
echo "✅ Έτοιμο! Ανοίξτε τον browser στη διεύθυνση: http://localhost:8000"
echo ""

# Εκκίνηση server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
