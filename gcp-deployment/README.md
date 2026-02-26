# GCP Cloud Scheduler Configuration for Risk Alert Service

This directory contains configuration files and scripts for deploying the Risk Alert Service
to Google Cloud Platform with automatic monthly scheduling.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ AUTOMATED MONTHLY ALERT FLOW (GCP)                              │
└─────────────────────────────────────────────────────────────────┘

1. DATA AVAILABILITY
   ↓
   gs://fde-take-home/monthly_account_status.parquet
   (Updated by data pipeline on 1st of each month)

2. SCHEDULED TRIGGER (2nd of month at 9 AM UTC)
   ↓
   ┌────────────────────────────────────┐
   │ Cloud Scheduler                    │
   │ Schedule: 0 9 2 * *               │
   │ Target: Cloud Run Job              │
   │ Runs: scheduler.py                 │
   └────────────────────────────────────┘
   
3. SCHEDULER SCRIPT
   ↓
   - Calculates previous month (e.g., if today is Feb 2, processes Jan data)
   - Calls POST /runs API with source_uri and month
   
4. ALERT SERVICE (Cloud Run Service)
   ↓
   - Reads Parquet from GCS
   - Computes at-risk alerts
   - Sends to Slack
   - Persists outcomes to SQLite
   
5. MONITORING
   ↓
   Cloud Logging + Optional alerting on failures

MANUAL TRIGGER (Available Anytime)
   ↓
   Developer/Ops can call POST /runs directly via:
   - curl
   - Cloud Console
   - gcloud CLI
```

## Deployment Options

### Option 1: Cloud Scheduler → Cloud Run Service (Recommended)

**Best for:** Long-running service that can handle both scheduled and manual triggers

```bash
# 1. Deploy the main service to Cloud Run
gcloud run deploy risk-alert-service \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars="SLACK_WEBHOOK_BASE_URL=https://hooks.slack.com,SOURCE_URI=gs://fde-take-home/monthly_account_status.parquet"

# 2. Create Cloud Scheduler job
gcloud scheduler jobs create http risk-alerts-monthly \
  --schedule="0 9 2 * *" \
  --time-zone="America/New_York" \
  --uri="https://risk-alert-service-HASH-uc.a.run.app/runs" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"source_uri":"gs://fde-take-home/monthly_account_status.parquet","month":"auto","dry_run":false}' \
  --oidc-service-account-email=scheduler@PROJECT_ID.iam.gserviceaccount.com
```

### Option 2: Cloud Scheduler → Cloud Run Job (Cost-Optimized)

**Best for:** One-off batch processing, lower costs (only runs when triggered)

```bash
# 1. Create Cloud Run Job
gcloud run jobs create risk-alert-job \
  --image gcr.io/PROJECT_ID/risk-alert-service:latest \
  --region us-central1 \
  --set-env-vars="SOURCE_URI=gs://fde-take-home/monthly_account_status.parquet" \
  --tasks=1 \
  --max-retries=3 \
  --task-timeout=600s

# 2. Create Cloud Scheduler job to execute the job
gcloud scheduler jobs create http risk-alerts-monthly-job \
  --schedule="0 9 2 * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/v1/projects/PROJECT_ID/locations/us-central1/jobs/risk-alert-job:run" \
  --http-method=POST \
  --oauth-service-account-email=scheduler@PROJECT_ID.iam.gserviceaccount.com
```

### Option 3: Cloud Scheduler → Cloud Function (Lightweight)

**Best for:** Simple orchestration, don't need persistent service

```bash
# Deploy the scheduler.py as a Cloud Function
gcloud functions deploy trigger-risk-alerts \
  --runtime python310 \
  --trigger-http \
  --entry-point main \
  --set-env-vars="RISK_ALERT_SERVICE_URL=https://risk-alert-service-HASH-uc.a.run.app,SOURCE_URI=gs://fde-take-home/monthly_account_status.parquet"

# Create scheduler to call the function
gcloud scheduler jobs create http risk-alerts-monthly-fn \
  --schedule="0 9 2 * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-PROJECT_ID.cloudfunctions.net/trigger-risk-alerts" \
  --http-method=GET \
  --oidc-service-account-email=scheduler@PROJECT_ID.iam.gserviceaccount.com
```

## Configuration Files

### `cloud-scheduler-config.yaml`
Cloud Scheduler job definition for monthly automated runs.

### `cloudbuild.yaml`
Cloud Build configuration for CI/CD deployment.

### `terraform/`
Infrastructure as Code for complete GCP setup (optional).

## Manual Trigger Options

### Option 1: Direct API Call
```bash
# Get the service URL
SERVICE_URL=$(gcloud run services describe risk-alert-service --region=us-central1 --format='value(status.url)')

# Trigger for a specific month
curl -X POST "$SERVICE_URL/runs" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -d '{
    "source_uri": "gs://fde-take-home/monthly_account_status.parquet",
    "month": "2026-01-01",
    "dry_run": false
  }'
