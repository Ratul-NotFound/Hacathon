"""
Pydantic models for GridWise LLM-Assisted Energy Optimization API.
Covers request schema, response schema, and all intermediate types.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DirectiveType(str, Enum):
    solar_reduction = "solar_reduction"
    minimum_battery_reserve = "minimum_battery_reserve"
    no_charge_window = "no_charge_window"
    no_discharge_window = "no_discharge_window"
    max_grid_window = "max_grid_window"
    no_op = "no_op"


class BatteryAction(str, Enum):
    charge = "charge"
    discharge = "discharge"
    idle = "idle"


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class HourEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)


class Battery(BaseModel):
    capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)


class OptimizeRequest(BaseModel):
    scenario_id: str
    operator_notes: List[str] = Field(..., min_length=1, max_length=3)
    hours: List[HourEntry] = Field(..., min_length=24, max_length=24)
    battery: Battery

    model_config = {
        "json_schema_extra": {
            "example": {
                "scenario_id": "SAMPLE-01",
                "operator_notes": [
                    "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
                    "The sports office moved next month's registration deadline."
                ],
                "battery": {
                    "capacity_kwh": 220,
                    "initial_energy_kwh": 110,
                    "minimum_energy_kwh": 40,
                    "max_charge_kwh_per_hour": 50,
                    "max_discharge_kwh_per_hour": 50
                },
                "hours": [
                    {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
                    {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
                    {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
                    {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
                    {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
                    {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
                    {"hour": 6, "demand_kwh": 110, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
                    {"hour": 7, "demand_kwh": 130, "solar_kwh": 20, "tariff_bdt_per_kwh": 10},
                    {"hour": 8, "demand_kwh": 150, "solar_kwh": 50, "tariff_bdt_per_kwh": 12},
                    {"hour": 9, "demand_kwh": 170, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
                    {"hour": 10, "demand_kwh": 180, "solar_kwh": 130, "tariff_bdt_per_kwh": 15},
                    {"hour": 11, "demand_kwh": 190, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
                    {"hour": 12, "demand_kwh": 195, "solar_kwh": 180, "tariff_bdt_per_kwh": 16},
                    {"hour": 13, "demand_kwh": 195, "solar_kwh": 170, "tariff_bdt_per_kwh": 16},
                    {"hour": 14, "demand_kwh": 190, "solar_kwh": 140, "tariff_bdt_per_kwh": 16},
                    {"hour": 15, "demand_kwh": 180, "solar_kwh": 100, "tariff_bdt_per_kwh": 16},
                    {"hour": 16, "demand_kwh": 170, "solar_kwh": 50, "tariff_bdt_per_kwh": 15},
                    {"hour": 17, "demand_kwh": 180, "solar_kwh": 15, "tariff_bdt_per_kwh": 20},
                    {"hour": 18, "demand_kwh": 200, "solar_kwh": 0, "tariff_bdt_per_kwh": 24},
                    {"hour": 19, "demand_kwh": 220, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
                    {"hour": 20, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
                    {"hour": 21, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
                    {"hour": 22, "demand_kwh": 135, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
                    {"hour": 23, "demand_kwh": 105, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
                ]
            }
        }
    }

    @model_validator(mode="after")
    def validate_hours(self) -> "OptimizeRequest":
        hour_values = [h.hour for h in self.hours]
        if sorted(hour_values) != list(range(24)):
            raise ValueError("hours must contain exactly one entry for each hour 0 through 23")
        if len(self.operator_notes) == 0:
            raise ValueError("operator_notes must have at least 1 note")
        for note in self.operator_notes:
            if not note.strip():
                raise ValueError("All operator_notes must be non-empty strings")
        return self



# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------

class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[Any] = None
    explanation: str


class HourlyPlanEntry(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: BatteryAction
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(BaseModel):
    status: str = "ok"
