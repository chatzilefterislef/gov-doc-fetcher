from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from automation.base import debug_dir
from automation.myaade import MyAADEAutomation

app = FastAPI(title="Gov Document Fetcher")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def resource(rel: str) -> Path:
    """
    Διαδρομή σε πόρο (π.χ. templates/index.html) που δουλεύει και όταν η
    εφαρμογή είναι πακεταρισμένη με PyInstaller.

    Στο bundle τα αρχεία δεν βρίσκονται δίπλα στο .py αλλά σε προσωρινό φάκελο
    που το PyInstaller δίνει στο sys._MEIPASS — χωρίς αυτό η σελίδα έβγαζε
    FileNotFoundError μόλις άνοιγε ο χρήστης την εφαρμογή.
    """
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) if base else Path(__file__).parent) / rel

DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", Path.home() / "Downloads" / "GovDocs"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

sessions: dict[str, dict] = {}

# Τα μηνύματα προόδου ζουν στη μνήμη (sessions) και χάνονται σε κάθε restart του
# server, οπότε μετά από ένα σφάλμα δεν έμενε κανένα ίχνος για διάγνωση.
# Κρατάμε αντίγραφο σε αρχείο. Δεν γράφονται ποτέ κωδικοί — μόνο τα μηνύματα.
# debug_dir() και όχι σκέτο "/tmp": στα Windows δεν υπάρχει /tmp και η εγγραφή
# αποτύγχανε σιωπηλά (είναι σε try/except), οπότε χανόταν κάθε ίχνος διάγνωσης
# στο μηχάνημα του συναδέλφου. Σε macOS/Linux παραμένει /tmp, όπως πριν.
LOG_FILE = debug_dir() / "gov_doc_fetcher.log"

MYAADE_DOCS = {"e1", "e3", "n", "ekkatharistiko", "fpa"}


class DownloadRequest(BaseModel):
    username: str
    password: str
    client_name: str
    year: str
    documents: List[str]
    # Ατομική επιχείρηση = φυσικό πρόσωπο. Καθορίζει αν θα επιλεγεί νομικό
    # πρόσωπο στα portals που το ζητούν — αλλιώς κατεβαίνουν τα έγγραφα
    # ΑΛΛΗΣ οντότητας που τυχόν εκπροσωπεί ο χρήστης.
    is_atomiki: bool = True


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(resource("templates/index.html").read_text(encoding="utf-8"))


@app.post("/api/start")
async def start(req: DownloadRequest):
    session_id = str(uuid.uuid4())
    sessions[session_id] = {"status": "running", "messages": [], "files": []}
    asyncio.create_task(_run(session_id, req))
    return {"session_id": session_id}


@app.get("/api/progress/{session_id}")
async def progress(session_id: str):
    async def stream():
        if session_id not in sessions:
            yield f"data: {json.dumps({'type':'error','message':'Session not found'})}\n\n"
            return
        sent = 0
        while True:
            s = sessions[session_id]
            while sent < len(s["messages"]):
                yield f"data: {json.dumps(s['messages'][sent])}\n\n"
                sent += 1
            if s["status"] in ("done", "error"):
                yield f"data: {json.dumps({'type':'done','files':s['files'],'status':s['status']})}\n\n"
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/files")
async def list_files():
    return [
        {"name": f.name, "size": f.stat().st_size}
        for f in sorted(DOWNLOADS_DIR.glob("*.pdf"),
                        key=lambda x: x.stat().st_mtime, reverse=True)
    ]


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    p = DOWNLOADS_DIR / filename
    if not p.exists():
        raise HTTPException(404, "Αρχείο δεν βρέθηκε")
    return FileResponse(p, filename=filename, media_type="application/pdf")


@app.get("/api/downloads-dir")
async def get_dl_dir():
    return {"path": str(DOWNLOADS_DIR)}


async def _run(session_id: str, req: DownloadRequest):
    s = sessions[session_id]

    def log(msg: str, level: str = "info"):
        s["messages"].append({"type": level, "message": msg})
        try:
            with LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(f"[{level}] {msg}\n")
        except Exception:
            pass  # το logging δεν πρέπει ποτέ να ρίξει το τρέξιμο

    try:
        # Καθαρό αρχείο ανά τρέξιμο, για να μη μπερδεύονται παλιά σφάλματα
        try:
            LOG_FILE.write_text(
                f"=== {req.client_name} | Έτος: {req.year} | "
                f"{'ατομική' if req.is_atomiki else 'νομικό πρόσωπο'} | "
                f"έγγραφα: {req.documents} ===\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        log(f"🚀 Πελάτης: {req.client_name} | Έτος: {req.year}")

        myaade_docs = [d for d in req.documents if d in MYAADE_DOCS]
        all_files: List[str] = []

        if myaade_docs:
            auto = MyAADEAutomation(log)
            files = await auto.run(
                username=req.username,
                password=req.password,
                client_name=req.client_name,
                year=req.year,
                documents=myaade_docs,
                dl_dir=DOWNLOADS_DIR,
                is_atomiki=req.is_atomiki,
            )
            all_files.extend(files)

        s["files"] = all_files
        s["status"] = "done"
        # Ουδέτερο μήνυμα: το «🎉 Ολοκληρώθηκε!» εμφανιζόταν ως επιτυχία ακόμη κι
        # όταν τα μισά έγγραφα είχαν αποτύχει. Η αναλυτική σύνοψη μπαίνει από το
        # MyAADEAutomation.run() λίγο πιο πάνω στο log.
        log(f"🏁 Τέλος — {len(all_files)} αρχεία αποθηκεύτηκαν.",
            "success" if all_files else "error")
        log(f"📁 {DOWNLOADS_DIR}", "info")

    except Exception as e:
        s["status"] = "error"
        log(f"❌ {e}", "error")
