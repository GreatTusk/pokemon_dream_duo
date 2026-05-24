#!/usr/bin/env bash
# =============================================================================
# 04_create_composer.sh — Create Cloud Composer environment
# =============================================================================
set -euo pipefail

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${2:-us-central1}"
COMPOSER_ENV="${3:-smogon-composer-env}"
if [[ -z "$PROJECT_ID" ]]; then
  echo "Usage: $0 <project-id> [region] [env-name]"
  exit 1
fi

echo "=== Creating Cloud Composer environment: $COMPOSER_ENV ==="

gcloud composer environments create "$COMPOSER_ENV" \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --image-version="composer-2-airflow-2" \
  --node-count=3 \
  --machine-type="n1-standard-4" \
  --disk-size=50 \
  --env-variables="PROJECT_ID=${PROJECT_ID},RAW_BUCKET=smogon-raw-${PROJECT_ID},DAGS_BUCKET=smogon-dags-${PROJECT_ID}" \
  --network="default" \
  --subnetwork="default"

echo "=== Composer environment created ==="
echo "  Name: $COMPOSER_ENV"
echo "  Location: $REGION"

# Output the DAGs folder path
echo ""
echo "Deploy DAGs to:"
echo "  gcloud composer environments storage dags import \\"
echo "    --environment=$COMPOSER_ENV --location=$REGION \\"
echo "    --source=./dags/"
