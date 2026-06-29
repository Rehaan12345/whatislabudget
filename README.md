# LA Budget 2026-27 — Ask Anything (demo)

A small demo that loads the LA City Budget FY2026-27 PDF into NotebookLM once and
exposes a question/answer UI over it. FastAPI backend + single-file HTML frontend.

This is a demo, not production. The goal is to see how much can be pulled from the
budget PDF through NotebookLM as the Q&A backbone.

## Setup

### 1. One-time install + Google login

```bash
pip install "notebooklm-py[browser]"
notebooklm login
```

`notebooklm login` opens a browser for Google auth and caches credentials locally.
Run it once before starting the server. (The app authenticates via
`NotebookLMClient.from_storage()`, which reuses those cached credentials.)

Install the rest of the dependencies:

```bash
pip install -r requirements.txt
```

### 2. Place the budget PDF

Put the source PDF at:

```
data/la-budget-2026.pdf
```

### 3. Start the server

```bash
uvicorn backend.main:app --reload
```

Run this from the repo root so `data/la-budget-2026.pdf` resolves.

### 4. Open the frontend

Open `frontend/index.html` in a browser (double-click, or serve it statically).
It talks to the backend at `http://localhost:8000`.

### 5. First run takes ~60 seconds

On first start the app creates a NotebookLM notebook and uploads the PDF; questions
won't work until indexing finishes. The frontend shows a "Loading budget…" state and
starts working once status is **Ready**. The notebook id is saved to `.notebook_id`,
so restarting the server reuses the same notebook and does **not** re-upload.

## Endpoints

- `GET /health` — `{status, notebook_id, notebook_name}`
- `GET /notebook-status` — `{status, ready, notebook_id, error}` (frontend polls this)
- `POST /ask` — body `{"question": "..."}` → `{answer, citations, question, viz}`
  (`viz` is reserved for a later charts iteration and is always `null` for now)
