import sys
import json
import requests

CASES_FILE = "d:/Projects/Hacathon/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
BASE_URL = "http://localhost:8000"

def run_case_test(case_idx: int):
    with open(CASES_FILE, "r") as f:
        cases = json.load(f)["cases"]

    if case_idx < 0 or case_idx >= len(cases):
        print(f"Error: case index must be between 1 and {len(cases)}")
        return

    case = cases[case_idx]
    inp = case["input"]
    ref_out = case.get("expected_output", {})

    print("=" * 75)
    print(f"TESTING {case['id']}: {case.get('label', '')}")
    print("=" * 75)
    print("OPERATOR NOTES SENT TO AI:")
    for i, note in enumerate(inp["operator_notes"]):
        print(f"  [{i}] {note}")
    print("-" * 75)

    try:
        res = requests.post(f"{BASE_URL}/optimize-energy", json=inp, timeout=30)
    except Exception as e:
        print(f"[FAIL] Request failed: {e}")
        return

    print(f"HTTP STATUS: {res.status_code}")
    if res.status_code != 200:
        print(f"ERROR RESPONSE: {res.text}")
        return

    data = res.json()

    # 1. Directives
    print("\n--- 1. AI DIRECTIVE INTERPRETATION ---")
    print(json.dumps(data["directive_interpretation"], indent=2))

    # Compare with reference directives
    ref_dirs = ref_out.get("directive_interpretation", [])
    act_dirs = data.get("directive_interpretation", [])
    match = True
    if len(ref_dirs) == len(act_dirs):
        for rd, ad in zip(ref_dirs, act_dirs):
            if (rd.get("directive_type") != ad.get("directive_type") or 
                rd.get("applies") != ad.get("applies") or
                rd.get("structured_adjustment") != ad.get("structured_adjustment")):
                match = False
                break
    else:
        match = False
    print(f">> Directive Ground Truth Match: {'[EXACT MATCH]' if match else '[MISMATCH]'}")

    # 2. Plan Summary
    print("\n--- 2. PLAN SUMMARY ---")
    print(data.get("plan_summary", ""))

    # 3. Cost & Energy Comparison
    act_cost = data.get("total_cost_bdt", 0.0)
    ref_cost = ref_out.get("total_cost_bdt", 0.0)
    act_grid = data.get("total_grid_kwh", 0.0)
    ref_grid = ref_out.get("total_grid_kwh", 0.0)
    act_peak = data.get("peak_grid_kwh", 0.0)
    ref_peak = ref_out.get("peak_grid_kwh", 0.0)

    print("\n--- 3. METRICS COMPARISON WITH GROUND TRUTH ---")
    print(f"{'Metric':<25} | {'API Output':<15} | {'Reference':<15} | {'Difference'}")
    print("-" * 70)
    print(f"{'Total Cost (BDT)':<25} | {act_cost:<15.2f} | {ref_cost:<15.2f} | {act_cost - ref_cost:+.2f}")
    print(f"{'Total Grid Import (kWh)':<25} | {act_grid:<15.2f} | {ref_grid:<15.2f} | {act_grid - ref_grid:+.2f}")
    print(f"{'Peak Hourly Grid (kWh)':<25} | {act_peak:<15.2f} | {ref_peak:<15.2f} | {act_peak - ref_peak:+.2f}")

    # 4. Independent Constraint Verification
    battery = inp["battery"]
    hours = inp["hours"]
    cap = battery["capacity_kwh"]
    init_e = battery["initial_energy_kwh"]
    base_min = battery["minimum_energy_kwh"]
    max_c = battery["max_charge_kwh_per_hour"]
    max_d = battery["max_discharge_kwh_per_hour"]

    violations = []
    cur_e = init_e
    plan = data.get("hourly_plan", [])

    for row in plan:
        h = row["hour"]
        g = row["grid_kwh"]
        su = row["solar_used_kwh"]
        act = row["battery_action"]
        bkwh = row["battery_kwh"]
        e_after = row["battery_energy_after_kwh"]

        c_val = bkwh if act == "charge" else 0.0
        d_val = bkwh if act == "discharge" else 0.0

        # Energy balance
        lhs = g + su + d_val
        rhs = hours[h]["demand_kwh"] + c_val
        if abs(lhs - rhs) > 0.01:
            violations.append(f"Hour {h}: Balance LHS={lhs:.2f} != RHS={rhs:.2f}")

        # Battery transition
        exp_e = cur_e + c_val - d_val
        if abs(e_after - exp_e) > 0.01:
            violations.append(f"Hour {h}: SoC={e_after:.2f} != expected {exp_e:.2f}")

        # Bounds
        if e_after > cap + 0.01:
            violations.append(f"Hour {h}: SoC={e_after:.2f} > capacity {cap:.2f}")
        if e_after < base_min - 0.01:
            violations.append(f"Hour {h}: SoC={e_after:.2f} < base min {base_min:.2f}")

        # Limits
        if c_val > max_c + 0.01:
            violations.append(f"Hour {h}: Charge={c_val:.2f} > max charge {max_c:.2f}")
        if d_val > max_d + 0.01:
            violations.append(f"Hour {h}: Discharge={d_val:.2f} > max discharge {max_d:.2f}")

        cur_e = e_after

    if abs(cur_e - init_e) > 0.01:
        violations.append(f"End-of-day neutrality: final SoC={cur_e:.2f} != initial {init_e:.2f}")

    print("\n--- 4. INDEPENDENT PHYSICAL CONSTRAINT CHECK ---")
    if not violations:
        print("[PASS] All 24 hours strictly satisfy Energy Balance, Bounds, Rate Limits, and Neutrality (0 violations)!")
    else:
        print(f"[FAIL] Found {len(violations)} constraint violations:")
        for v in violations:
            print(f"  * {v}")

    # 5. Hourly Schedule
    print("\n--- 5. HOURLY SCHEDULE (HOURS 10 TO 16) ---")
    print(f"{'Hour':<6}{'Grid(kWh)':<12}{'SolarUsed(kWh)':<16}{'Action':<12}{'Batt(kWh)':<12}{'SoC After(kWh)'}")
    print("-" * 65)
    for h in plan:
        if 10 <= h["hour"] <= 16:
            print(f"{h['hour']:<6}{h['grid_kwh']:<12.1f}{h['solar_used_kwh']:<16.1f}{h['battery_action']:<12}{h['battery_kwh']:<12.1f}{h['battery_energy_after_kwh']:.1f}")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "1"
    if arg.lower() == "all":
        for i in range(10):
            run_case_test(i)
    else:
        try:
            c_num = int(arg) - 1
            run_case_test(c_num)
        except ValueError:
            print("Usage: python test_manual.py [case_number 1-10 | 'all']")
