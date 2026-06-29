"""Two-step viz pipeline: classify intent, then extract chart data.

Runs after NotebookLM answers. Any failure (no API key, classifier error,
bad JSON, empty extraction) returns None so the answer is never blocked.
"""

import json
import logging

from anthropic import AsyncAnthropic

logger = logging.getLogger("intent")

MODEL = "claude-haiku-4-5"

INTENT_SYSTEM = """You are a classifier. Given a user question about a government budget,
determine whether the answer would benefit from a chart.

Respond with JSON only, no preamble:
{
  "needs_viz": true | false,
  "viz_type": "bar" | "pie" | "single_stat" | null,
  "reason": "one sentence"
}

Rules:
- needs_viz = true when the question compares multiple items, asks for rankings,
  distributions, proportions, or asks "how much" about something with a known numeric answer
- needs_viz = false for process questions, timeline questions, "who" questions,
  "why" questions, or anything that doesn't have a clean numeric answer
- viz_type "bar" for comparisons and rankings (most vs least, top N, side by side)
- viz_type "pie" for proportions and share of a whole ("what percentage", "breakdown of")
- viz_type "single_stat" for a single important number ("how much does X cost", "what is the total")
- When in doubt, needs_viz = false"""

EXTRACT_SYSTEM = """You are a data extractor. Given a text answer about a government budget,
extract the numeric data into a chart-ready JSON structure.

Respond with JSON only, no preamble, no markdown fences:
{
  "title": "short descriptive chart title",
  "data": [
    {"label": "string", "value": number}
  ],
  "unit": "dollars" | "percent" | "count",
  "note": "optional caveat about the data, or null"
}

Rules:
- Extract only numbers that are explicitly stated in the answer text
- Do not infer, estimate, or calculate values not present in the text
- Labels should be short (department name only, not full sentences)
- Values must be raw numbers, never strings — no dollar signs, no commas
- If you cannot extract clean numeric data, return {"data": []}
- Maximum 15 data points"""

REWRITE_SYSTEM = """You are a helpful assistant explaining Los Angeles city budget information
to everyday residents. Rewrite the following answer in plain, conversational
English — like you're explaining it to a smart friend who doesn't work in government.

Rules:
- Keep it concise. If the original is a long list, summarize the key takeaway
  first, then give the most important details. Maximum 3-4 sentences for simple
  questions, maximum 6-7 sentences for complex comparisons.
- Preserve every number exactly as stated. Never round or approximate a figure
  that was given precisely.
- Do not add information that isn't in the original answer.
- Do not reference "the document", "the budget resolution", or "sources" —
  just state the facts directly.
- If a chart is being shown alongside this answer, briefly acknowledge it
  (e.g. "You can see the full breakdown in the chart below.") at the end.
  If no chart, don't mention one.
- Use plain dollar formatting ($2.1 billion, not $2,114,202,404)."""

_client = None
_client_failed = False


def _get_client():
    """Lazily build the client; cache a failure (e.g. no API key) so we don't retry it."""
    global _client, _client_failed
    if _client is None and not _client_failed:
        try:
            _client = AsyncAnthropic()
        except Exception as e:  # noqa: BLE001 - missing key etc. -> just disable viz
            logger.warning("Anthropic client unavailable, viz disabled: %s", e)
            _client_failed = True
    return _client


def _parse_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        # tolerate a ```json ... ``` fence despite the "no fences" instruction
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


async def _ask_model(system: str, user: str):
    client = _get_client()
    if client is None:
        return None
    resp = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return _parse_json(text)


async def _ask_model_text(system: str, user: str) -> str:
    client = _get_client()
    if client is None:
        return ""
    resp = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "")


async def rewrite_answer(answer: str, question: str, has_viz: bool) -> str:
    """Rewrite NotebookLM's formal answer into plain conversational English.

    Falls back to the original answer on any failure — never surfaces an error.
    """
    try:
        chart_note = (
            "A chart IS being shown alongside this answer."
            if has_viz
            else "No chart is being shown alongside this answer."
        )
        system = REWRITE_SYSTEM + "\n\n" + chart_note
        user = f"Question: {question}\n\nAnswer to rewrite:\n{answer}"
        out = (await _ask_model_text(system, user)).strip()
        return out or answer
    except Exception as e:  # noqa: BLE001 - never surface rewrite errors to the user
        logger.warning("rewrite_answer failed, using original answer: %s", e)
        return answer


async def get_viz(question: str, answer: str) -> dict | None:
    """Return {type, title, data, unit, note} or None when no chart applies."""
    try:
        # Step 1 — intent classification
        intent = await _ask_model(INTENT_SYSTEM, question)
        if not intent or not intent.get("needs_viz"):
            return None
        viz_type = intent.get("viz_type")
        if viz_type not in ("bar", "pie", "single_stat"):
            return None

        # Step 2 — data extraction (only runs when a chart is warranted)
        extracted = await _ask_model(EXTRACT_SYSTEM, answer)
        if not extracted:
            return None
        data = extracted.get("data") or []
        if not data:
            return None

        return {
            "type": viz_type,
            "title": extracted.get("title", ""),
            "data": data,
            "unit": extracted.get("unit"),
            "note": extracted.get("note"),
        }
    except Exception as e:  # noqa: BLE001 - extraction must never break the answer
        logger.warning("get_viz failed, returning no viz: %s", e)
        return None