```

### Option 2: Using the Scheduler Script
```bash
# From your local machine or Cloud Shell
python scheduler.py \
  --service-url https://risk-alert-service-HASH-uc.a.run.app \
  --month 2026-01-01 \
  --check-status
```

### Option 3: Cloud Console
1. Navigate to Cloud Run → risk-alert-service
2. Click "Test" tab
3. Send POST request to `/runs` with JSON body

### Option 4: gcloud CLI
```bash
# Execute the scheduled job manually (immediate trigger)
gcloud scheduler jobs run risk-alerts-monthly
```

## Environment Variables

Set these in Cloud Run service configuration:

```bash
SLACK_WEBHOOK_BASE_URL=https://hooks.slack.com
SOURCE_URI=gs://fde-take-home/monthly_account_status.parquet
ARR_THRESHOLD=10000
REGION_CHANNEL_MAP={"regions":{"AMER":"amer-risk-alerts","EMEA":"emea-risk-alerts","APAC":"apac-risk-alerts"}}
DATABASE_URL=sqlite:////data/risk_alerts.db
GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa-key.json
```

## Permissions Required

### Service Account Permissions
```bash
# Create service account
gcloud iam service-accounts create risk-alert-runner \
  --display-name="Risk Alert Service Runner"

# Grant GCS read access
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:risk-alert-runner@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"

# Grant Cloud Run invoker (for scheduler)
gcloud run services add-iam-policy-binding risk-alert-service \
  --member="serviceAccount:scheduler@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --region=us-central1
```

## Monitoring & Alerting

### Cloud Logging Queries

**View all scheduled runs:**
```
resource.type="cloud_run_revision"
resource.labels.service_name="risk-alert-service"
textPayload=~"Run .* completed successfully"
```

**View failures:**
```
resource.type="cloud_run_revision"
resource.labels.service_name="risk-alert-service"
severity>=ERROR
```

### Cloud Monitoring Alerts

Create alert policies for:
1. **Failed runs** - Trigger if error logs detected
2. **No runs in 32 days** - Alert if scheduler missed a run
3. **High failure rate** - Alert if >10% of alerts fail to send

```bash
# Example: Create alert for failed runs
gcloud alpha monitoring policies create \
  --notification-channels=EMAIL_CHANNEL_ID \
  --display-name="Risk Alert Service Failures" \
  --condition-display-name="Error logs detected" \
  --condition-threshold-value=1 \
  --condition-threshold-duration=60s
```

## Testing

### Test Locally
```bash
# Start the service locally
uvicorn app.main:app --host 0.0.0.0 --port 8000

# In another terminal, test the scheduler
python scheduler.py \
  --service-url http://localhost:8000 \
  --month 2026-01-01 \
  --dry-run \
  --check-status
```

### Test in GCP (Dry Run)
```bash
# Trigger a dry run (won't send Slack alerts)
SERVICE_URL=$(gcloud run services describe risk-alert-service --region=us-central1 --format='value(status.url)')

curl -X POST "$SERVICE_URL/runs" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "gs://fde-take-home/monthly_account_status.parquet",
    "month": "2026-01-01",
    "dry_run": true
  }'
```

## Cost Estimation

### Cloud Run Service (Always-on)
- **Minimum instances:** 0 (scales to zero when idle)
- **Maximum instances:** 10
- **Cost:** ~$5-10/month for typical usage (mostly idle, spikes on 2nd of month)

### Cloud Run Job (Batch only)
- **Cost:** ~$0.50/month (only charged for actual execution time)

### Cloud Scheduler
- **Cost:** $0.10/job/month = $0.10/month

### Cloud Storage
- **Storage:** ~$0.02/GB/month for Parquet file
- **Operations:** Negligible (1 read/month)

**Total estimated cost:** $5-10/month (service) or $0.60/month (job-based)

## Troubleshooting

### Scheduler not triggering
```bash
# Check scheduler status
gcloud scheduler jobs describe risk-alerts-monthly

# Check scheduler logs
gcloud logging read "resource.type=cloud_scheduler_job" --limit 50
```

### Service failing to start
```bash
# Check Cloud Run logs
gcloud run services logs read risk-alert-service --region=us-central1 --limit=50
```

### GCS access denied
```bash
# Verify service account has permissions
gcloud projects get-iam-policy PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:risk-alert-runner@*"
```

## Migration from Manual to Automated

1. **Phase 1 (Current):** Manual triggers via API
2. **Phase 2 (Deploy scheduled job):**
   ```bash
   # Deploy scheduler (runs in parallel with manual option)
   gcloud scheduler jobs create http risk-alerts-monthly ...
   ```
3. **Phase 3 (Monitor):** Watch automated runs for 2-3 months
4. **Phase 4 (Optimize):** Adjust schedule, thresholds, or switch to Cloud Run Job

## Support

For issues or questions:
- Check logs: `gcloud run services logs read risk-alert-service`
- Check scheduler: `gcloud scheduler jobs list`
- Manual trigger: `gcloud scheduler jobs run risk-alerts-monthly`
