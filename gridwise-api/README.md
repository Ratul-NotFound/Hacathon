# GridWise — LLM-Assisted Campus Energy Optimizer
**BUP CSE Fest 2026 Hackathon · Online Preliminary**

An HTTP API service that interprets natural-language campus operator notes using Google Gemini, validates them with deterministic guardrails, and produces a cost-minimizing 24-hour energy schedule using Linear Programming (PuLP/CBC).

---

## Architecture

```
Operator Notes (natural language)
         │
         ▼
  ┌──────────────┐
  │  Google      │  LLM call (Gemini 2.0 Flash)
  │  Gemini LLM  │  → Structured JSON directives
  └──────────────┘
         │
         ▼
  ┌──────────────┐
  │  Guardrails  │  Deterministic validation:
  │  (guardrails │  – allowed directive types
  │   .py)       │  – hours 0-23, sorted, unique
  └──────────────┘  – numeric range checks
         │          – safe fallback to no_op
         ▼
  ┌──────────────┐
  │  Optimizer   │  PuLP Linear Program (CBC solver)
  │  (optimizer  │  – applies directive constraints
  │   .py)       │  – minimizes total grid cost BDT
  └──────────────┘  – satisfies all GridWise rules
         │
         ▼
  JSON Response (directive_interpretation + hourly_plan)
```

### Key design decisions
| Decision | Rationale |
|---|---|
| **Gemini 2.0 Flash** | Fast (p95 < 5s target), capable, generous free tier |
| **PuLP + CBC** | Exact LP solver, open-source, no external service |
| **Binary charge/discharge mutex** | Prevents simultaneous charge+discharge |
| **End-of-day neutrality as LP constraint** | `batt_energy[23] == initial_energy` |
| **Safe fallback** | Any guardrail failure → `no_op`; service never crashes |
| **Temperature 0.0** | Deterministic LLM output for consistent directive extraction |

---

## LLM Role

Google Gemini (`gemini-2.0-flash`) is used **exclusively for the operator note interpretation path**. It receives the operator notes and returns a structured JSON array of directives. This output is then deterministically validated by the guardrails before being applied to the optimizer. The LLM is **not** used only for `plan_summary` or documentation.

### Prompt Strategy
- Zero-temperature for deterministic output
- Precise time-window semantics (start-inclusive, end-exclusive)
- Explicit `factor` semantics (remaining fraction, not reduction fraction)
- Clear `no_op` definition for irrelevant notes

---

## Guardrails

All LLM output is treated as **untrusted** until deterministic validation passes:
- `directive_type` must be one of the 6 supported types
- `note_index` must match an existing note, no duplicates
- `hours` must be unique integers 0–23 in ascending order
- `factor` (solar_reduction) must be in [0, 1]
- `minimum_energy_kwh` must be non-negative and ≤ battery capacity
- `max_grid_kwh` must be non-negative
- `no_op` must have `applies=false` and `structured_adjustment=null`
- Any invalid directive falls back to `no_op` (safe failure)

---

## Optimizer / Solver

