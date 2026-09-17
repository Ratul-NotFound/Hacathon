import requests
import json

# Load Case 1 directly from the official sample cases file
with open("d:/Projects/Hacathon/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json", "r") as f:
    cases = json.load(f)["cases"]

case_0 = cases[0]

print("=" * 60)
print(f"TESTING CASE: {case_0['id']} - {case_0.get('label')}")
print("=" * 60)
print("OPERATOR NOTES SENT TO AI:")
for i, note in enumerate(case_0["input"]["operator_notes"]):
    print(f"  [{i}] {note}")
print("=" * 60)

res = requests.post("http://localhost:8000/optimize-energy", json=case_0["input"])

print("\nHTTP STATUS:", res.status_code)
if res.status_code == 200:
    data = res.json()
    print("\n--- 1. AI DIRECTIVE INTERPRETATION ---")
    print(json.dumps(data["directive_interpretation"], indent=2))
    
    print("\n--- 2. PLAN SUMMARY ---")
    print(data["plan_summary"])
    
    print("\n--- 3. OVERALL METRICS ---")
    print(f"  Total Cost: {data['total_cost_bdt']} BDT")
    print(f"  Total Grid: {data['total_grid_kwh']} kWh")
    print(f"  Peak Grid:  {data['peak_grid_kwh']} kWh")

    print("\n--- 4. HOURLY PLAN (HOURS 10 to 16) ---")
    print(f"{'Hour':<6}{'Grid(kWh)':<12}{'SolarUsed(kWh)':<16}{'Action':<12}{'Batt(kWh)':<12}{'SoC After(kWh)'}")
    print("-" * 65)
    for h in data["hourly_plan"]:
        if 10 <= h["hour"] <= 16:
            print(f"{h['hour']:<6}{h['grid_kwh']:<12.1f}{h['solar_used_kwh']:<16.1f}{h['battery_action']:<12}{h['battery_kwh']:<12.1f}{h['battery_energy_after_kwh']:.1f}")

else:
    print("ERROR:", res.text)
