"""
FastAPI application entry point for GridWise LLM-Assisted Energy Optimization.
BUP CSE Fest 2026 Hackathon Preliminary.
"""

from __future__ import annotations

import logging
import os
import traceback
from contextlib import asynccontextmanager

import app.config  # noqa: F401 — loads .env before any other imports

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.guardrails import validate_all_directives
from app.llm_interpreter import interpret_notes_with_llm
from app.models import HealthResponse, OptimizeRequest, OptimizeResponse
from app.optimizer import compute_totals, optimize

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("gridwise")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("GridWise API starting up")
    yield
    logger.info("GridWise API shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="GridWise LLM-Assisted Energy Optimizer",
    description=(
        "BUP CSE Fest 2026 Hackathon Preliminary — "
        "Interprets campus operator notes using an LLM, validates directives, "
        "and produces a cost-minimizing 24-hour energy schedule."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
from fastapi.encoders import jsonable_encoder

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors():
        clean_err = {
            "type": err.get("type"),
            "loc": [str(x) for x in err.get("loc", [])],
            "msg": str(err.get("msg")),
        }
        errors.append(clean_err)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "Invalid request schema", "details": errors},
    )



@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc)
    # Never expose raw stack traces in production
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error. Please check your request and try again."},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, status_code=200, tags=["Health"])
async def health():
    """Readiness check. Returns {status: ok} when the service is ready."""
    return HealthResponse(status="ok")


@app.post(
    "/optimize-energy",
    response_model=OptimizeResponse,
    status_code=200,
    tags=["Optimization"],
)
async def optimize_energy(request: OptimizeRequest):
    """
    Main endpoint: interprets operator notes with LLM, validates with guardrails,
    applies directives to optimization, and returns a valid cost-minimizing 24-hour schedule.
    """
    scenario_id = request.scenario_id
    logger.info("Received request for scenario_id=%s with %d notes", scenario_id, len(request.operator_notes))

    # Step 1: LLM Interpretation
    try:
        raw_directives = interpret_notes_with_llm(
            operator_notes=request.operator_notes,
            battery_capacity=request.battery.capacity_kwh,
        )
    except Exception as exc:
        logger.warning("LLM call or parsing failed for scenario %s: %s. Using safe no_op fallback.", scenario_id, exc)
        # Safe failure: create no_op fallback for all notes
        raw_directives = [
            {
                "note_index": i,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": f"LLM unavailable or malformed output; safe no-op fallback applied.",
            }
            for i in range(len(request.operator_notes))
        ]


    # Step 2: Deterministic Guardrails
    validated_directives = validate_all_directives(
        raw_list=raw_directives,
        note_count=len(request.operator_notes),
        battery_capacity=request.battery.capacity_kwh,
    )

    # Step 3: Optimization
    try:
        hourly_plan = optimize(
            hours_data=request.hours,
            battery=request.battery,
            directives=validated_directives,
        )
    except RuntimeError as exc:
        logger.error("Optimization failed for scenario %s: %s", scenario_id, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": f"Optimization error: {str(exc)}"},
        )

    # Step 4: Compute summary statistics
    tariffs = {h.hour: h.tariff_bdt_per_kwh for h in request.hours}
    total_grid, total_cost, peak_grid = compute_totals(hourly_plan, tariffs)

    # Step 5: Build plan summary
    applied = [d for d in validated_directives if d.applies]
    summary_parts = []
    if applied:
        summary_parts.append(
            f"Applied {len(applied)} operator directive(s): "
            + ", ".join(f"{d.directive_type.value}" for d in applied)
            + "."
        )
    summary_parts.append(
        f"Optimized 24-hour schedule: total grid import {total_grid:.1f} kWh at "
        f"BDT {total_cost:.2f}. Peak hourly grid draw: {peak_grid:.1f} kWh. "
        f"Battery charged during low-tariff hours and discharged during peak tariff hours to minimize cost."
    )
    plan_summary = " ".join(summary_parts)

    response = OptimizeResponse(
        scenario_id=scenario_id,
        directive_interpretation=validated_directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=plan_summary,
    )

    logger.info(
        "Scenario %s solved: total_cost=%.2f BDT, total_grid=%.1f kWh",
        scenario_id,
        total_cost,
        total_grid,
    )
    return response
