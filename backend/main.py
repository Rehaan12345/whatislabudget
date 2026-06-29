"""FastAPI app for the LA Budget NotebookLM demo."""

import asyncio
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import NOTEBOOK_NAME
from backend.notebook_manager import NotebookManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI(title="LA Budget 2026-27 — Ask Anything")

# Wide-open CORS: this is a local demo served from file:// or a static host.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# One manager for the whole process. Reused by every request; never per-request.
manager = NotebookManager()


class AskRequest(BaseModel):
    question: str


def _reference_to_str(ref) -> str:
    """Render a ChatReference as a readable citation string.

    ChatReference fields (from notebooklm-py): source_id, citation_number,
    cited_text, start_char, end_char, ... The cited passage is the useful bit.
    """
    number = getattr(ref, "citation_number", None)
    text = getattr(ref, "cited_text", None)
    label = text.strip() if isinstance(text, str) and text.strip() else f"Source {getattr(ref, 'source_id', '?')}"
    return f"[{number}] {label}" if number is not None else label


@app.on_event("startup")
async def _startup():
    # Run initialization in the background so the server starts serving immediately.
    # The frontend polls /notebook-status to show a loading state during first-run upload.
    logger.info("Starting notebook initialization in the background…")
    asyncio.create_task(manager.initialize())


@app.on_event("shutdown")
async def _shutdown():
    await manager.aclose()


@app.get("/health")
async def health():
    if manager.status == "error":
        raise HTTPException(status_code=503, detail=f"NotebookLM unavailable: {manager.error}")
    return {
        "status": "ok" if manager.status == "ready" else manager.status,
        "notebook_id": manager._notebook_id,
        "notebook_name": NOTEBOOK_NAME,
    }


@app.get("/notebook-status")
async def notebook_status():
    return {
        "status": manager.status,          # initializing | ready | error
        "ready": manager.status == "ready",
        "notebook_id": manager._notebook_id,
        "error": manager.error,
    }


@app.post("/ask")
async def ask(req: AskRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is empty.")

    try:
        notebook_id = await manager.get_notebook_id()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        result = await manager.client.chat.ask(notebook_id, question)
    except Exception as e:  # noqa: BLE001 - never crash the server on a chat error
        logger.exception("chat.ask failed")
        raise HTTPException(status_code=502, detail=f"NotebookLM error: {e}")

    references = getattr(result, "references", None) or []
    return {
        "answer": getattr(result, "answer", ""),
        "citations": [_reference_to_str(r) for r in references],
        "question": question,
        "viz": None,  # reserved for iteration 2 (charts); always null for now
    }
