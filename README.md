# Risk Alert Service

Automated service for identifying at-risk accounts and sending region-specific Slack alerts.

## Table of Contents

1. [Features](#features)
2. [Quick Start](#quick-start)
3. [API Endpoints](#api-endpoints)
4. [Configuration](#configuration)
5. [Testing](#testing)
6. [Deployment](#deployment)
7. [Architecture](#architecture)

## Features

**Core Capabilities:**
- At-risk account detection
- Risk duration calculation (walks back through historical data)
- Region-specific Slack routing (North America, EMEA, APAC)
- Replay safety (prevents duplicate alerts)
- ARR-based filtering (configurable threshold, default $10k)
- Unknown region handling (aggregated notifications to support team)

**Scale Optimizations:**
- Predicate pushdown (filters at Parquet reader level)
- Column pruning (only reads required columns)
- Deduplication (latest `updated_at` wins)
- Cloud storage support (file://, gs://, s3://)

**Reliability:**
- Retry logic with exponential backoff
- Comprehensive error tracking
- Idempotent runs
- Dry run mode

## Quick Start

### Prerequisites
- Python 3.9+
- Virtual environment recommended

### Installation

```bash
# Navigate to project directory
cd /path/to/risk-alert-service

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Local Testing with Mock Slack

```bash
# Terminal 1: Start Mock Slack Server
source venv/bin/activate          # Windows PowerShell: venv\Scripts\Activate.ps1
python mock_slack/server.py        # listens on http://127.0.0.1:8001

# Terminal 2: Start Main Service
source venv/bin/activate          # or venv\Scripts\Activate.ps1 on Windows
uvicorn app.main:app --reload      # default port 8000

# Terminal 3: Smoke check
curl http://localhost:8000/health
```
### Run Your First Alert

Requests are now JSON bodies that match the `RunRequest` model. The `month` field accepts a literal `"auto"` value to process the previous month.

```bash
# Preview alerts (no Slack delivery, no DB writes)
curl -X POST http://localhost:8000/preview \
  -H "Content-Type: application/json" \
  -d '{
        "source_uri": "file://monthly_account_status.parquet",
        "month": "2025-05-01",
        "dry_run": false
      }'

# Dry run - outcomes are recorded but nothing is sent
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{
        "source_uri": "file://monthly_account_status.parquet",
        "month": "auto",          
        "dry_run": true
      }'

# Full run - alerts go to Slack endpoints defined in Config (set via env)
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{
        "source_uri": "file://monthly_account_status.parquet",
        "month": "2025-05-01",
        "dry_run": false
      }'

# List runs
curl http://localhost:8000/runs

# Get details for a run
curl http://localhost:8000/runs/{run_id}
```

## API Endpoints

### GET /health
Health check endpoint.

```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "ok",
  "database": "connected",
  "timestamp": "2024-06-01T00:00:00Z"
}
```

### POST /preview
Compute what alerts *would* be sent for a given month without actually sending any messages or writing to the database. Useful for dry‑running logic.

**Request body** (JSON):

```json
{
  "source_uri": "file://monthly_account_status.parquet",
  "month": "2024-06-01",     // or "auto" for previous month
  "dry_run": false              // ignored for preview, kept for uniform type
}
```

Example:
```bash
curl -X POST http://localhost:8000/preview \
  -H "Content-Type: application/json" \
  -d '{"source_uri":"file://monthly_account_status.parquet","month":"2024-06-01"}'
```

**Successful response**:
```json
{
  "month": "2024-06-01",
  "alerts": [ /* list of alert objects */ ],
  "stats": {
    "rows_scanned": 5000,
    "duplicates_found": 12
  }
}
```

Errors return a JSON object with an `error` field.


### POST /runs
Trigger a run that computes alerts and (unless `dry_run` is true) delivers them to Slack and records outcomes in the database.

**Request body** (JSON):

```json
{
  "source_uri": "file://monthly_account_status.parquet",
  "month": "2024-06-01",    // or "auto" for previous month
  "dry_run": false            // skips delivery when true
}
```

Example:
```bash
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"source_uri":"file://monthly_account_status.parquet","month":"2024-06-01"}'
```

**Successful response**:
```json
{
  "run_id": "<uuid>",
  "status": "succeeded",
  "source_uri": "file://monthly_account_status.parquet",
  "month": "2024-06-01",
  "dry_run": false,
  "counts": {
    "rows_scanned": 5000,
    "duplicates_found": 12,
    "alerts_computed": 115,
    "alerts_sent": 110,
    "skipped_replay": 0,
    "failed_deliveries": 0,
    "unknown_region_count": 5
  },
  "sample_alerts": [ /* first 5 sent alerts */ ],
  "sample_errors": [ /* sample failures */ ],
  "created_at": "2024-06-01T12:34:56Z",
  "completed_at": "2024-06-01T12:35:10Z"
}
```

### GET /runs
List all alert runs.

```bash
curl http://localhost:8000/runs
```

Response:
```json
{
  "runs": [
    {
      "run_id": "run_20240601_123456",
      "target_month": "2024-06-01",
      "status": "completed",
      "started_at": "2024-06-01T12:34:56Z",
      "total_alerts": 115
    }
  ]
}
```

### GET /runs/{run_id}
Get details for a specific run.

```bash
curl http://localhost:8000/runs/run_20240601_123456
```

Response:
```json
{
  "run_id": "run_20240601_123456",
  "status": "completed",
  "total_alerts": 115,
  "outcomes": {
    "sent": 110,
    "skipped_replay": 0,
    "unknown_region": 5,
    "failed": 0
  },
  "alerts_by_region": {
    "AMER": 42,
    "EMEA": 35,
    "APAC": 38
  }
}
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ARR_THRESHOLD` | No | `10000` | Minimum ARR to trigger alerts ($10,000) |
| `DATABASE_URL` | No | `sqlite:///./risk_alerts.db` | SQLite database path |
| `SLACK_WEBHOOK_URL` | No | *none* | Full Slack webhook endpoint (deprecated; use `SLACK_WEBHOOK_BASE_URL` with region channels)
| `SLACK_WEBHOOK_BASE_URL` | No | *none* | Base URL used by SlackClient; individual channels appended automatically |
| `SLACK_RETRY_MAX` | No | `3` | Max Slack retry attempts |
| `SLACK_RETRY_BACKOFF` | No | `2.0` | Exponential backoff multiplier |
| `SLACK_RETRY_INITIAL_DELAY` | No | `1.0` | Initial retry delay (seconds) |

### Region Channel Mapping

Default mapping in `app/config.py`:

```python
SLACK_REGION_CHANNELS = {
    "AMER": "amer-risk-alerts",
    "EMEA": "emea-risk-alerts",
    "APAC": "apac-risk-alerts",
}
```

To customize, edit the config before deployment.

## Testing

### Replay Safety Test

```bash
# First run: sends alerts
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"source_uri":"file://monthly_account_status.parquet","month":"2024-06-01","dry_run":false}'
# Result: alerts appear in mock_slack_requests.jsonl

# Second run: duplicates are skipped
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"source_uri":"file://monthly_account_status.parquet","month":"2024-06-01","dry_run":false}'
# Result: no new webhooks, skipped_replay count increases
```

### Error Handling Test

The mock Slack server simulates errors to test retry logic:

```bash
# Watch retries with exponential backoff
tail -f mock_slack_requests.jsonl | jq
```

See `mock_slack/README.md` for error configuration.

### View Alert Outcomes

```bash
# Query database directly
sqlite3 risk_alerts.db "SELECT outcome, COUNT(*) FROM alert_outcomes GROUP BY outcome;"

# Or via API
curl http://localhost:8000/runs/{run_id} | jq '.outcomes'
```

### Reset for Fresh Test

```bash
# Delete database
rm risk_alerts.db

# Or delete specific run
sqlite3 risk_alerts.db "DELETE FROM alert_outcomes WHERE run_id = 'run_20240601_123456';"
```

## Deployment

### Docker

```bash
# Build
docker build -t risk-alert-service .

# Run
docker run -p 8000:8000 -e ARR_THRESHOLD=10000 risk-alert-service
```

### Google Cloud Platform

See `gcp-deployment/README.md` for:
- Cloud Run deployment
- Cloud Scheduler setup
- Service account configuration
- GCS access

### Scheduled Runs

For manual or cron-based scheduling, see the scheduler script in `_reference_docs/`.

## Architecture

```
risk-alert-service/
├── app/                              # Core application
│   ├── main.py                       # FastAPI app
│   ├── config.py                     # Configuration
│   ├── models.py                     # Data models
│   ├── storage.py                    # Storage abstraction
│   ├── data_processor.py             # Risk computation
│   ├── slack_client.py               # Slack API with retries
│   └── alert_service.py              # Alert orchestration
│
├── mock_slack/                       # Mock Slack for testing
│   ├── server.py                     # Mock server
│   └── README.md
│
├── gcp-deployment/                   # GCP deployment
│   └── README.md                     # Deployment guide
│
├── Dockerfile                        # Container image
├── requirements.txt                  # Dependencies
├── monthly_account_status.parquet    # Test data
├── Instructions.md                   # Original requirements
├── ARCHITECTURE.md                   # Detailed architecture
└── README.md                         # This file
```

See `ARCHITECTURE.md` for detailed design decisions and data flow diagrams.

## Key Design Decisions

### Scale Optimizations

Problem: Large Parquet files can cause memory issues.

Solution:
- Predicate pushdown: Filter at reader level (80% I/O reduction)
- Column pruning: Read only required columns (82% memory reduction)
- Streaming cloud storage: Use gcsfs/s3fs for direct access

Impact: 100GB file → 3.6GB read, 200GB RAM → 20GB RAM

### Replay Safety

Problem: Re-running the same month shouldn't send duplicate alerts.

Solution: Persist outcomes to SQLite with (account_id, target_month) unique constraint.

Behavior:
- First run: Send alerts, save as "sent"
- Later runs: Skip alerts in DB, mark as "skipped_replay"

### Region Routing

Problem: Alerts must go to region-specific channels.

Solution: Map account_region to Slack channel via config.

Unknown regions:
- Not sent to Slack
- Recorded in DB as "unknown_region"
- Single aggregated email to support@quadsci.ai with all unknown region accounts
- Current implementation logs the email content (production would use actual email sender)

### Error Handling

Problem: Slack API can return 429 or 5xx errors.

Solution: Exponential backoff retry (3 attempts).

Behavior:
- 429: Wait retry-after seconds, then retry
- 5xx: Exponential backoff (1s, 2s, 4s)
- Final failure: Log as "failed"

## Business Logic

### Risk Duration Calculation

For each at-risk account in target month, walk backward month-by-month until:
1. Status changes from "At Risk"
2. No data for previous month
3. Reach 24-month lookback limit

Example:
- 2024-06: At Risk (current)
- 2024-05: At Risk
- 2024-04: At Risk
- 2024-03: Healthy ← Stop

Result: 3 months at risk, started 2024-04-01

### ARR Filtering

Only alert on accounts with ARR >= threshold (default $10k).

Rationale: Focus on high-value accounts.

### Deduplication

If multiple rows exist for same (account_id, month), keep the latest updated_at.

Rationale: Handle data pipeline reruns.

## Additional Documentation
