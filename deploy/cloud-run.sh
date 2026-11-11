#!/usr/bin/env bash
# Deploy the ANPR backend to Google Cloud Run, sized to stay within the free tier.
#
#   PROJECT_ID=my-project ./deploy/cloud-run.sh
#   PROJECT_ID=my-project SHEET_ID=<sheet id> ./deploy/cloud-run.sh   # log to Google Sheets
#
# Needs: gcloud CLI, logged in (`gcloud auth login`), and a project with billing
# enabled. No local Docker: Cloud Build builds the image from the Dockerfile.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-anpr-backend}"
SHEET_ID="${SHEET_ID:-}"
SOURCE_DIR="$(cd "$(dirname "$0")/../backend/anpr-server" && pwd)"

gcloud config set project "$PROJECT_ID" >/dev/null

echo "Enabling APIs..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com sheets.googleapis.com

ENV_VARS="SHEET_ID=${SHEET_ID}"
SA_FLAG=()
if [[ -n "$SHEET_ID" ]]; then
  # A dedicated identity for the service. Share the Sheet with its email
  # (Editor) and it can log without any key file.
  SA_EMAIL="anpr-logger@${PROJECT_ID}.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 || \
    gcloud iam service-accounts create anpr-logger --display-name "ANPR Sheets logger"
  SA_FLAG=(--service-account "$SA_EMAIL")
fi

echo "Building and deploying (first build takes ~10 minutes)..."
gcloud run deploy "$SERVICE" \
  --source "$SOURCE_DIR" \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 1 \
  --cpu-boost \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 4 \
  --timeout 60 \
  --set-env-vars "$ENV_VARS" \
  ${SA_FLAG[@]+"${SA_FLAG[@]}"}

# Keep only the newest image: Artifact Registry's free tier is 0.5 GB.
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location "$REGION" \
  --policy "$(dirname "$0")/cleanup-policy.json" --no-dry-run >/dev/null

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
echo
echo "Deployed: $URL"
echo "Health:   curl $URL/health"
echo "Mobile:   echo EXPO_PUBLIC_API_URL=$URL > mobile/.env"
if [[ -n "$SHEET_ID" ]]; then
  echo "Sheets:   share the sheet with $SA_EMAIL as Editor"
fi
