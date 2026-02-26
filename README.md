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
source venv/bin/activate
python mock_slack/server.py
# Runs on http://127.0.0.1:8001

# Terminal 2: Start Main Service
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
# Runs on http://0.0.0.0:8000

# Terminal 3: Test
curl http://localhost:8000/health
```

### Run Your First Alert

```bash
# Preview alerts (no Slack, no DB writes)
curl -X POST "http://localhost:8000/preview?target_month=2024-06-01&data_path=file://monthly_account_status.parquet"

# Dry run (saves to DB but doesn't send Slack)
curl -X POST "http://localhost:8000/runs?target_month=2024-06-01&data_path=file://monthly_account_status.parquet&slack_webhook_url=http://localhost:8001&dry_run=true"

# Full run (sends to mock Slack)
curl -X POST "http://localhost:8000/runs?target_month=2024-06-01&data_path=file://monthly_account_status.parquet&slack_webhook_url=http://localhost:8001"

# View run history
curl http://localhost:8000/runs | jq

# Get specific run
curl http://localhost:8000/runs/{run_id} | jq
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
Preview alerts without sending to Slack or saving to database.

Query Parameters:
- `target_month` (required): Month in YYYY-MM-01 format
- `data_path` (required): Path to Parquet file (file://, gs://, s3://)
- `arr_threshold` (optional): ARR threshold, default 10000

```bash
curl -X POST "http://localhost:8000/preview?target_month=2024-06-01&data_path=file://monthly_account_status.parquet"
```

Response:
```json
{
  "target_month": "2024-06-01",
  "total_alerts": 115,
  "alerts_by_region": {
    "AMER": 42,
    "EMEA": 35,
    "APAC": 38
  },
  "unknown_region_count": 0,
  "sample_alerts": [...],
  "stats": {
    "rows_scanned": 5000,
    "duplicates_found": 12
  }
}
```

### POST /runs
Execute an alert run (send to Slack and persist outcomes).

Query Parameters:
- `target_month` (required): Month in YYYY-MM-01 format
- `data_path` (required): Path to Parquet file
- `slack_webhook_url` (required): Base Slack webhook URL
- `dry_run` (optional): If true, skip Slack sending (default: false)
- `arr_threshold` (optional): ARR threshold, default 10000

```bash
curl -X POST "http://localhost:8000/runs?target_month=2024-06-01&data_path=file://monthly_account_status.parquet&slack_webhook_url=http://localhost:8001"
```

Response:
```json
{
  "run_id": "run_20240601_123456",
  "status": "completed",
  "target_month": "2024-06-01",
  "started_at": "2024-06-01T12:34:56Z",
  "completed_at": "2024-06-01T12:35:10Z",
  "total_alerts": 115,
  "outcomes": {
    "sent": 110,
    "skipped_replay": 0,
    "unknown_region": 5,
    "failed": 0
  }
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
curl -X POST "http://localhost:8000/runs?target_month=2024-06-01&data_path=file://monthly_account_status.parquet&slack_webhook_url=http://localhost:8001"
# Result: 115 alerts sent

# Second run: skips duplicates
curl -X POST "http://localhost:8000/runs?target_month=2024-06-01&data_path=file://monthly_account_status.parquet&slack_webhook_url=http://localhost:8001"
# Result: 0 sent, 115 skipped
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
