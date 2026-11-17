#!/usr/bin/env bash
# Deploy the ANPR backend to Google Cloud Run, sized to stay within the free tier.
#
#   PROJECT_ID=my-project ./deploy/cloud-run.sh            # demo: plates go to the log
#
# Log to the yard audit in your own Drive (see the README's Google Sheets setup):
#
#   PROJECT_ID=my-project SHEETS_FOLDER_ID=<folder id> SHEETS_TEMPLATE_ID=<template id> \
#     SHEET_TITLE_PREFIX="Physical report" OAUTH_TOKEN_FILE=backend/google-token.json \
#     ./deploy/cloud-run.sh
#
# The sign-in (OAUTH_TOKEN_FILE) and the app's API key are kept in Secret
# Manager, not in env vars. API_KEY=<key> sets the key; otherwise one is made
# on the first Sheets deploy and kept.
#
# Needs: gcloud CLI, logged in (`gcloud auth login`), and a project with billing
# enabled. No local Docker: Cloud Build builds the image from the Dockerfile.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-anpr-backend}"
SHEET_ID="${SHEET_ID:-}"
SHEETS_FOLDER_ID="${SHEETS_FOLDER_ID:-}"
SHEETS_TEMPLATE_ID="${SHEETS_TEMPLATE_ID:-}"
SHEET_TITLE_PREFIX="${SHEET_TITLE_PREFIX:-Yard audit}"
OAUTH_TOKEN_FILE="${OAUTH_TOKEN_FILE:-}"
API_KEY="${API_KEY:-}"
SOURCE_DIR="$(cd "$(dirname "$0")/../backend/anpr-server" && pwd)"

gcloud config set project "$PROJECT_ID" >/dev/null

echo "Enabling APIs..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com sheets.googleapis.com drive.googleapis.com \
  secretmanager.googleapis.com

# On newer projects the default build service account starts without
# permissions; source deploys need it to hold the Cloud Run Builder role.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role roles/run.builder --condition None >/dev/null

# ^ separates the values, since the title prefix may contain commas
ENV_VARS="^|^SHEET_ID=${SHEET_ID}|SHEETS_FOLDER_ID=${SHEETS_FOLDER_ID}|SHEETS_TEMPLATE_ID=${SHEETS_TEMPLATE_ID}|SHEET_TITLE_PREFIX=${SHEET_TITLE_PREFIX}"
SA_FLAG=()
SECRETS_FLAG=()
if [[ -n "$SHEET_ID" || -n "$SHEETS_FOLDER_ID" ]]; then
  # A dedicated identity for the service: it reads the secrets below, or
  # (with SHEET_ID and no sign-in) is shared on the Sheet as Editor itself.
  SA_EMAIL="anpr-logger@${PROJECT_ID}.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 || \
    gcloud iam service-accounts create anpr-logger --display-name "ANPR Sheets logger"
  SA_FLAG=(--service-account "$SA_EMAIL")

  # Store a value in Secret Manager (a new version only if it changed) and
  # let the service read it
  put_secret() {
    local name="$1" file="$2"
    if ! gcloud secrets describe "$name" >/dev/null 2>&1; then
      gcloud secrets create "$name" --replication-policy automatic --data-file "$file" >/dev/null
    elif ! cmp -s "$file" <(gcloud secrets versions access latest --secret "$name"); then
      gcloud secrets versions add "$name" --data-file "$file" >/dev/null
    fi
    gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:$SA_EMAIL" \
      --role roles/secretmanager.secretAccessor --condition None >/dev/null
  }

  SECRETS="API_KEY=fleetscan-api-key:latest"
  KEY_FILE="$(mktemp)"
  trap 'rm -f "$KEY_FILE"' EXIT
  if [[ -n "$API_KEY" ]]; then
    printf '%s' "$API_KEY" > "$KEY_FILE"
    put_secret fleetscan-api-key "$KEY_FILE"
  elif ! gcloud secrets describe fleetscan-api-key >/dev/null 2>&1; then
    openssl rand -hex 24 | tr -d '\n' > "$KEY_FILE"
    put_secret fleetscan-api-key "$KEY_FILE"
  fi

  if [[ -n "$OAUTH_TOKEN_FILE" ]]; then
    put_secret fleetscan-google-token "$OAUTH_TOKEN_FILE"
    SECRETS="${SECRETS},GOOGLE_OAUTH_TOKEN_JSON=fleetscan-google-token:latest"
  elif [[ -n "$SHEETS_FOLDER_ID" ]]; then
    echo "SHEETS_FOLDER_ID needs OAUTH_TOKEN_FILE: a service account can't create files in your Drive." >&2
    exit 1
  fi
  SECRETS_FLAG=(--set-secrets "$SECRETS")
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
  ${SA_FLAG[@]+"${SA_FLAG[@]}"} \
  ${SECRETS_FLAG[@]+"${SECRETS_FLAG[@]}"}

# Keep only the newest image: Artifact Registry's free tier is 0.5 GB.
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location "$REGION" \
  --policy "$(dirname "$0")/cleanup-policy.json" --no-dry-run >/dev/null

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
echo
echo "Deployed: $URL"
echo "Health:   curl $URL/health"
echo "Mobile:   echo EXPO_PUBLIC_API_URL=$URL > mobile/.env"
if [[ -n "$SHEET_ID" && -z "$OAUTH_TOKEN_FILE" ]]; then
  echo "Sheets:   share the sheet with $SA_EMAIL as Editor"
fi
if [[ ${#SECRETS_FLAG[@]} -gt 0 ]]; then
  echo "API key:  echo EXPO_PUBLIC_API_KEY=\$(gcloud secrets versions access latest --secret fleetscan-api-key) >> mobile/.env"
fi
