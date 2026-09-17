"""
Optimizer module.
Uses PuLP linear programming to minimize total grid electricity cost.
Applies validated directives deterministically before solving.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pulp

from app.models import (
    Battery,
    BatteryAction,
    DirectiveInterpretation,
    DirectiveType,
    HourEntry,
    HourlyPlanEntry,
)

logger = logging.getLogger(__name__)

TOLERANCE = 1e-6


@dataclass
class DirectiveSet:
    """Parsed, ready-to-apply directives for the optimizer."""
    solar_factors: Dict[int, float] = field(default_factory=dict)          # hour → factor
    min_reserve: Dict[int, float] = field(default_factory=dict)            # hour → min kWh
    no_charge_hours: set = field(default_factory=set)                      # set of hours
    no_discharge_hours: set = field(default_factory=set)                   # set of hours
    max_grid: Dict[int, float] = field(default_factory=dict)               # hour → max kWh


def _build_directive_set(
    directives: List[DirectiveInterpretation],
    battery_base_min: float,
) -> DirectiveSet:
    ds = DirectiveSet()
    for d in directives:
        if not d.applies or d.directive_type == DirectiveType.no_op:
            continue
        sa = d.structured_adjustment
        hours = sa["hours"]

        if d.directive_type == DirectiveType.solar_reduction:
            for h in hours:
                # If multiple solar_reduction directives for the same hour, take min factor
                ds.solar_factors[h] = min(ds.solar_factors.get(h, 1.0), sa["factor"])

        elif d.directive_type == DirectiveType.minimum_battery_reserve:
            reserve = max(sa["minimum_energy_kwh"], battery_base_min)
            for h in hours:
                ds.min_reserve[h] = max(ds.min_reserve.get(h, battery_base_min), reserve)

        elif d.directive_type == DirectiveType.no_charge_window:
            ds.no_charge_hours.update(hours)

        elif d.directive_type == DirectiveType.no_discharge_window:
            ds.no_discharge_hours.update(hours)

        elif d.directive_type == DirectiveType.max_grid_window:
            for h in hours:
                # If multiple cap directives, take the tightest
                ds.max_grid[h] = min(ds.max_grid.get(h, float("inf")), sa["max_grid_kwh"])

    return ds


def optimize(
    hours_data: List[HourEntry],
    battery: Battery,
    directives: List[DirectiveInterpretation],
) -> List[HourlyPlanEntry]:
    """
    Build and solve a linear program to minimize grid electricity cost.
    Returns a list of hourly plan entries.
    """
    # Sort hours by hour index
    hours_sorted = sorted(hours_data, key=lambda h: h.hour)
    n = 24

    ds = _build_directive_set(directives, battery.minimum_energy_kwh)

    # Compute effective solar per hour
    effective_solar = {}
    for h_entry in hours_sorted:
        h = h_entry.hour
        factor = ds.solar_factors.get(h, 1.0)
        effective_solar[h] = h_entry.solar_kwh * factor

    # -----------------------------------------------------------------------
    # LP Variables
    # -----------------------------------------------------------------------
    prob = pulp.LpProblem("GridWise_Cost_Minimization", pulp.LpMinimize)

    # Grid import per hour (kWh)
    grid = {h: pulp.LpVariable(f"grid_{h}", lowBound=0) for h in range(n)}

    # Solar used per hour (kWh)
    solar_used = {
        h: pulp.LpVariable(f"solar_used_{h}", lowBound=0, upBound=effective_solar[h])
        for h in range(n)
    }

    # Battery charge / discharge per hour (kWh, non-negative magnitudes)
    b_charge = {
        h: pulp.LpVariable(f"b_charge_{h}", lowBound=0, upBound=battery.max_charge_kwh_per_hour)
        for h in range(n)
    }
    b_discharge = {
        h: pulp.LpVariable(f"b_discharge_{h}", lowBound=0, upBound=battery.max_discharge_kwh_per_hour)
        for h in range(n)
    }

    # Binary: is_charging[h] = 1 if charging, 0 otherwise (prevents simultaneous charge/discharge)
    is_charging = {h: pulp.LpVariable(f"is_charging_{h}", cat="Binary") for h in range(n)}

    # Battery energy level AFTER each hour (kWh)
    batt_energy = {h: pulp.LpVariable(f"batt_energy_{h}", lowBound=0, upBound=battery.capacity_kwh) for h in range(n)}

    # -----------------------------------------------------------------------
    # Objective: Minimize total grid cost
    # -----------------------------------------------------------------------
    tariff = {h_entry.hour: h_entry.tariff_bdt_per_kwh for h_entry in hours_sorted}
    demand = {h_entry.hour: h_entry.demand_kwh for h_entry in hours_sorted}

    prob += pulp.lpSum(grid[h] * tariff[h] for h in range(n)), "Total_Grid_Cost"

    # -----------------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------------
    BIG_M = battery.max_charge_kwh_per_hour + battery.max_discharge_kwh_per_hour + 1000

    for h in range(n):
        # Energy balance: grid + solar + discharge = demand + charge
        prob += (
            grid[h] + solar_used[h] + b_discharge[h] == demand[h] + b_charge[h],
            f"energy_balance_{h}",
        )

        # Prevent simultaneous charging and discharging
        prob += b_charge[h] <= battery.max_charge_kwh_per_hour * is_charging[h], f"charge_flag_{h}"
        prob += b_discharge[h] <= battery.max_discharge_kwh_per_hour * (1 - is_charging[h]), f"discharge_flag_{h}"

        # Battery energy transition
        if h == 0:
            prev_energy = battery.initial_energy_kwh
        else:
            prev_energy = batt_energy[h - 1]

        prob += batt_energy[h] == prev_energy + b_charge[h] - b_discharge[h], f"battery_state_{h}"

        # Battery minimum reserve (base or directive-elevated)
        min_reserve = ds.min_reserve.get(h, battery.minimum_energy_kwh)
        prob += batt_energy[h] >= min_reserve, f"batt_min_{h}"

        # Battery capacity
        prob += batt_energy[h] <= battery.capacity_kwh, f"batt_max_{h}"

        # No charge window directive
        if h in ds.no_charge_hours:
            prob += b_charge[h] == 0, f"no_charge_{h}"

        # No discharge window directive
        if h in ds.no_discharge_hours:
            prob += b_discharge[h] == 0, f"no_discharge_{h}"

        # Max grid window directive
        if h in ds.max_grid:
            prob += grid[h] <= ds.max_grid[h], f"max_grid_{h}"

        # Grid must be non-negative (already set via lowBound=0)

    # End-of-day battery neutrality: final energy == initial energy
    prob += batt_energy[23] == battery.initial_energy_kwh, "end_of_day_neutrality"

    # -----------------------------------------------------------------------
    # Solve — compatible with PuLP 2.x and 3.x
    # PuLP 3.x removed PULP_CBC_CMD; use COIN_CMD instead (requires pulp[cbc])
    # -----------------------------------------------------------------------
    def _get_solver():
        """Return the best available CBC-compatible solver."""
        # PuLP 3.x
        if hasattr(pulp, "COIN_CMD"):
            try:
                s = pulp.COIN_CMD(msg=False, timeLimit=25)
                if s.available():
                    return s
            except Exception:
                pass
        # PuLP 2.x
        if hasattr(pulp, "PULP_CBC_CMD"):
            try:
                s = pulp.PULP_CBC_CMD(msg=False, timeLimit=25)
                if s.available():
                    return s
            except Exception:
                pass
        # Last resort: let PuLP auto-detect
        return None

    solver = _get_solver()
    if solver:
        status = prob.solve(solver)
    else:
        logger.warning("No CBC solver found, using PuLP default solver")
        status = prob.solve()

    if pulp.LpStatus[status] not in ("Optimal", "Feasible"):
        logger.warning(
            "LP solver status: %s — attempting relaxed fallback", pulp.LpStatus[status]
        )
        raise RuntimeError(f"Optimization failed: LP solver returned status '{pulp.LpStatus[status]}'")

    # -----------------------------------------------------------------------
    # Extract solution
    # -----------------------------------------------------------------------
    plan: List[HourlyPlanEntry] = []
    for h in range(n):
        g = max(0.0, pulp.value(grid[h]) or 0.0)
        s = max(0.0, pulp.value(solar_used[h]) or 0.0)
        # Clamp solar to effective available
        s = min(s, effective_solar[h])

        c_val = max(0.0, pulp.value(b_charge[h]) or 0.0)
        d_val = max(0.0, pulp.value(b_discharge[h]) or 0.0)
        e_after = max(0.0, pulp.value(batt_energy[h]) or 0.0)

        # Determine battery action
        if c_val > TOLERANCE and d_val <= TOLERANCE:
            action = BatteryAction.charge
            bkwh = round(c_val, 6)
        elif d_val > TOLERANCE and c_val <= TOLERANCE:
            action = BatteryAction.discharge
            bkwh = round(d_val, 6)
        else:
            action = BatteryAction.idle
            bkwh = 0.0

        plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=round(g, 6),
                solar_used_kwh=round(s, 6),
                battery_action=action,
                battery_kwh=bkwh,
                battery_energy_after_kwh=round(e_after, 6),
            )
        )

    return plan


def compute_totals(plan: List[HourlyPlanEntry], tariffs: Dict[int, float]):
    """Compute total_grid_kwh, total_cost_bdt, peak_grid_kwh from the hourly plan."""
    total_grid = sum(p.grid_kwh for p in plan)
    total_cost = sum(p.grid_kwh * tariffs[p.hour] for p in plan)
    peak_grid = max(p.grid_kwh for p in plan)
    return round(total_grid, 4), round(total_cost, 4), round(peak_grid, 4)
