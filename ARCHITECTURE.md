# Risk Alert Service - Architecture

System architecture, component interactions, and design patterns.

## System Overview

```
┌─────────────┐      ┌──────────────────────┐      ┌─────────────┐
│   Parquet   │─────▶│  Risk Alert Service  │─────▶│   Slack     │
│   Files     │      │  (FastAPI)           │      │   Webhooks  │
│             │      │                      │      │             │
│  • Local    │      │  Port 8000           │      │  Regional   │
│  • GCS      │      │  REST API            │      │  Channels   │
│  • S3       │      │                      │      │             │
└─────────────┘      └──────┬───────────────┘      └─────────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │  SQLite Database │
                   │  (Audit Trail)   │
                   │                  │
                   │  • Alert outcomes│
                   │  • Replay safety │
                   └─────────────────┘
```

## Component Architecture

```
                     RISK ALERT SERVICE
                     FastAPI Application
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
    ┌──────────────────┐ ┌─────────┐ ┌──────────────┐
    │  API Endpoints   │ │ Models  │ │ Health Check │
    │                  │ │ Config  │ │              │
    │ • POST /runs     │ │         │ │ • GET /health│
    │ • POST /preview  │ │         │ │              │
    │ • GET /runs      │ │         │ │              │
    └────────┬─────────┘ └─────────┘ └──────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────┐
    │        ALERT SERVICE (Orchestrator)              │
    │                                                  │
    │  Coordinates:                                    │
    │  1. Data loading & processing                   │
    │  2. Replay safety checks                        │
    │  3. Regional routing                            │
    │  4. Slack notification                          │
    │  5. Outcome persistence                         │
    └─────────────────────────────────────────────────┘
             │
    ┌────────┼────────┬──────────────┐
    │        │        │              │
    ▼        ▼        ▼              ▼
┌─────────┐ ┌─────┐ ┌──────────┐ ┌──────────┐
│Storage  │ │Data │ │  Slack   │ │Database  │
│Layer    │ │Proc │ │  Client  │ │(SQLite)  │
└─────────┘ └─────┘ └──────────┘ └──────────┘
```

## Data Flow

### Request Flow

```
1. TRIGGER
   POST /runs {"source_uri": "...", "month": "2025-05-01"}

2. STORAGE LAYER
   Read Parquet with optimizations:
   • Predicate pushdown
   • Column pruning
   Result: ~150K rows

3. DATA PROCESSOR
   Transform & calculate:
   • Deduplicate by latest updated_at
   • Filter: status = "At Risk"
   • Calculate risk duration
   • Filter: arr >= $10,000
   Result: ~170 alerts

4. ALERT SERVICE
   Process each alert:
   • Check if already sent (replay safety)
   • Map region to Slack channel
   • Send to Slack (with retries)
   • Record outcome

5. SLACK CLIENT
   Send webhook:
   • POST to regional webhook
   • Retry on 429/500
   • Return success/failure

6. DATABASE
   Persist outcome:
   • INSERT alert_outcome
   • Unique constraint prevents duplicates
```

## Replay Safety Mechanism

Database unique constraint prevents duplicate alerts:

```
DATABASE CONSTRAINT:
  UNIQUE(account_id, month, alert_type)

FLOW:
  New Alert
      │
      ▼
  Check: Already sent?
      │
  ┌───┴────┐
  │        │
  ▼        ▼
EXISTS   NOT EXISTS
  │        │
  ▼        ▼
SKIP     SEND + INSERT

OUTCOME STATES:
  • sent            - Successfully delivered
  • failed          - Slack API error after retries
  • skipped_replay  - Already sent previously
  • unknown_region  - No valid region mapping
```

Benefits:
- Safe re-runs without spam
- Error recovery without duplicates
- Full audit trail

## Data Processing Pipeline

### Optimization Strategy

```
INPUT: monthly_account_status.parquet (10GB)
  18M rows (36 months × 500K accounts)
      │
      ▼
STEP 1: Predicate Pushdown (PyArrow)
  Filter: month in last 24
  Result: 4M rows (70% reduction)
      │
      ▼
STEP 2: Column Pruning (PyArrow)
  Read only 9 needed columns
  Skip other 40+ columns
  Benefit: 82% memory savings
      │
      ▼
STEP 3: Deduplication (Pandas)
  Keep latest by updated_at
  Result: 3.95M rows
      │
      ▼
STEP 4: Risk Calculation (Pandas)
  Calculate continuous months
  Walk backward through history
      │
      ▼
STEP 5: ARR Filtering (Pandas)
  Filter: arr >= $10,000
  Result: 170 alerts
      │
      ▼
OUTPUT: 170 alerts ready to send
```

## Regional Routing

```
Alert → Check Region → Route to Channel

AMER   → Region Mapper → amer-risk-alerts
EMEA   → Region Mapper → emea-risk-alerts  
APAC   → Region Mapper → apac-risk-alerts
null   → Region Mapper → ERROR: Log & Skip

Unknown Region Handling:
  • Not sent to Slack
  • Recorded as "unknown_region"
  • Aggregated notification to support@quadsci.ai
    (current: logged, production: email sender)
```

