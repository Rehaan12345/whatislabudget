"""Owns the NotebookLM client lifecycle and ensures the budget PDF is loaded once.

A single instance is created at app startup and reused by every request. It never
re-uploads the PDF: on startup it checks `.notebook_id`, validates the saved id
against the live notebook list, and only creates + uploads when there is no valid
existing notebook.
"""

import logging
from pathlib import Path

from notebooklm import NotebookLMClient

from backend.config import NOTEBOOK_NAME, PDF_PATH, NOTEBOOK_ID_FILE

logger = logging.getLogger("notebook_manager")


def _get_id(obj):
    """Notebook objects expose `.id`; tolerate dict-shaped responses too."""
    if isinstance(obj, dict):
        return obj.get("id")
    return getattr(obj, "id", None)


class NotebookManager:
    def __init__(self):
        self._ctx = None          # the async-context-manager returned by from_storage()
        self._client = None       # the live NotebookLMClient, held open for the app's lifetime
        self._notebook_id = None
        self.status = "initializing"  # one of: initializing | ready | error
        self.error = None

    # ----- lifecycle -------------------------------------------------------
    async def initialize(self):
        """Authenticate, then ensure exactly one notebook with the PDF indexed."""
        try:
            # from_storage() is an async context manager. We enter it manually and
            # keep the client open so every request reuses one authenticated session.
            self._ctx = NotebookLMClient.from_storage()
            self._client = await self._ctx.__aenter__()

            saved = self._read_saved_id()
            if saved and await self._notebook_exists(saved):
                self._notebook_id = saved
                logger.info("Reusing existing notebook %s", saved)
            else:
                if saved:
                    logger.info("Saved notebook %s is gone; creating a fresh one", saved)
                await self._create_and_upload()

            self.status = "ready"
            logger.info("Notebook ready (%s)", self._notebook_id)
        except Exception as e:  # noqa: BLE001 - surface any startup failure as status=error
            self.status = "error"
            self.error = str(e)
            logger.exception("NotebookManager initialization failed")

    async def aclose(self):
        if self._ctx is not None:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None
            self._client = None

    # ----- accessors -------------------------------------------------------
    async def get_notebook_id(self) -> str:
        if self.status != "ready" or not self._notebook_id:
            raise RuntimeError(f"Notebook not ready (status={self.status})")
        return self._notebook_id

    @property
    def client(self) -> NotebookLMClient:
        return self._client

    # ----- internals -------------------------------------------------------
    async def _create_and_upload(self):
        nb = await self._client.notebooks.create(NOTEBOOK_NAME)
        self._notebook_id = _get_id(nb)
        logger.info("Uploading PDF, this may take a minute…")
        # wait=True blocks until indexing finishes. Adding more sources later
        # (iteration 3) is the same call against this notebook_id — no reset needed.
        await self._client.sources.add_file(
            self._notebook_id, PDF_PATH, wait=True, wait_timeout=300
        )
        self._write_saved_id(self._notebook_id)

    async def _notebook_exists(self, notebook_id: str) -> bool:
        try:
            notebooks = await self._client.notebooks.list()
        except Exception as e:  # noqa: BLE001 - if we can't list, treat as not-found
            logger.warning("Could not list notebooks to validate saved id: %s", e)
            return False
        return any(_get_id(n) == notebook_id for n in notebooks)

    def _read_saved_id(self):
        p = Path(NOTEBOOK_ID_FILE)
        if p.exists():
            return p.read_text().strip() or None
        return None

    def _write_saved_id(self, notebook_id: str):
        Path(NOTEBOOK_ID_FILE).write_text(notebook_id)
