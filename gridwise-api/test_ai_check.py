import requests
import json

with open("d:/Projects/Hacathon/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json", "r") as f:
    cases = json.load(f)["cases"]

for case in cases[:3]:
    print("=" * 70)
    print(f"CASE: {case['id']} - {case.get('label')}")
    print("NOTES:")
    for note in case["input"]["operator_notes"]:
        print(f"  * {note}")
    
    res = requests.post("http://localhost:8000/optimize-energy", json=case["input"])
    if res.status_code == 200:
        data = res.json()
        print("\nAI DIRECTIVE INTERPRETATION:")
        print(json.dumps(data["directive_interpretation"], indent=2))
        print(f"\nPLAN SUMMARY: {data['plan_summary']}")
        print(f"TOTAL COST:   {data['total_cost_bdt']} BDT")
    else:
        print(f"ERROR: {res.text}")
    print()
