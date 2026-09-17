"""
Guardrails module.
Deterministically validates and cleans raw LLM output before it touches the optimizer.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models import DirectiveInterpretation, DirectiveType

logger = logging.getLogger(__name__)

VALID_DIRECTIVE_TYPES = {dt.value for dt in DirectiveType}

# Required structured_adjustment shapes per directive type
REQUIRED_FIELDS: Dict[str, List[str]] = {
    "solar_reduction": ["hours", "factor"],
    "minimum_battery_reserve": ["hours", "minimum_energy_kwh"],
    "no_charge_window": ["hours"],
    "no_discharge_window": ["hours"],
    "max_grid_window": ["hours", "max_grid_kwh"],
    "no_op": [],  # no adjustment
}


class GuardrailError(Exception):
    """Raised when an LLM output item fails validation."""


def _validate_hours(hours: Any) -> List[int]:
    """Validate and return sorted unique hour list."""
    if not isinstance(hours, list):
        raise GuardrailError(f"hours must be a list, got {type(hours)}")
    cleaned = []
    for h in hours:
        if not isinstance(h, (int, float)) or not float(h).is_integer():
            raise GuardrailError(f"Each hour must be an integer, got {h!r}")
        h_int = int(h)
        if h_int < 0 or h_int > 23:
            raise GuardrailError(f"Hour {h_int} is out of range 0-23")
        cleaned.append(h_int)
    if len(cleaned) != len(set(cleaned)):
        raise GuardrailError("hours array contains duplicate values")
    if cleaned != sorted(cleaned):
        raise GuardrailError("hours array must be in ascending order")
    return cleaned


def validate_single_directive(
    raw: Dict[str, Any],
    note_count: int,
    battery_capacity: float,
    seen_indices: set,
) -> DirectiveInterpretation:
    """
    Validate a single raw LLM directive dict.
    Returns a validated DirectiveInterpretation or raises GuardrailError.
    """

    # --- note_index ---
    note_index = raw.get("note_index")
    if not isinstance(note_index, (int, float)) or not float(note_index).is_integer():
        raise GuardrailError(f"note_index must be an integer, got {note_index!r}")
    note_index = int(note_index)
    if note_index < 0 or note_index >= note_count:
        raise GuardrailError(f"note_index {note_index} out of range [0, {note_count - 1}]")
    if note_index in seen_indices:
        raise GuardrailError(f"Duplicate note_index {note_index}")

    # --- directive_type ---
    directive_type_raw = raw.get("directive_type")
    if directive_type_raw not in VALID_DIRECTIVE_TYPES:
        raise GuardrailError(f"Invalid directive_type: {directive_type_raw!r}")
    directive_type = DirectiveType(directive_type_raw)

    # --- applies ---
    applies = raw.get("applies")
    if not isinstance(applies, bool):
        # Try to coerce
        if str(applies).lower() == "true":
            applies = True
        elif str(applies).lower() == "false":
            applies = False
        else:
            raise GuardrailError(f"applies must be boolean, got {applies!r}")

    # --- applies semantics ---
    if directive_type == DirectiveType.no_op and applies is not False:
        raise GuardrailError("no_op must have applies=false")
    if directive_type != DirectiveType.no_op and applies is not True:
        raise GuardrailError(f"Non-no_op directive must have applies=true, got applies={applies}")

    # --- structured_adjustment ---
    sa = raw.get("structured_adjustment")

    if directive_type == DirectiveType.no_op:
        if sa is not None:
            logger.warning("Correcting: structured_adjustment for no_op should be null, fixing.")
            sa = None
        explanation = raw.get("explanation", "This note does not affect the energy schedule.")
        return DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type=DirectiveType.no_op,
            structured_adjustment=None,
            explanation=explanation,
        )

    # For non-no_op directives, validate structured_adjustment
    if sa is None or not isinstance(sa, dict):
        raise GuardrailError(
            f"structured_adjustment must be a dict for directive_type={directive_type_raw}, got {sa!r}"
        )

    required = REQUIRED_FIELDS.get(directive_type_raw, [])
    for field in required:
        if field not in sa:
            raise GuardrailError(f"structured_adjustment missing required field '{field}' for {directive_type_raw}")

    validated_sa: Dict[str, Any] = {}

    # Validate hours for all non-no_op directives
    validated_sa["hours"] = _validate_hours(sa["hours"])
    if len(validated_sa["hours"]) == 0:
        raise GuardrailError("hours array must not be empty")

    # Directive-specific numeric validation
    if directive_type == DirectiveType.solar_reduction:
        factor = sa.get("factor")
        if not isinstance(factor, (int, float)):
            raise GuardrailError(f"solar_reduction factor must be numeric, got {factor!r}")
        factor = float(factor)
        if not (0.0 <= factor <= 1.0):
            raise GuardrailError(f"solar_reduction factor must be in [0, 1], got {factor}")
        validated_sa["factor"] = factor

    elif directive_type == DirectiveType.minimum_battery_reserve:
        min_kwh = sa.get("minimum_energy_kwh")
        if not isinstance(min_kwh, (int, float)):
            raise GuardrailError(f"minimum_energy_kwh must be numeric, got {min_kwh!r}")
        min_kwh = float(min_kwh)
        if min_kwh < 0:
            raise GuardrailError(f"minimum_energy_kwh must be non-negative, got {min_kwh}")
        if min_kwh > battery_capacity:
            raise GuardrailError(
                f"minimum_energy_kwh ({min_kwh}) exceeds battery capacity ({battery_capacity})"
            )
        validated_sa["minimum_energy_kwh"] = min_kwh

    elif directive_type == DirectiveType.max_grid_window:
        max_grid = sa.get("max_grid_kwh")
        if not isinstance(max_grid, (int, float)):
            raise GuardrailError(f"max_grid_kwh must be numeric, got {max_grid!r}")
        max_grid = float(max_grid)
        if max_grid < 0:
            raise GuardrailError(f"max_grid_kwh must be non-negative, got {max_grid}")
        validated_sa["max_grid_kwh"] = max_grid

    # no_charge_window and no_discharge_window: only hours needed, already validated

    explanation = raw.get("explanation", f"Directive {directive_type_raw} applied.")
    if not isinstance(explanation, str):
        explanation = str(explanation)

    return DirectiveInterpretation(
        note_index=note_index,
        applies=True,
        directive_type=directive_type,
        structured_adjustment=validated_sa,
        explanation=explanation,
    )


def validate_all_directives(
    raw_list: List[Dict[str, Any]],
    note_count: int,
    battery_capacity: float,
) -> List[DirectiveInterpretation]:
    """
    Validate and return all directives, one per note.
    Falls back to no_op for any invalid directive (safe failure).
    Returns list sorted by note_index 0..N-1.
    """
    if not isinstance(raw_list, list):
        logger.error("LLM returned non-list; creating no_op fallback for all notes")
        return _fallback_all_no_op(note_count)

    results: Dict[int, DirectiveInterpretation] = {}
    seen_indices: set = set()

    for raw in raw_list:
        if not isinstance(raw, dict):
            logger.warning("Skipping non-dict item in LLM output: %s", raw)
            continue
        try:
            di = validate_single_directive(raw, note_count, battery_capacity, seen_indices)
            seen_indices.add(di.note_index)
            results[di.note_index] = di
        except GuardrailError as exc:
            note_idx = raw.get("note_index")
            if isinstance(note_idx, (int, float)):
                note_idx = int(note_idx)
                if 0 <= note_idx < note_count and note_idx not in seen_indices:
                    logger.warning(
                        "Guardrail failed for note %d (%s), falling back to no_op", note_idx, exc
                    )
                    results[note_idx] = _make_no_op(note_idx, f"Guardrail failure: {exc}")
                    seen_indices.add(note_idx)
            else:
                logger.warning("Could not extract note_index from invalid directive: %s", exc)

    # Fill any missing notes with no_op
    for i in range(note_count):
        if i not in results:
            logger.warning("Missing directive for note_index %d, falling back to no_op", i)
            results[i] = _make_no_op(i, "No directive produced by LLM for this note.")

    # Return sorted by note_index
    return [results[i] for i in sorted(results.keys())]


def _make_no_op(note_index: int, explanation: str = "") -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type=DirectiveType.no_op,
        structured_adjustment=None,
        explanation=explanation or "This note does not affect the current energy schedule.",
    )


def _fallback_all_no_op(note_count: int) -> List[DirectiveInterpretation]:
    return [_make_no_op(i, "LLM output was invalid; safe fallback to no_op.") for i in range(note_count)]