## Retry Strategy

```
POST to Slack
    │
    ▼
Response?
    │
┌───┴───────┬────────┬────────┐
│           │        │        │
▼           ▼        ▼        ▼
200 OK    429      500    Other 4xx
  │      Rate     Error      │
  │      Limit      │        │
  ▼        │        │        ▼
SUCCESS    ▼        ▼      FAIL
outcome  RETRY   RETRY   outcome
='sent'    │        │    ='error'
           │        │
           └────┬───┘
                │
          Exponential Backoff
          Attempt 1: wait 1s
          Attempt 2: wait 2s
          Attempt 3: wait 4s
                │
            ┌───┴───┐
            ▼       ▼
         SUCCESS  FAIL

Retry Conditions:
- 429: Retry with backoff
- 500/502/503: Retry with backoff
- 4xx: No retry, log as failed
- Network error: Retry with backoff
```

## Component Details

### 1. API Layer (`main.py`)

**Responsibilities**:
- HTTP request handling
- Input validation (Pydantic models)
- Response formatting
- Error handling

**Endpoints**:
```
POST /runs          → Trigger alert run
POST /preview       → Dry-run (no Slack sends)
GET /runs           → List all runs
GET /runs/{run_id}  → Get run details
GET /health         → Health check
```

### 2. Alert Service (`alert_service.py`)

**Responsibilities**:
- Orchestrate entire alert workflow
- Manage replay safety logic
- Route alerts by region
- Coordinate between components

**Flow**:
1. Load data from storage
2. Process data (filter, deduplicate, calculate)
3. Check replay safety for each alert
4. Send alerts to Slack (if not duplicate)
5. Persist all outcomes to database

### 3. Data Processor (`data_processor.py`)

**Responsibilities**:
- Read and filter Parquet files
- Deduplicate records (by latest `updated_at`)
- Calculate continuous risk duration
- Apply business rules (ARR threshold)

**Algorithm - Risk Duration Calculation**:
```python
# For account A-12345 in target month 2025-05:
# 
# Month History:
#   2025-05: At Risk   ← Target month (start)
#   2025-04: At Risk   → duration++
#   2025-03: At Risk   → duration++
#   2025-02: Healthy   → STOP
#
# Result:
#   duration_months = 3
#   risk_start_month = 2025-03-01
```

### 4. Slack Client (`slack_client.py`)

**Responsibilities**:
- Format alert messages
- Send webhooks to regional channels
- Handle retries with exponential backoff
- Map regions to webhook URLs

**Message Format**:
```json
{
  "text": "Account Alert",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*Acme Corp* (A-12345)\n• At Risk for 3 months\n• ARR: $50,000"
      }
    }
  ]
}
```

### 5. Storage Layer (`storage.py`)

**Responsibilities**:
- Abstract file storage (local/GCS/S3)
- Optimize Parquet reads
- Handle different URI schemes

**URI Support**:
- `file://` → Local filesystem
- `gs://` → Google Cloud Storage
- `s3://` → Amazon S3

### 6. Database (`models.py`)

**Responsibilities**:
- Define SQLAlchemy models
- Manage database connections
- Enforce replay safety constraints

**Schema**:
```sql
CREATE TABLE alert_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    month TEXT NOT NULL,
    alert_type TEXT NOT NULL DEFAULT 'risk_alert',
    status TEXT NOT NULL,
    channel TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, month, alert_type)  -- Replay safety
);
```

---

## Key Design Patterns

### 1. Idempotency (Replay Safety)

**Problem**: What if the service runs twice for the same month?

**Solution**: Database unique constraint prevents duplicates
- First run: Alerts sent, outcomes inserted
- Second run: Database check finds existing records, skips sending
- Result: No duplicate Slack messages

### 2. Regional Isolation

**Problem**: Different teams need different notifications

**Solution**: Route alerts based on account region
- Each region has dedicated Slack channel
- Failed/unknown regions logged separately
- Teams only see relevant alerts

### 3. Graceful Degradation

**Problem**: What if Slack is down?

**Solution**: Fail gracefully, log errors, allow retry
- Slack errors don't crash entire run
- Failed alerts logged with error message
- Can retry later without re-sending successes

### 4. Storage Abstraction

**Problem**: Need to support multiple data sources

**Solution**: Pluggable storage layer
- Abstract interface for read operations
- Implementations for local, GCS, S3
- Easy to add new storage backends

---

## Deployment Architecture

### Local Testing

```
┌──────────────────┐     ┌─────────────────┐
│ Terminal 1       │     │ Terminal 2      │
│                  │     │                 │
│ Mock Slack       │     │ Main Service    │
│ (Port 8001)      │     │ (Port 8000)     │
│                  │     │                 │
│ Simulates:       │◀────│ Sends to:       │
│ • Real webhooks  │     │ localhost:8001  │
│ • Rate limits    │     │                 │
│ • Server errors  │     │                 │
└──────────────────┘     └─────────────────┘
         │                        │
         ▼                        ▼
┌──────────────────┐     ┌─────────────────┐
│mock_slack_       │     │risk_alerts.db   │
│requests.jsonl    │     │(SQLite)         │
└──────────────────┘     └─────────────────┘
```

