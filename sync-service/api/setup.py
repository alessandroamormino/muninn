"""Setup API router — POST /setup/suggest-config.

Dato il path di un CSV in /app/data/, legge headers + prime 10 righe,
chiama il LLM locale (qwen2.5:3b via Ollama) e restituisce una proposta
di configurazione (text_fields, metadata_fields, output_fields, id_field,
collection). Read-only: nessuna scrittura su file, nessuna modifica allo stato.
"""
from __future__ import annotations

import csv
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import require_admin
from auth.user_store import UserRecord

from config.settings import settings
from llm.ollama_llm import LLMError, OllamaLLMClient

logger = logging.getLogger(__name__)
router = APIRouter()

_DATA_ROOT = Path("/app/data")

# --- Prompt injection hardening (D-01..D-04, Phase 13.2) ---
_SUSPECT_RE = re.compile(r'ignore|system:|###|</?\w+>', re.I)
MAX_CELL = 200


def _sanitize_cell(v: str) -> str:
    """Sanitize a single cell value before LLM insertion (D-01..D-04, Phase 13.2).

    1. Stringify and strip leading/trailing whitespace
    2. Normalize whitespace: newlines/tabs -> single space
    3. Hard-truncate to MAX_CELL characters
    4. SUSPECT pattern match -> replace entirely with '[REDACTED]'

    Header names are NOT passed through this function — only cell VALUES.
    """
    v = str(v).strip()
    v = re.sub(r'[\n\t]+', ' ', v)
    v = v[:MAX_CELL]
    if _SUSPECT_RE.search(v):
        return '[REDACTED]'
    return v


class SuggestConfigRequest(BaseModel):
    file_path: str


def _collection_from_filename(file_path: str) -> str:
    """Derive PascalCase collection name from CSV filename (D-07).

    Examples: 'collaboratori.csv' -> 'Collaboratori'
              'test_fake.csv' -> 'TestFake'
    """
    stem = os.path.splitext(os.path.basename(file_path))[0]
    return stem.replace("_", " ").title().replace(" ", "")


