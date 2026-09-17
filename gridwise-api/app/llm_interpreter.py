"""
LLM Interpreter module.
Supports multiple LLM providers:
  - Groq (default, fast, recommended)
  - Google Gemini (fallback)
  
Environment variables:
  LLM_PROVIDER: "groq" (default) or "gemini"
  GROQ_API_KEY: Required when using Groq
  GROQ_MODEL:   Optional Groq model (default: llama-3.3-70b-versatile)
  GEMINI_API_KEY: Required when using Gemini
  GEMINI_MODEL:   Optional Gemini model (default: gemini-2.0-flash)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# ---------------------------------------------------------------------------
# Supported directive type descriptions for the prompt
# ---------------------------------------------------------------------------
DIRECTIVE_DESCRIPTIONS = """
Supported directive types:
1. solar_reduction       → Reduce usable solar during specific hours.
                           structured_adjustment: {"hours": [...], "factor": <fraction remaining, 0-1>}
                           Example: "80% reduction" → factor=0.2 (not 0.8); "drop to 25%" → factor=0.25

2. minimum_battery_reserve → Keep battery energy at or above a required level.
                           structured_adjustment: {"hours": [...], "minimum_energy_kwh": <number>}

3. no_charge_window      → Battery charging is unavailable during specific hours.
                           structured_adjustment: {"hours": [...]}

4. no_discharge_window   → Battery discharging is unavailable during specific hours.
                           structured_adjustment: {"hours": [...]}

5. max_grid_window       → Grid import may not exceed a stated amount during specific hours.
                           structured_adjustment: {"hours": [...], "max_grid_kwh": <number>}

6. no_op                 → The note does NOT affect the current 24-hour energy schedule.
                           structured_adjustment: null, applies: false

Time rules:
- Hours are integers 0-23 (0=midnight, 13=1PM, etc.)
- Windows are START-INCLUSIVE, END-EXCLUSIVE: "1 PM to 3 PM" → [13, 14]
- "noon until 2 PM" = [12, 13]  |  "6 PM until 9 PM" = [18, 19, 20]
- Hours array must be sorted ascending with unique integers.

Factor rule for solar_reduction:
- factor = the REMAINING fraction (not the reduction fraction)
- "drop to 20%" → factor=0.20
- "80% reduction" → factor=0.20
- "25% of forecast" → factor=0.25
- "roughly one-fifth of normal" → factor=0.20
"""

SYSTEM_PROMPT = f"""You are an energy scheduling assistant for a smart campus.
Your job is to interpret natural-language operator notes and classify each note into exactly one directive.

{DIRECTIVE_DESCRIPTIONS}

You must return ONLY a valid JSON array (no markdown, no extra text), one object per note.
Each object must have:
{{
  "note_index": <int starting at 0>,
  "applies": <bool>,
  "directive_type": <one of the directive type names>,
  "structured_adjustment": <object or null>,
  "explanation": "<brief string>"
}}

Important rules:
- If a note is irrelevant (e.g., cafeteria menu, schedule changes, non-energy topics), use directive_type="no_op", applies=false, structured_adjustment=null.
- Do NOT invent unsupported directive types.
- Do NOT modify demand, tariff, or battery parameters.
- Return exactly as many objects as there are notes, in note_index order.
- Return ONLY the JSON array. No preamble, no explanation outside the array.
"""


def _build_user_message(operator_notes: List[str], battery_capacity: float) -> str:
    notes_text = "\n".join(
        f"Note {i}: {note}" for i, note in enumerate(operator_notes)
    )
    return (
        f"Battery capacity: {battery_capacity} kWh\n\n"
        f"Operator notes:\n{notes_text}\n\n"
        f"Return the JSON array interpretation."
    )


def _extract_json_from_text(text: str) -> str:
    """Extract JSON array from model response, handling markdown code blocks."""
    text = text.strip()

    # Remove markdown code blocks if present
    if "```json" in text:
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
    elif "```" in text:
        text = re.sub(r"```\s*", "", text)

    text = text.strip()

    # Find the array start
    start = text.find("[")
    if start == -1:
        raise ValueError("No JSON array found in LLM response")

    # Find matching end bracket
    depth = 0
    end = -1
    for i, ch in enumerate(text[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        raise ValueError("Malformed JSON array in LLM response")

    return text[start:end]


def _call_groq(operator_notes: List[str], battery_capacity: float) -> List[Dict[str, Any]]:
    """Call Groq API using the openai-compatible SDK."""
    from groq import Groq

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY environment variable is not set")

    client = Groq(api_key=GROQ_API_KEY)
    user_message = _build_user_message(operator_notes, battery_capacity)

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            max_tokens=2048,
        )
        raw_text = response.choices[0].message.content
        logger.debug("Groq raw response: %s", raw_text)
    except Exception as exc:
        logger.error("Groq API call failed: %s", exc)
        raise RuntimeError(f"Groq LLM call failed: {exc}") from exc

    try:
        json_str = _extract_json_from_text(raw_text)
        parsed = json.loads(json_str)
    except Exception as exc:
        logger.error("Failed to parse Groq LLM JSON: %s | raw: %s", exc, raw_text)
        raise ValueError(f"Groq LLM returned unparseable output: {exc}") from exc

    if not isinstance(parsed, list):
        raise ValueError("Groq LLM response is not a JSON array")

    return parsed


def _call_gemini(operator_notes: List[str], battery_capacity: float) -> List[Dict[str, Any]]:
    """Call Google Gemini API."""
    import google.generativeai as genai

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
    )

    user_message = _build_user_message(operator_notes, battery_capacity)
    generation_config = genai.types.GenerationConfig(
        temperature=0.0,
        max_output_tokens=2048,
    )

    try:
        response = model.generate_content(user_message, generation_config=generation_config)
        raw_text = response.text
        logger.debug("Gemini raw response: %s", raw_text)
    except Exception as exc:
        logger.error("Gemini API call failed: %s", exc)
        raise RuntimeError(f"Gemini LLM call failed: {exc}") from exc

    try:
        json_str = _extract_json_from_text(raw_text)
        parsed = json.loads(json_str)
    except Exception as exc:
        logger.error("Failed to parse Gemini JSON: %s | raw: %s", exc, raw_text)
        raise ValueError(f"Gemini LLM returned unparseable output: {exc}") from exc

    if not isinstance(parsed, list):
        raise ValueError("Gemini LLM response is not a JSON array")

    return parsed


def interpret_notes_with_llm(
    operator_notes: List[str],
    battery_capacity: float,
) -> List[Dict[str, Any]]:
    """
    Interpret operator notes using the configured LLM provider.
    Returns a list of raw dicts (not yet guardrail-validated).
    """
    provider = LLM_PROVIDER
    logger.info("Using LLM provider: %s", provider)

    if provider == "groq":
        return _call_groq(operator_notes, battery_capacity)
    elif provider == "gemini":
        return _call_gemini(operator_notes, battery_capacity)
    else:
        raise RuntimeError(f"Unknown LLM_PROVIDER: {provider!r}. Use 'groq' or 'gemini'.")
