from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from automation.myaade import MyAADEAutomation

app = FastAPI(title="Gov Document Fetcher")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", Path.home() / "Downloads" / "GovDocs"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

sessions: dict[str, dict] = {}

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
    return HTMLResponse((Path(__file__).parent / "templates" / "index.html")
                        .read_text(encoding="utf-8"))


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
        log(f"🎉 Ολοκληρώθηκε! {len(all_files)} αρχεία.", "success")
        log(f"📁 {DOWNLOADS_DIR}", "info")

    except Exception as e:
        s["status"] = "error"
        log(f"❌ {e}", "error")