### Production (GCP)

```
┌──────────────────┐
│ Cloud Scheduler  │
│ (Cron: Monthly)  │
└────────┬─────────┘
         │ POST /runs
         ▼
┌─────────────────────────────────────────┐
│         Cloud Run Service                │
│         (Container)                      │
│                                          │
│  • Auto-scaling: 0-10 instances         │
│  • Timeout: 15 minutes                  │
│  • Memory: 2GB                          │
│  • CPU: 2 vCPU                          │
└─────────┬─────────────────────┬─────────┘
          │                     │
          ▼                     ▼
┌──────────────────┐  ┌──────────────────┐
│ Cloud Storage    │  │ Slack API        │
│ (Parquet files)  │  │ (Production)     │
│                  │  │                  │
│ gs://bucket/     │  │ Real webhooks    │
│ data/            │  │ Rate limits      │
└──────────────────┘  └──────────────────┘
          │
          ▼
┌──────────────────┐
│ Cloud SQL        │
│ (PostgreSQL)     │
│ or SQLite        │
└──────────────────┘
```

---

## Testing Architecture

### Mock Slack Server

The `mock_slack/` component simulates real Slack behavior:

```
┌────────────────────────────────────────────────┐
│          MOCK SLACK SERVER                     │
│          (Port 8001)                           │
└────────────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
        ▼           ▼           ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│POST      │ │GET       │ │GET       │
│/hooks/*  │ │/logs     │ │/health   │
└────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │
     ▼            ▼            ▼
┌──────────────────────────────────────┐
│ Behavior Simulation:                 │
│ • 10% chance → 429 (Rate Limit)     │
│ • 5% chance → 500 (Server Error)    │
│ • 85% chance → 200 (Success)        │
│                                      │
│ All requests logged to:              │
│ mock_slack_requests.jsonl            │
└──────────────────────────────────────┘
```

**Benefits**:
- Test retry logic locally
- Verify alert formatting
- Debug issues without Slack API
- Replay and inspect all alerts

---

## Scalability Considerations

### Current Capacity

- **File Size**: Up to 10GB Parquet files
- **Rows**: ~18M rows (36 months × 500K accounts)
- **Processing Time**: ~2-3 minutes
- **Memory Usage**: ~500MB peak
- **Alerts/Run**: ~500 alerts (typical)

### Scale Limits

| Dimension | Current | Next Bottleneck | Solution |
|-----------|---------|----------------|----------|
| File Size | 10GB | Memory | Streaming/chunking |
| Alert Volume | 500/run | Slack rate limits | Batch sends |
| Concurrency | 1 run at a time | Database locks | PostgreSQL |
| History | 36 months | Query time | Partitioning |

### Future Optimizations

If you need to scale further:

1. **Streaming Processing**: Use Dask/Ray for 100GB+ files
2. **Batch Sends**: Group alerts, send in batches
3. **Async Processing**: Queue-based architecture (Pub/Sub, SQS)
4. **Database**: Move to PostgreSQL for production scale
5. **Caching**: Cache historical data for duration calculations

---

## Security Considerations

### Current Implementation

- **Secrets**: Environment variables (not hardcoded)
- **Database**: Local SQLite (no network exposure)
- **API**: No authentication (internal use only)
- **Slack**: Webhook URLs kept in environment

### Production Hardening

For production deployment:

1. **Authentication**: Add API key or OAuth
2. **Secrets Management**: Use Secret Manager (GCP) or AWS Secrets
3. **Database**: Use Cloud SQL with SSL/TLS
4. **Network**: Deploy in private VPC
5. **Audit**: Log all API calls with caller identity
6. **Rate Limiting**: Add request throttling

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| API Framework | FastAPI | High-performance async API |
| Data Processing | Pandas + PyArrow | Efficient Parquet handling |
| Database | SQLite (local) / PostgreSQL (prod) | Replay safety & audit |
| HTTP Client | httpx | Async webhook sends |
| Validation | Pydantic | Type-safe models |
| Testing | Mock Slack Server | Local testing |
| Deployment | Docker + Cloud Run | Serverless containers |
| Scheduling | Cloud Scheduler | Monthly triggers |

---

## Design Principles

1. **Simplicity**: Clear separation of concerns, each component has one job
2. **Reliability**: Replay safety, retry logic, error tracking
3. **Observability**: Comprehensive logging, health checks, metrics
4. **Testability**: Mock components, local testing, dry-run mode
5. **Scalability**: Optimized data processing, ready to scale up
6. **Maintainability**: Clean code, good docs, easy to understand

---

## Further Reading

- **README.md**: Implementation documentation and setup guide
- **mock_slack/README.md**: Mock server documentation
- **gcp-deployment/README.md**: Production deployment guide
- **Instructions.md**: Original assignment requirements

---

**Version**: 1.0  
**Last Updated**: February 2026
