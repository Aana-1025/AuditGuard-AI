# AuditGuard - AI Fraud Detection Workspace

AuditGuard is an AI-assisted fraud auditing workspace built for hackathon demos and rapid iteration. It combines a FastAPI backend, a React frontend, and a lightweight inference runner to simulate how an analyst or agent can review expense transactions, flag suspicious activity, and finalise a scored audit session.

## Problem Statement

Expense fraud is often caught too late because reviews are manual, inconsistent, and hard to scale. AuditGuard turns that challenge into an interactive review environment where suspicious transactions can be surfaced, actioned, and scored in real time. The platform is designed to demonstrate how rule-based reasoning, risk scoring, and audit workflows can work together in a practical fraud detection workspace.

## Features

- Fraud detection rules for merchant, receipt, category cap, MCC, duplicate, and split-transaction patterns
- Continuous risk scoring for each transaction and aggregate audit scoring at episode finalisation
- Audit simulation workflow with `reset`, per-item review actions, and `finalise`
- Frontend review dashboard with action controls, item status tracking, and final result display
- Inference runner that executes a simple rule policy against the backend API

## Tech Stack

- Backend: FastAPI
- Frontend: React + Vite
- Client runner: Python + `requests`

## API Endpoints

### `POST /reset`

Starts a fresh audit episode.

Example request:

```json
{
  "scenario": "easy",
  "seed": 0
}
```

### `POST /step`

Applies one audit action.

Example request:

```json
{
  "action_type": "flag",
  "item_id": "EXP-001"
}
```

Typical actions:

- `approve`
- `flag`
- `finalise`

## How To Run Locally

### 1. Backend

```bash
cd auditguard_env
python -m pip install -r server/requirements.txt
uvicorn server.app:app --reload
```

The backend will be available at `http://127.0.0.1:8000`.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend will be available at `http://localhost:5173`.

### 3. Inference Runner

Install the root Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
API_BASE_URL=http://127.0.0.1:8000
MODEL_NAME=hackathon-rule-policy
HF_TOKEN=
```

Run:

```bash
python inference.py
```

## How Inference Works

The root `inference.py` script drives the environment through the backend API:

1. Calls `POST /reset`
2. Reads the returned transactions
3. Applies a simple rule policy:
   - `GIFT HUB` -> `FLAG`
   - `CASH DEPOT` -> `FLAG`
   - all other merchants -> `APPROVE`
4. Calls `POST /step` for each transaction
5. Calls `POST /step` again with `action_type = "finalise"`

It uses environment variables only and never hardcodes secrets.

## Example Output

```text
START
STEP: resetting environment
STEP: processing item EXP-001 -> FLAG
STEP: processing item EXP-002 -> APPROVE
STEP: processing item EXP-003 -> APPROVE
STEP: finalising audit
END
```

## Project Structure

```text
AuditGaurd/
|-- README.md
|-- inference.py
|-- requirements.txt
|-- auditguard_env/
|   |-- openenv.yaml
|   `-- server/
|       |-- app.py
|       |-- auditguard_environment.py
|       |-- grading.py
|       |-- rules_engine.py
|       |-- scenario_factory.py
|       |-- scoring.py
|       `-- models.py
`-- frontend/
    |-- package.json
    `-- src/
        |-- Dashboard.jsx
        |-- AIInsights.jsx
        `-- ProgressBar.jsx
```

## Hackathon Note

AuditGuard is optimized for demonstration value: quick setup, clear fraud review flows, and a polished analyst-facing UI. It is built to showcase how AI-assisted auditing can improve consistency, transparency, and speed in fraud detection workflows.
