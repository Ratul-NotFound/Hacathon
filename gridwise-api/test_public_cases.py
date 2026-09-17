"""
Test script: Runs all 10 public sample cases against the local GridWise API.
Usage:
  python test_public_cases.py [--url http://localhost:8000]
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

TOLERANCE = 0.01


def check_response(case: dict, resp_json: dict) -> list[str]:
    """Check the response against expected output. Returns list of issues found."""
    issues = []
    expected = case.get("expected_output", {})

    # Check scenario_id
    if resp_json.get("scenario_id") != case["input"]["scenario_id"]:
        issues.append(f"scenario_id mismatch: got {resp_json.get('scenario_id')!r}")

    # Check directive interpretations
    exp_di = expected.get("directive_interpretation", [])
    got_di = resp_json.get("directive_interpretation", [])
    if len(got_di) != len(exp_di):
        issues.append(f"directive_interpretation length: expected {len(exp_di)}, got {len(got_di)}")
    else:
        for i, (exp, got) in enumerate(zip(exp_di, got_di)):
            if got.get("note_index") != exp.get("note_index"):
                issues.append(f"  DI[{i}] note_index: expected {exp.get('note_index')}, got {got.get('note_index')}")
            if got.get("applies") != exp.get("applies"):
                issues.append(f"  DI[{i}] applies: expected {exp.get('applies')}, got {got.get('applies')}")
            if got.get("directive_type") != exp.get("directive_type"):
                issues.append(f"  DI[{i}] directive_type: expected {exp.get('directive_type')!r}, got {got.get('directive_type')!r}")
            # Check structured_adjustment shape
            exp_sa = exp.get("structured_adjustment")
            got_sa = got.get("structured_adjustment")
            if exp_sa is None and got_sa is not None:
                issues.append(f"  DI[{i}] structured_adjustment should be null")
            elif exp_sa is not None and got_sa is None:
                issues.append(f"  DI[{i}] structured_adjustment should not be null")
            elif exp_sa is not None and got_sa is not None:
                if "hours" in exp_sa:
                    if sorted(got_sa.get("hours", [])) != sorted(exp_sa["hours"]):
                        issues.append(f"  DI[{i}] hours: expected {exp_sa['hours']}, got {got_sa.get('hours')}")
                if "factor" in exp_sa:
                    if abs((got_sa.get("factor") or 0) - exp_sa["factor"]) > TOLERANCE:
                        issues.append(f"  DI[{i}] factor: expected {exp_sa['factor']}, got {got_sa.get('factor')}")
                if "minimum_energy_kwh" in exp_sa:
                    if abs((got_sa.get("minimum_energy_kwh") or 0) - exp_sa["minimum_energy_kwh"]) > TOLERANCE:
                        issues.append(f"  DI[{i}] minimum_energy_kwh: expected {exp_sa['minimum_energy_kwh']}")
                if "max_grid_kwh" in exp_sa:
                    if abs((got_sa.get("max_grid_kwh") or 0) - exp_sa["max_grid_kwh"]) > TOLERANCE:
                        issues.append(f"  DI[{i}] max_grid_kwh: expected {exp_sa['max_grid_kwh']}")

    # Check hourly_plan length
    plan = resp_json.get("hourly_plan", [])
    if len(plan) != 24:
        issues.append(f"hourly_plan has {len(plan)} entries (expected 24)")

    # Check totals recalculation
    battery = case["input"]["battery"]
    hours_data = {h["hour"]: h for h in case["input"]["hours"]}
    if len(plan) == 24:
        recalc_grid = sum(e["grid_kwh"] for e in plan)
        recalc_cost = sum(e["grid_kwh"] * hours_data[e["hour"]]["tariff_bdt_per_kwh"] for e in plan)
        recalc_peak = max(e["grid_kwh"] for e in plan)

        if abs(recalc_grid - resp_json.get("total_grid_kwh", 0)) > TOLERANCE:
            issues.append(f"total_grid_kwh mismatch: recalc={recalc_grid:.3f}, reported={resp_json.get('total_grid_kwh')}")
        if abs(recalc_cost - resp_json.get("total_cost_bdt", 0)) > TOLERANCE:
            issues.append(f"total_cost_bdt mismatch: recalc={recalc_cost:.3f}, reported={resp_json.get('total_cost_bdt')}")

        # Check energy balance per hour
        initial_energy = battery["initial_energy_kwh"]
        e_before = initial_energy
        for entry in sorted(plan, key=lambda x: x["hour"]):
            h = entry["hour"]
            h_data = hours_data[h]
            g = entry["grid_kwh"]
            s = entry["solar_used_kwh"]
            bkwh = entry["battery_kwh"]
            action = entry["battery_action"]
            e_after = entry["battery_energy_after_kwh"]

            bc = bkwh if action == "charge" else 0.0
            bd = bkwh if action == "discharge" else 0.0

            # Energy balance check
            lhs = g + s + bd
            rhs = h_data["demand_kwh"] + bc
            if abs(lhs - rhs) > TOLERANCE:
                issues.append(f"  Energy balance failed at hour {h}: {lhs:.3f} != {rhs:.3f}")

            # Battery state check
            expected_after = e_before + bc - bd
            if abs(expected_after - e_after) > TOLERANCE:
                issues.append(f"  Battery state failed at hour {h}: expected {expected_after:.3f}, got {e_after:.3f}")

            e_before = e_after

        # End-of-day neutrality
        if abs(e_before - initial_energy) > TOLERANCE:
            issues.append(f"End-of-day battery energy {e_before:.3f} != initial {initial_energy:.3f}")

    return issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument(
        "--cases-file",
        default=str(
            Path(__file__).parent.parent / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
        ),
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    print(f"\n{'='*70}")
    print("GridWise Public Sample Case Test Runner")
    print(f"{'='*70}")
    print(f"Base URL: {base_url}")

    # Health check
    print("\n[1/2] Health check...")
    try:
        r = requests.get(f"{base_url}/health", timeout=10)
        if r.status_code == 200 and r.json().get("status") == "ok":
            print("  [OK] /health OK")
        else:
            print(f"  [FAIL] /health returned {r.status_code}: {r.text}")
            sys.exit(1)
    except Exception as e:
        print(f"  [FAIL] /health failed: {e}")
        sys.exit(1)

    # Load cases
    print(f"\n[2/2] Loading sample cases from {args.cases_file}...")
    with open(args.cases_file) as f:
        data = json.load(f)
    cases = data["cases"]
    print(f"  Found {len(cases)} cases\n")

    passed = 0
    failed = 0

    for case in cases:
        case_id = case["id"]
        label = case.get("label", "")
        print(f"--- {case_id}: {label}")

        start = time.time()
        try:
            r = requests.post(
                f"{base_url}/optimize-energy",
                json=case["input"],
                timeout=35,
            )
            elapsed = time.time() - start

            if r.status_code != 200:
                print(f"  [FAIL] HTTP {r.status_code} in {elapsed:.1f}s: {r.text[:200]}")
                failed += 1
                continue

            resp_json = r.json()
            issues = check_response(case, resp_json)

            if issues:
                print(f"  [WARN] Passed HTTP in {elapsed:.1f}s, but found issues:")
                for issue in issues:
                    print(f"     * {issue}")
                failed += 1
            else:
                print(f"  [PASS] ({elapsed:.1f}s) | Cost: {resp_json.get('total_cost_bdt', '?')} BDT")
                passed += 1

        except Exception as e:
            elapsed = time.time() - start
            print(f"  [FAIL] Exception after {elapsed:.1f}s: {e}")
            failed += 1

    print(f"\n{'='*70}")
    print(f"Results: {passed}/{len(cases)} passed, {failed}/{len(cases)} failed")
    print(f"{'='*70}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
