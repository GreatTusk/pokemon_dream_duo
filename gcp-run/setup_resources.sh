#!/usr/bin/env bash
# GCP ETL Full Setup & Deploy Script
# Usage: bash setup_resources.sh [gen9ou|gen9uu|...]

set -euo pipefail
export COLUMNS=200

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-east1}"
BUCKET_NAME="${BUCKET_NAME:-smogon-etl-${PROJECT_ID}}"
BQ_DATASET="${BQ_DATASET:-smogon_etl}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-smogon-etl-sa}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE_NAME="${IMAGE_NAME:-gcr.io/${PROJECT_ID}/smogon-etl}"
JOB_NAME="${JOB_NAME:-smogon-etl}"
FORMAT="${1:-gen9ou}"

echo "=== GCP ETL Full Setup & Deploy ==="
echo "Project:      ${PROJECT_ID}"
echo "Region:       ${REGION}"
echo "Bucket:       ${BUCKET_NAME}"
echo "BQ Dataset:   ${BQ_DATASET}"
echo "Service Acct: ${SERVICE_ACCOUNT}"
echo "Image:        ${IMAGE_NAME}"
echo "Format:       ${FORMAT}"
echo "=============================="

# 1. Set project
echo ">>> Setting project..."
gcloud config set project "${PROJECT_ID}"

# 2. Enable required APIs
echo ">>> Enabling APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    bigquery.googleapis.com \
    storage.googleapis.com \
    --project="${PROJECT_ID}"

# 3. Create service account (idempotent)
echo ">>> Creating service account..."
if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" \
        --project="${PROJECT_ID}" &>/dev/null; then
    gcloud iam service-accounts create "${SERVICE_ACCOUNT_NAME}" \
        --display-name="Smogon ETL Service Account" \
        --project="${PROJECT_ID}"
    echo "Created service account: ${SERVICE_ACCOUNT}"
else
    echo "Service account already exists: ${SERVICE_ACCOUNT}"
fi

# 4. Create Cloud Storage bucket (idempotent)
echo ">>> Creating Cloud Storage bucket..."
if gsutil ls "gs://${BUCKET_NAME}" &>/dev/null; then
    echo "Bucket already exists: gs://${BUCKET_NAME}"
else
    gsutil mb -l "${REGION}" "gs://${BUCKET_NAME}"
    gsutil uniformbucketlevelaccess set on "gs://${BUCKET_NAME}"
    echo "Created bucket: gs://${BUCKET_NAME}"
fi

# 5. Create BigQuery dataset (idempotent)
# FIX: use bq ls | grep instead of bq show, which doesn't support --exists_ok
echo ">>> Creating BigQuery dataset..."
if bq ls -d --project_id="${PROJECT_ID}" | grep -qw "${BQ_DATASET}"; then
    echo "Dataset already exists: ${PROJECT_ID}:${BQ_DATASET}"
else
    bq --location="${REGION}" mk --dataset \
        --project_id="${PROJECT_ID}" "${BQ_DATASET}"
    echo "Created dataset: ${PROJECT_ID}:${BQ_DATASET}"
fi

# 6. Grant project-level BigQuery job user
echo ">>> Granting IAM roles..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/bigquery.jobUser" \
    --condition=None \
    --quiet

# 7. Grant bucket-level storage.objectAdmin
gsutil iam ch \
    "serviceAccount:${SERVICE_ACCOUNT}:roles/storage.objectAdmin" \
    "gs://${BUCKET_NAME}"

# 8. Grant dataset-level permissions via project IAM (dataset-level IAM requires allowlisting)
echo ">>> Granting BigQuery dataEditor role at project level..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/bigquery.dataEditor" \
    --condition=None \
    --quiet

# 9. Build and push container image
echo ">>> Building and pushing container image..."
cd "$(dirname "$0")"
gcloud builds submit --tag "${IMAGE_NAME}" --project="${PROJECT_ID}"

# 10. Create or update Cloud Run job
echo ">>> Creating/updating Cloud Run job..."
if gcloud run jobs describe "${JOB_NAME}" \
        --region="${REGION}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "Job ${JOB_NAME} already exists, updating..."
    gcloud run jobs update "${JOB_NAME}" \
        --image="${IMAGE_NAME}" \
        --region="${REGION}" \
        --service-account="${SERVICE_ACCOUNT}" \
        --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION},BUCKET_NAME=${BUCKET_NAME},BQ_DATASET=${BQ_DATASET}" \
        --memory=4Gi --cpu=2 --task-timeout=3600
else
    gcloud run jobs create "${JOB_NAME}" \
        --image="${IMAGE_NAME}" \
        --region="${REGION}" \
        --service-account="${SERVICE_ACCOUNT}" \
        --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION},BUCKET_NAME=${BUCKET_NAME},BQ_DATASET=${BQ_DATASET}" \
        --memory=4Gi --cpu=2 --task-timeout=3600
fi

# 11. Execute the job
echo ">>> Executing Cloud Run job with format=${FORMAT}..."
gcloud run jobs execute "${JOB_NAME}" \
    --region="${REGION}" \
    --args="--format=${FORMAT}"

echo ""
echo "============================================"
echo "  ALL DONE - Pipeline running in Cloud Run! "
echo "============================================"
echo "Watch logs: gcloud run jobs executions list --job=${JOB_NAME} --region=${REGION}"