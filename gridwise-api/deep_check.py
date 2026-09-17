"""
Deep verification script comparing GridWise API responses with official reference sample cases.
"""
import json
import math
import sys
import time
import requests

CASES_PATH = "d:/Projects/Hacathon/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
BASE_URL = "http://localhost:8000"

def run_deep_check():
    with open(CASES_PATH, "r") as f:
        data = json.load(f)
    cases = data["cases"]

    print("=" * 80)
    print("GRIDWISE DEEP VERIFICATION REPORT — COMPARED WITH OFFICIAL REFERENCE CASES")
    print("=" * 80)

    all_passed = True
    summary_rows = []

    for idx, case in enumerate(cases):
        case_id = case["id"]
        label = case.get("label", "")
        exp_out = case["expected_output"]
        
        print(f"\n[{idx+1}/10] Testing {case_id}: {label}")
        print("-" * 75)

        t0 = time.time()
        try:
            res = requests.post(f"{BASE_URL}/optimize-energy", json=case["input"], timeout=30)
            elapsed = time.time() - t0
        except Exception as e:
            print(f"  [CRITICAL] Request failed with exception: {e}")
            all_passed = False
            continue

        if res.status_code != 200:
            print(f"  [CRITICAL] HTTP {res.status_code}: {res.text}")
            all_passed = False
            continue

        actual = res.json()

        # 1. Compare Directives
        exp_dirs = exp_out.get("directive_interpretation", [])
        act_dirs = actual.get("directive_interpretation", [])

        dir_match = True
        dir_details = []

        if len(exp_dirs) != len(act_dirs):
            dir_match = False
            dir_details.append(f"Directive count mismatch: expected {len(exp_dirs)}, got {len(act_dirs)}")
        else:
            for i, (ed, ad) in enumerate(zip(exp_dirs, act_dirs)):
                # Type
                if ed.get("directive_type") != ad.get("directive_type"):
                    dir_match = False
                    dir_details.append(f"Note {i} type: exp '{ed.get('directive_type')}' != got '{ad.get('directive_type')}'")
                # Applies
                if ed.get("applies") != ad.get("applies"):
                    dir_match = False
                    dir_details.append(f"Note {i} applies: exp {ed.get('applies')} != got {ad.get('applies')}")
                # Structured adjustment
                esa = ed.get("structured_adjustment")
                asa = ad.get("structured_adjustment")
                if esa is None and asa is not None:
                    dir_match = False
                    dir_details.append(f"Note {i} structured_adjustment: exp null != got {asa}")
                elif esa is not None and asa is None:
                    dir_match = False
                    dir_details.append(f"Note {i} structured_adjustment: exp {esa} != got null")
                elif esa and asa:
                    # check hours
                    if esa.get("hours") != asa.get("hours"):
                        dir_match = False
                        dir_details.append(f"Note {i} hours: exp {esa.get('hours')} != got {asa.get('hours')}")
                    # check factor
                    if "factor" in esa:
                        if abs(esa.get("factor", 0) - asa.get("factor", 0)) > 1e-4:
                            dir_match = False
                            dir_details.append(f"Note {i} factor: exp {esa.get('factor')} != got {asa.get('factor')}")
                    # check min energy
                    if "minimum_energy_kwh" in esa:
                        if abs(esa.get("minimum_energy_kwh", 0) - asa.get("minimum_energy_kwh", 0)) > 1e-3:
                            dir_match = False
                            dir_details.append(f"Note {i} min_energy: exp {esa.get('minimum_energy_kwh')} != got {asa.get('minimum_energy_kwh')}")
                    # check max grid
                    if "max_grid_kwh" in esa:
                        if abs(esa.get("max_grid_kwh", 0) - asa.get("max_grid_kwh", 0)) > 1e-3:
                            dir_match = False
                            dir_details.append(f"Note {i} max_grid: exp {esa.get('max_grid_kwh')} != got {asa.get('max_grid_kwh')}")

        # 2. Compare Cost and Grid Metrics
        exp_cost = exp_out.get("total_cost_bdt", 0.0)
        act_cost = actual.get("total_cost_bdt", 0.0)
        cost_diff = act_cost - exp_cost

        exp_grid = exp_out.get("total_grid_kwh", 0.0)
        act_grid = actual.get("total_grid_kwh", 0.0)
        grid_diff = act_grid - exp_grid

        exp_peak = exp_out.get("peak_grid_kwh", 0.0)
        act_peak = actual.get("peak_grid_kwh", 0.0)
        peak_diff = act_peak - exp_peak

        cost_match = abs(cost_diff) <= 0.05
        grid_match = abs(grid_diff) <= 0.05

        # 3. Constraint checks on hourly plan
        plan = actual.get("hourly_plan", [])
        battery = case["input"]["battery"]
        hours = case["input"]["hours"]
        eff = battery.get("round_trip_efficiency", 0.90)
        eta_c = math.sqrt(eff)
        eta_d = math.sqrt(eff)
        cap = battery["capacity_kwh"]
        init_e = battery["initial_energy_kwh"]
        base_min_e = battery["minimum_energy_kwh"]
        max_c = battery["max_charge_kwh_per_hour"]
        max_d = battery["max_discharge_kwh_per_hour"]

        # Build hourly adjustments
        solar_factors = [1.0] * 24
        no_charge_h = set()
        no_discharge_h = set()
        hourly_min_e = [base_min_e] * 24
        hourly_max_g = [float("inf")] * 24

        for d in act_dirs:
            if not d.get("applies") or not d.get("structured_adjustment"):
                continue
            dtype = d.get("directive_type")
            sa = d.get("structured_adjustment")
            d_hours = sa.get("hours", [])
            if dtype == "solar_reduction":
                f_val = sa.get("factor", 1.0)
                for h in d_hours:
                    solar_factors[h] = min(solar_factors[h], f_val)
            elif dtype == "no_charge_window":
                no_charge_h.update(d_hours)
            elif dtype == "no_discharge_window":
                no_discharge_h.update(d_hours)
            elif dtype == "minimum_battery_reserve":
                m_val = sa.get("minimum_energy_kwh", base_min_e)
                for h in d_hours:
                    hourly_min_e[h] = max(hourly_min_e[h], m_val)
            elif dtype == "max_grid_window":
                g_val = sa.get("max_grid_kwh", float("inf"))
                for h in d_hours:
                    hourly_max_g[h] = min(hourly_max_g[h], g_val)

        constraint_violations = []
        cur_e = init_e
        calc_cost = 0.0
        calc_grid = 0.0
        calc_peak = 0.0

        for row in plan:
            h = row["hour"]
            g = row["grid_kwh"]
            su = row["solar_used_kwh"]
            action = row["battery_action"]
            bkwh = row["battery_kwh"]
            e_after = row["battery_energy_after_kwh"]
            inp_h = hours[h]
            demand = inp_h["demand_kwh"]
            solar_raw = inp_h["solar_kwh"]
            tariff = inp_h["tariff_bdt_per_kwh"]
            solar_avail = solar_raw * solar_factors[h]

            charge_val = bkwh if action == "charge" else 0.0
            discharge_val = bkwh if action == "discharge" else 0.0

            # Energy balance: Grid + SolarUsed + Discharge = Demand + Charge
            lhs = g + su + discharge_val
            rhs = demand + charge_val
            if abs(lhs - rhs) > 1e-2:
                constraint_violations.append(f"Hour {h}: Energy balance LHS={lhs:.3f} != RHS={rhs:.3f}")

            # Solar usability
            if su > solar_avail + 1e-3:
                constraint_violations.append(f"Hour {h}: Solar used {su:.3f} > available {solar_avail:.3f}")

            # Battery state transition
            expected_e_after = cur_e + charge_val - discharge_val
            if abs(e_after - expected_e_after) > 1e-2:
                constraint_violations.append(f"Hour {h}: Battery SoC {e_after:.3f} != expected {expected_e_after:.3f}")


            # Battery bounds
            if e_after < hourly_min_e[h] - 1e-3:
                constraint_violations.append(f"Hour {h}: Battery SoC {e_after:.3f} < min allowed {hourly_min_e[h]:.3f}")
            if e_after > cap + 1e-3:
                constraint_violations.append(f"Hour {h}: Battery SoC {e_after:.3f} > capacity {cap:.3f}")

            # Rate limits
            if charge_val > max_c + 1e-3:
                constraint_violations.append(f"Hour {h}: Charge {charge_val:.3f} > max charge {max_c:.3f}")
            if discharge_val > max_d + 1e-3:
                constraint_violations.append(f"Hour {h}: Discharge {discharge_val:.3f} > max discharge {max_d:.3f}")

            # Windows
            if h in no_charge_h and charge_val > 1e-3:
                constraint_violations.append(f"Hour {h}: Charged in no_charge_window")
            if h in no_discharge_h and discharge_val > 1e-3:
                constraint_violations.append(f"Hour {h}: Discharged in no_discharge_window")
            if g > hourly_max_g[h] + 1e-3:
                constraint_violations.append(f"Hour {h}: Grid {g:.3f} > max_grid {hourly_max_g[h]:.3f}")

            calc_cost += g * tariff
            calc_grid += g
            if g > calc_peak:
                calc_peak = g
            cur_e = e_after

        # End of day neutrality
        if abs(cur_e - init_e) > 1e-2:
            constraint_violations.append(f"End of day SoC {cur_e:.3f} != initial {init_e:.3f}")

        # Recalculated match
        if abs(calc_cost - act_cost) > 1e-2:
            constraint_violations.append(f"Total cost mismatch: plan sum {calc_cost:.2f} != reported {act_cost:.2f}")

        # Print results for this case
        dir_status = "EXACT MATCH" if dir_match else "MISMATCH"
        cost_status = f"{act_cost:.2f} BDT (Ref: {exp_cost:.2f} BDT | Diff: {cost_diff:+.2f})"
        grid_status = f"{act_grid:.1f} kWh (Ref: {exp_grid:.1f} kWh | Diff: {grid_diff:+.1f})"
        cons_status = "0 VIOLATIONS" if not constraint_violations else f"{len(constraint_violations)} VIOLATIONS"

        print(f"  * Directive Interpretation : [{dir_status}]")
        if not dir_match:
            for d in dir_details:
                print(f"      - {d}")
        print(f"  * Total Cost               : {cost_status}")
        print(f"  * Total Grid Energy        : {grid_status}")
        print(f"  * Physical Constraints     : [{cons_status}]")
        if constraint_violations:
            for cv in constraint_violations[:3]:
                print(f"      - {cv}")
        print(f"  * Time Elapsed             : {elapsed:.2f}s")

        passed_case = dir_match and cost_match and not constraint_violations
        summary_rows.append({
            "id": case_id,
            "label": label,
            "dir_status": dir_status,
            "act_cost": act_cost,
            "exp_cost": exp_cost,
            "cost_diff": cost_diff,
            "act_grid": act_grid,
            "exp_grid": exp_grid,
            "cons_status": cons_status,
            "passed": passed_case,
            "time": elapsed
        })
        if not passed_case:
            all_passed = False

    print("\n" + "=" * 80)
    print("COMPREHENSIVE BENCHMARK SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Case ID':<11} | {'Directives':<12} | {'Got Cost (BDT)':<15} | {'Ref Cost (BDT)':<15} | {'Diff':<8} | {'Constraints':<12} | {'Result'}")
    print("-" * 88)
    for r in summary_rows:
        res_str = "[PASS]" if r["passed"] else "[FAIL]"
        print(f"{r['id']:<11} | {r['dir_status']:<12} | {r['act_cost']:<15.2f} | {r['exp_cost']:<15.2f} | {r['cost_diff']:<+8.2f} | {r['cons_status']:<12} | {res_str}")
    print("=" * 88)

    passed_count = sum(1 for r in summary_rows if r["passed"])
    print(f"\nFinal Result: {passed_count}/{len(summary_rows)} Cases 100% Passed and Verified against Reference Ground Truth.")
    if all_passed:
        print("PERFECT: All directive interpretations, energy balances, constraints, and cost minimizations match exactly!\n")
    else:
        print("ATTENTION: Some differences detected. Please review above.\n")

if __name__ == "__main__":
    run_deep_check()