**Library**: [PuLP](https://coin-or.github.io/pulp/) with the bundled **CBC** solver.  
**Method**: Linear Programming (LP)  
**Objective**: Minimize `SUM(grid_kwh[h] * tariff[h]) for h = 0..23`

**Hard constraints**:
- Energy balance every hour
- Battery capacity and minimum reserve bounds
- Hourly charge/discharge rate limits
- Charge/discharge mutual exclusion (binary variable)
- End-of-day battery energy = initial battery energy
- All operator directive constraints (solar, reserve, charge/discharge windows, grid cap)

---

## Local Quickstart (Clean Environment)

### Prerequisites
- Python 3.11+
- A **Google Gemini API key** ([Get one free at Google AI Studio](https://aistudio.google.com/))

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/<your-org>/gridwise-api.git
cd gridwise-api

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set environment variables (never commit actual keys)
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=<your key>
# Or export directly:
export GEMINI_API_KEY=<your-gemini-api-key>    # Windows: set GEMINI_API_KEY=...

# 5. Start the service
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Verify health
```bash
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

### Run a sample request
```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "GRID-101",
    "operator_notes": [
      "Solar output will drop to about 20% from 1 PM to 3 PM.",
      "Do not charge the battery between 2 PM and 4 PM.",
      "The cafeteria menu changes tomorrow."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7},
      {"hour": 1, "demand_kwh": 170, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 160, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 3, "demand_kwh": 155, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 4, "demand_kwh": 155, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 5, "demand_kwh": 160, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 180, "solar_kwh": 10, "tariff_bdt_per_kwh": 8},
      {"hour": 7, "demand_kwh": 210, "solar_kwh": 40, "tariff_bdt_per_kwh": 10},
      {"hour": 8, "demand_kwh": 240, "solar_kwh": 90, "tariff_bdt_per_kwh": 12},
      {"hour": 9, "demand_kwh": 260, "solar_kwh": 140, "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 270, "solar_kwh": 190, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 275, "solar_kwh": 210, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 280, "solar_kwh": 220, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 270, "solar_kwh": 200, "tariff_bdt_per_kwh": 14},
      {"hour": 14, "demand_kwh": 265, "solar_kwh": 170, "tariff_bdt_per_kwh": 13},
      {"hour": 15, "demand_kwh": 260, "solar_kwh": 120, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 270, "solar_kwh": 60, "tariff_bdt_per_kwh": 18},
      {"hour": 17, "demand_kwh": 290, "solar_kwh": 15, "tariff_bdt_per_kwh": 22},
      {"hour": 18, "demand_kwh": 310, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
      {"hour": 19, "demand_kwh": 320, "solar_kwh": 0, "tariff_bdt_per_kwh": 30},
      {"hour": 20, "demand_kwh": 305, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
      {"hour": 21, "demand_kwh": 270, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 220, "solar_kwh": 0, "tariff_bdt_per_kwh": 12},
      {"hour": 23, "demand_kwh": 200, "solar_kwh": 0, "tariff_bdt_per_kwh": 9}
    ],
    "battery": {
      "capacity_kwh": 500,
      "initial_energy_kwh": 200,
      "minimum_energy_kwh": 50,
      "max_charge_kwh_per_hour": 100,
      "max_discharge_kwh_per_hour": 100
    }
  }'
```

### Run all public sample cases
```bash
python test_public_cases.py --url http://localhost:8000
```

---

## Docker Fallback

### Build and run locally
```bash
docker build -t gridwise-api:latest .

docker run -d \
  -p 8000:8000 \
  -e GEMINI_API_KEY=<your-gemini-api-key> \
  --name gridwise \
  gridwise-api:latest

# Verify
curl http://localhost:8000/health
```

### Pull from registry (after publishing)
```bash
docker pull <your-dockerhub-username>/gridwise-api:latest

docker run -d \
  -p 8000:8000 \
  -e GEMINI_API_KEY=<your-gemini-api-key> \
  --name gridwise \
  <your-dockerhub-username>/gridwise-api:latest
```

> **Port**: 8000 (exposed and bound to 0.0.0.0)  
> **No secrets baked into the image** — API key is passed as an environment variable at runtime.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ Yes | — | Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model name |

---

## API Reference

### `GET /health`
Returns readiness status.
```json
{"status": "ok"}
```

### `POST /optimize-energy`
Main endpoint. Accepts a 24-hour energy scenario with operator notes and returns a directive interpretation + optimized schedule.

**Request**: See `problem_statement.txt` for full schema.  
**Response**: `directive_interpretation[]` + `hourly_plan[]` + summary statistics.

**Error codes**:
- `400` — Malformed JSON or structurally invalid request
- `500` — Controlled internal error (no secrets or stack traces exposed)

---

## Dependencies

| Library | Version | Purpose |
|---|---|---|
| `fastapi` | ≥0.111 | HTTP API framework |
| `uvicorn` | ≥0.29 | ASGI server |
| `pydantic` | ≥2.7 | Request/response validation |
| `google-generativeai` | ≥0.7 | Gemini LLM client |
| `pulp` | ≥2.8 | Linear programming (CBC solver bundled) |
| `python-dotenv` | ≥1.0 | Local env file support |

---

## Known Limitations

- **Gemini API availability**: The service depends on Google's Gemini API. Ensure your API key has sufficient quota.
- **Rate limits**: Under very high request volume, Gemini may rate-limit. The service returns a 500 with a safe error message.
- **LP solver time**: The CBC solver is configured with a 25-second time limit. Very large or highly constrained scenarios may return suboptimal feasible solutions.
- **Simultaneous directives**: If contradictory hard constraints are sent (e.g., `no_charge_window` and `minimum_battery_reserve` that require charging), the LP solver may return infeasible. The problem statement guarantees organizer scoring cases are feasible.

---

## Security

- No API keys, tokens, or secrets are committed to this repository.
- Secrets are passed via environment variables at runtime.
- Error responses never expose raw stack traces or internal configuration.
- Only synthetic challenge data is processed; no live campus or personal data.