def _read_csv_sample(resolved: Path) -> tuple[list[str], list[dict]]:
    """Return (headers, first_10_rows) with space->underscore column normalisation."""
    with open(resolved, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        raw_rows = []
        for i, row in enumerate(reader):
            if i >= 10:
                break
            raw_rows.append(row)
        raw_headers = list(reader.fieldnames or [])
    headers = [k.replace(" ", "_") for k in raw_headers]
    rows = [{k.replace(" ", "_"): v for k, v in row.items()} for row in raw_rows]
    return headers, rows


def _build_prompt(headers: list[str], rows: list[dict]) -> str:
    """Build the LLM prompt containing column list, sample rows, and output schema.

    Instructs the model to classify each column as text_field, metadata_field,
    id_field, or skip; output must be a JSON object matching the specified schema.
    """
    # Serialize sample as mini-CSV table
    sample_lines = [",".join(headers)]
    for row in rows:
        sample_lines.append(",".join(_sanitize_cell(row.get(h, "")) for h in headers))
    sample_table = "\n".join(sample_lines)

    return f"""You are a data analyst helping configure a semantic search system.
Given a CSV with the following headers and sample rows, classify each column.

COLUMNS: {headers}

SAMPLE DATA (first rows):
{sample_table}

CLASSIFICATION RULES:
- text_field: free-text columns suitable for semantic search (descriptions, titles, names, notes)
- metadata_field: structured/categorical columns used for filtering (IDs, emails, codes, dates, enums)
- id_field: the single best unique identifier column (prefer columns named 'id', 'ID', or similar)
- skip: redundant or derived columns

INSTRUCTIONS:
1. Classify every column into exactly one category.
2. Choose ONE column as id_field (it can also appear in metadata_fields).
3. output_fields should be a useful subset of text_fields union metadata_fields (5-8 fields).
4. graph_filter_fields: choose 1-5 categorical metadata columns with low cardinality (enums, status, type, category) that would be useful as graph filters. Exclude IDs, emails, free-text, and high-cardinality fields.
5. Return ONLY a JSON object with this exact schema - no prose, no explanation outside the JSON:

{{
  "id_field": "<column_name>",
  "text_fields": ["<col>", ...],
  "metadata_fields": ["<col>", ...],
  "output_fields": ["<col>", ...],
  "graph_filter_fields": ["<col>", ...],
  "reasoning": {{
    "<col>": "<one-line classification reason>",
    ...
  }}
}}

Respond with valid JSON only."""


def _validate_suggested_fields(suggested: dict, headers: list[str]) -> None:
    """Raise ValueError if any suggested field name is not in the CSV headers (D-10 / success criterion 4)."""
    header_set = set(headers)
    for key in ("text_fields", "metadata_fields", "output_fields", "graph_filter_fields"):
        for field in suggested.get(key, []):
            if field not in header_set:
                raise ValueError(
                    f"Suggested field {field!r} (in {key}) does not exist in CSV headers: {sorted(header_set)}"
                )
    id_field = suggested.get("id_field", "")
    if id_field and id_field not in header_set:
        raise ValueError(
            f"Suggested id_field {id_field!r} does not exist in CSV headers: {sorted(header_set)}"
        )


@router.post("/setup/suggest-config")
def suggest_config(body: SuggestConfigRequest, _: UserRecord = Depends(require_admin)) -> dict:
    """Analizza un CSV e suggerisce una configurazione Weaviate.

    Il path deve puntare a un file in /app/data/. L'endpoint e' read-only:
    non modifica config.yaml ne' lo stato del sistema.
    """
    # --- Path validation (D-02, D-10) — before any I/O or LLM call -----------
    requested = Path(body.file_path)
    # Resolve relative paths against /app (container root) as config.yaml does
    if not requested.is_absolute():
        requested = Path("/app") / requested

    try:
        resolved = requested.resolve()
        resolved.relative_to(_DATA_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=422, detail="path not allowed")

    if not resolved.exists():
        raise HTTPException(status_code=422, detail="file not found")

    # --- Read CSV sample -------------------------------------------------------
    headers, rows = _read_csv_sample(resolved)
    if not headers:
        raise HTTPException(status_code=422, detail="CSV file has no columns")

    # --- Derive collection name deterministically (D-07) ----------------------
    collection = _collection_from_filename(body.file_path)

    # --- Call LLM (D-04, D-05) ------------------------------------------------
    llm = OllamaLLMClient(settings.embedding)
    prompt = _build_prompt(headers, rows)

    try:
        llm_result = llm.generate(prompt)
    except LLMError as exc:
        logger.warning("LLM call failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="LLM unavailable — make sure Ollama is running",
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("suggest_config failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="LLM unavailable — make sure Ollama is running",
        )

    # --- Validate LLM output fields exist in headers (success criterion 4) ----
    try:
        _validate_suggested_fields(llm_result, headers)
    except ValueError as exc:
        logger.warning("LLM suggested non-existent fields: %s", exc)
        # Return partial result with a warning rather than a 5xx;
        # the user can still review and correct the suggestion.
        llm_result["_warning"] = str(exc)

    # --- Build response (D-06) ------------------------------------------------
    suggested_config = {
        "id_field": llm_result.get("id_field", ""),
        "collection": collection,
        "text_fields": llm_result.get("text_fields", []),
        "metadata_fields": llm_result.get("metadata_fields", []),
        "output_fields": llm_result.get("output_fields", []),
        "graph_filter_fields": llm_result.get("graph_filter_fields", []),
    }

    response: dict = {
        "suggested_config": suggested_config,
        "reasoning": llm_result.get("reasoning", {}),
    }
    if "_warning" in llm_result:
        response["_warning"] = llm_result["_warning"]
    return response
