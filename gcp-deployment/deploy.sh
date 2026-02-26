#!/bin/bash
# GCP deployment script for Risk Alert Service
# Usage: ./deploy.sh [PROJECT_ID] [REGION]

set -e

PROJECT_ID="${1:-your-gcp-project-id}"
REGION="${2:-us-central1}"
SERVICE_NAME="risk-alert-service"
SCHEDULER_JOB_NAME="risk-alerts-monthly"
SERVICE_ACCOUNT="risk-alert-runner"
SCHEDULER_SA="scheduler"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Risk Alert Service - GCP Deployment${NC}"
echo ""
echo "Project ID: $PROJECT_ID"
echo "Region: $REGION"
echo "Service Name: $SERVICE_NAME"
echo ""

echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}Error: gcloud CLI not found. Please install Google Cloud SDK.${NC}"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: docker not found. Please install Docker.${NC}"
    exit 1
fi

echo -e "${YELLOW}Setting GCP project...${NC}"
gcloud config set project "$PROJECT_ID"

echo -e "${YELLOW}Enabling required GCP APIs...${NC}"
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    cloudscheduler.googleapis.com \
    storage-api.googleapis.com \
    iam.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com

# Create service account for the application
echo -e "${YELLOW}Creating service account: ${SERVICE_ACCOUNT}...${NC}"
if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com" &>/dev/null; then
    gcloud iam service-accounts create "$SERVICE_ACCOUNT" \
        --display-name="Risk Alert Service Runner" \
        --description="Service account for running the risk alert service"
    echo -e "${GREEN}✓ Service account created${NC}"
else
    echo -e "${GREEN}✓ Service account already exists${NC}"
fi

# Grant GCS read permissions
echo -e "${YELLOW}Granting GCS permissions...${NC}"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer" \
    --condition=None

# Create scheduler service account
echo -e "${YELLOW}Creating scheduler service account...${NC}"
if ! gcloud iam service-accounts describe "${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" &>/dev/null; then
    gcloud iam service-accounts create "$SCHEDULER_SA" \
        --display-name="Cloud Scheduler Service Account" \
        --description="Service account for Cloud Scheduler to invoke Cloud Run"
    echo -e "${GREEN}✓ Scheduler service account created${NC}"
else
    echo -e "${GREEN}✓ Scheduler service account already exists${NC}"
fi

# Build and deploy to Cloud Run
echo -e "${YELLOW}Building and deploying to Cloud Run...${NC}"
echo "This may take 2-3 minutes..."

gcloud run deploy "$SERVICE_NAME" \
    --source . \
    --platform managed \
    --region "$REGION" \
    --service-account="${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --set-env-vars="SOURCE_URI=gs://fde-take-home/monthly_account_status.parquet,ARR_THRESHOLD=10000,REGION_CHANNEL_MAP={\"regions\":{\"AMER\":\"amer-risk-alerts\",\"EMEA\":\"emea-risk-alerts\",\"APAC\":\"apac-risk-alerts\"}}" \
    --max-instances=10 \
    --min-instances=0 \
    --timeout=600 \
    --memory=1Gi \
    --cpu=1 \
    --allow-unauthenticated

# Get the service URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format='value(status.url)')
echo -e "${GREEN}✓ Service deployed at: ${SERVICE_URL}${NC}"

# Grant Cloud Run invoker role to scheduler
echo -e "${YELLOW}Granting Cloud Run invoke permissions to scheduler...${NC}"
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --member="serviceAccount:${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/run.invoker" \
    --region="$REGION"

# Create Cloud Scheduler job
echo -e "${YELLOW}Creating Cloud Scheduler job...${NC}"

# Check if scheduler job exists
if gcloud scheduler jobs describe "$SCHEDULER_JOB_NAME" --location="$REGION" &>/dev/null; then
    echo -e "${YELLOW}Scheduler job already exists. Updating...${NC}"
    gcloud scheduler jobs update http "$SCHEDULER_JOB_NAME" \
        --location="$REGION" \
        --schedule="0 9 2 * *" \
        --time-zone="America/New_York" \
        --uri="${SERVICE_URL}/runs" \
        --http-method=POST \
        --headers="Content-Type=application/json" \
        --message-body="{\"source_uri\":\"gs://fde-take-home/monthly_account_status.parquet\",\"month\":\"auto\",\"dry_run\":false}" \
        --oidc-service-account-email="${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --oidc-token-audience="${SERVICE_URL}"
else
    gcloud scheduler jobs create http "$SCHEDULER_JOB_NAME" \
        --location="$REGION" \
        --schedule="0 9 2 * *" \
        --time-zone="America/New_York" \
        --uri="${SERVICE_URL}/runs" \
        --http-method=POST \
        --headers="Content-Type=application/json" \
        --message-body="{\"source_uri\":\"gs://fde-take-home/monthly_account_status.parquet\",\"month\":\"auto\",\"dry_run\":false}" \
        --oidc-service-account-email="${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --oidc-token-audience="${SERVICE_URL}" \
        --max-retry-attempts=3 \
        --max-retry-duration=1h \
        --min-backoff=60s \
        --max-backoff=600s \
        --attempt-deadline=10m
fi

echo -e "${GREEN}✓ Cloud Scheduler job created/updated${NC}"

# Test the deployment
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Testing Deployment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Test health endpoint
echo -e "${YELLOW}Testing health endpoint...${NC}"
HEALTH_RESPONSE=$(curl -s "${SERVICE_URL}/health")
if echo "$HEALTH_RESPONSE" | grep -q "ok"; then
    echo -e "${GREEN}✓ Health check passed${NC}"
    echo "Response: $HEALTH_RESPONSE"
else
    echo -e "${RED}✗ Health check failed${NC}"
    echo "Response: $HEALTH_RESPONSE"
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Deployment Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}✓ Service deployed and accessible at:${NC}"
echo "  $SERVICE_URL"
echo ""
echo -e "${GREEN}✓ Automatic scheduling configured:${NC}"
echo "  Schedule: 2nd of each month at 9:00 AM ET"
echo "  Job name: $SCHEDULER_JOB_NAME"
echo ""
echo -e "${YELLOW}Manual trigger options:${NC}"
echo ""
echo "1. Via API (recommended for testing):"
echo "   curl -X POST '${SERVICE_URL}/runs' \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"source_uri\":\"gs://fde-take-home/monthly_account_status.parquet\",\"month\":\"2026-01-01\",\"dry_run\":true}'"
echo ""
echo "2. Via Cloud Scheduler (test the scheduled job):"
echo "   gcloud scheduler jobs run $SCHEDULER_JOB_NAME --location=$REGION"
echo ""
echo "3. Via scheduler script:"
echo "   python scheduler.py --service-url $SERVICE_URL --month 2026-01-01 --dry-run"
echo ""
echo -e "${YELLOW}Monitoring:${NC}"
echo "  View logs: gcloud run services logs read $SERVICE_NAME --region=$REGION"
echo "  Scheduler logs: gcloud scheduler jobs describe $SCHEDULER_JOB_NAME --location=$REGION"
echo "  Cloud Console: https://console.cloud.google.com/run/detail/${REGION}/${SERVICE_NAME}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Configure Slack webhooks (set SLACK_WEBHOOK_BASE_URL env var)"
echo "  2. Test with a dry run"
echo "  3. Run a manual trigger for a past month to verify"
echo "  4. Wait for scheduled run on the 2nd of next month"
echo ""
