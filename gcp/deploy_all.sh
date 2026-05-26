#!/usr/bin/env bash
# smogon_setup.sh — Full one-shot infrastructure setup for the Smogon ETL pipeline.
# Run from Google Cloud Shell:  bash smogon_setup.sh
# Requires: Project Owner or Editor. In Qwiklabs, some IAM bindings must be
# granted manually — the script will tell you exactly which ones and abort.

set -euo pipefail

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
SA_NAME="smogon-pipeline-sa"
COMPOSER_ENV="smogon-composer-env"
COMPOSER_IMAGE_VERSION="composer-2.9.7-airflow-2.9.3"
REPO_DIR="$HOME/smogon_big_data_etl"
# ─────────────────────────────────────────────────────────────────────────────

# Colours
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ─── PREFLIGHT ───────────────────────────────────────────────────────────────
[ -z "$PROJECT_ID" ] && die "PROJECT_ID is not set. Run: gcloud config set project YOUR_PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null) \
  || die "Cannot describe project '$PROJECT_ID'. Check your credentials and project ID."

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
COMPOSER_AGENT="service-${PROJECT_NUMBER}@cloudcomposer-accounts.iam.gserviceaccount.com"
RAW_BUCKET="smogon-raw-${PROJECT_ID}"
DAGS_BUCKET="smogon-dags-${PROJECT_ID}"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Smogon ETL — Infrastructure Setup"
echo "  Project : $PROJECT_ID  ($PROJECT_NUMBER)"
echo "  Region  : $REGION"
echo "  SA      : $SA_EMAIL"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ─── HELPER: verify a project-level IAM binding exists ────────────────────────
# Returns 0 if present, 1 if missing.
check_binding() {
  local MEMBER="$1" ROLE="$2"
  local FOUND
  FOUND=$(gcloud projects get-iam-policy "$PROJECT_ID" \
    --flatten="bindings[].members" \
    --filter="bindings.role=${ROLE} AND bindings.members=${MEMBER}" \
    --format="value(bindings.members)" 2>/dev/null)
  if [ -z "$FOUND" ]; then
    echo "  ❌ MISSING  ${ROLE}  →  ${MEMBER}"
    return 1
  else
    echo "  ✅ OK       ${ROLE}  →  ${MEMBER}"
    return 0
  fi
}

# ─── STEP 1: Clone repo ──────────────────────────────────────────────────────
info "Step 1 — Cloning repository..."
if [ -d "$REPO_DIR/.git" ]; then
  ok "Repository already cloned at $REPO_DIR"
else
  git clone https://github.com/GreatTusk/smogon_big_data_etl.git "$REPO_DIR"
  ok "Cloned to $REPO_DIR"
fi

# ─── STEP 2: Enable APIs ─────────────────────────────────────────────────────
info "Step 2 — Enabling GCP APIs..."
gcloud services enable \
  composer.googleapis.com \
  bigquery.googleapis.com \
  bigqueryconnection.googleapis.com \
  storage.googleapis.com \
  storage-api.googleapis.com \
  cloudbuild.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  --project="$PROJECT_ID" --quiet
ok "APIs enabled."

info "Waiting 90s for service agents to initialize..."
sleep 30

# ─── STEP 3: Private Google Access ───────────────────────────────────────────
info "Step 3 — Checking Private Google Access on default subnet..."
PRIVATE_ACCESS=$(gcloud compute networks subnets describe default \
  --region="$REGION" --format="value(privateIpGoogleAccess)" 2>/dev/null || echo "False")
if [ "$PRIVATE_ACCESS" != "True" ]; then
  info "Enabling Private Google Access..."
  gcloud compute networks subnets update default \
    --region="$REGION" --enable-private-ip-google-access
  ok "Private Google Access enabled."
else
  ok "Private Google Access already enabled."
fi

# ─── STEP 4: GCS buckets ─────────────────────────────────────────────────────
info "Step 4 — Creating GCS buckets..."

create_bucket() {
  local BUCKET="$1"
  if gsutil ls -b "gs://${BUCKET}/" &>/dev/null; then
    ok "Bucket gs://${BUCKET}/ already exists."
  else
    gsutil mb -p "$PROJECT_ID" -l "$REGION" -c STANDARD "gs://${BUCKET}/"
    gsutil uniformbucketlevelaccess set on "gs://${BUCKET}/"
    ok "Created gs://${BUCKET}/"
  fi
}

create_bucket "$RAW_BUCKET"
create_bucket "$DAGS_BUCKET"

for subdir in usage chaos leads metagame replays; do
  gsutil cp /dev/null "gs://${RAW_BUCKET}/${subdir}/" 2>/dev/null || true
done
ok "Bucket subdirectory placeholders created."

# ─── STEP 5: BigQuery ────────────────────────────────────────────────────────
info "Step 5 — Creating BigQuery datasets and tables..."

create_dataset() {
  local DS="$1" DESC="$2"
  if bq ls --project_id="$PROJECT_ID" "$DS" &>/dev/null; then
    ok "Dataset $DS already exists."
  else
    bq mk --dataset --location="$REGION" --description="$DESC" "${PROJECT_ID}:${DS}"
    ok "Created dataset $DS."
  fi
}

create_dataset "smogon_raw"     "Smogon raw data layer"
create_dataset "smogon_staging" "Smogon staging transforms"
create_dataset "smogon_dw"      "Smogon dimensional warehouse"

SQL_DIR="$REPO_DIR/gcp/sql"
for SQL_FILE in 01_ddl_raw.sql 02_ddl_staging.sql 03_ddl_dimensional.sql 04_views_dashboard.sql; do
  info "  Running $SQL_FILE ..."
  bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" \
    < "${SQL_DIR}/${SQL_FILE}"
done
ok "BigQuery schema applied."

# ─── STEP 6: Service account + IAM ───────────────────────────────────────────
info "Step 6 — Creating service account and IAM bindings..."

# Create the service account (idempotent — ignore already-exists error)
gcloud iam service-accounts create "$SA_NAME" \
  --project="$PROJECT_ID" \
  --display-name="Smogon ETL Pipeline Service Account" 2>/dev/null || true

# Roles for the pipeline service account
SA_ROLES=(
  roles/composer.worker
  roles/bigquery.dataEditor
  roles/bigquery.jobUser
  roles/storage.objectAdmin
  roles/storage.objectViewer
  roles/logging.logWriter
  roles/monitoring.metricWriter
)

echo ""
echo "Binding roles to $SA_EMAIL ..."
for ROLE in "${SA_ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" --quiet 2>/dev/null
  check_binding "serviceAccount:${SA_EMAIL}" "$ROLE" \
    || echo "    → Grant this manually in Cloud Console → IAM"
done

# Role for the Composer service agent (project-level, not SA-resource-level)
COMPOSER_AGENT="service-${PROJECT_NUMBER}@cloudcomposer-accounts.iam.gserviceaccount.com"
echo ""
echo "Binding roles/composer.ServiceAgentV2Ext to Composer service agent ..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${COMPOSER_AGENT}" \
  --role="roles/composer.ServiceAgentV2Ext" --quiet 2>/dev/null
check_binding "serviceAccount:${COMPOSER_AGENT}" "roles/composer.ServiceAgentV2Ext" \
  || echo "    → Grant this manually in Cloud Console → IAM"

echo ""
echo "If any binding shows ❌ MISSING: grant it manually in Cloud Console → IAM & Admin → IAM, then re-run check_binding to confirm."

# ─── STEP 7: Composer environment ────────────────────────────────────────────
info "Step 7 — Creating Cloud Composer environment (this takes 15–20 minutes)..."
info "Image version: $COMPOSER_IMAGE_VERSION"

if gcloud composer environments describe "$COMPOSER_ENV" \
     --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  warn "Composer environment '$COMPOSER_ENV' already exists — skipping creation."
else
  gcloud composer environments create "$COMPOSER_ENV" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --image-version="$COMPOSER_IMAGE_VERSION" \
    --service-account="$SA_EMAIL" \
    --env-variables="PROJECT_ID=${PROJECT_ID},RAW_BUCKET=${RAW_BUCKET},DAGS_BUCKET=${DAGS_BUCKET}" \
    --network="default" \
    --subnetwork="default"
fi

# Poll until RUNNING or ERROR
info "Waiting for Composer environment to reach RUNNING state..."
MAX_WAIT=1800  # 30 minutes
ELAPSED=0
INTERVAL=30
while true; do
  STATE=$(gcloud composer environments describe "$COMPOSER_ENV" \
    --location="$REGION" --project="$PROJECT_ID" \
    --format="value(state)" 2>/dev/null || echo "UNKNOWN")
  info "  State: $STATE  (${ELAPSED}s elapsed)"
  if [ "$STATE" = "RUNNING" ]; then
    ok "Composer environment is RUNNING."
    break
  elif [ "$STATE" = "ERROR" ]; then
    die "Composer environment entered ERROR state. Run: gcloud composer environments describe $COMPOSER_ENV --location=$REGION --format=json"
  elif [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
    die "Timed out after ${MAX_WAIT}s. Check Cloud Console → Composer for details."
  fi
  sleep $INTERVAL
  ELAPSED=$((ELAPSED + INTERVAL))
done

# ─── STEP 8: Deploy DAGs and pipeline code ───────────────────────────────────
info "Step 8 — Deploying DAGs..."
gcloud composer environments storage dags import \
  --environment="$COMPOSER_ENV" \
  --location="$REGION" \
  --source="$REPO_DIR/gcp/dags/" \
  --project="$PROJECT_ID"
ok "DAGs imported."

info "Syncing pipeline modules to plugins folder..."
gsutil -m rsync -r \
  "$REPO_DIR/gcp/pipeline/" \
  "gs://${DAGS_BUCKET}/plugins/pipeline/"
ok "Pipeline modules synced."

info "Installing PyPI packages in Composer (takes ~5 minutes)..."
gcloud composer environments update "$COMPOSER_ENV" \
  --location="$REGION" \
  --update-pypi-packages-from-file="$REPO_DIR/gcp/requirements-composer.txt" \
  --project="$PROJECT_ID"
ok "Packages installed."

# ─── DONE ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo ""
echo "  To run the pipeline:"
echo "    gcloud composer environments run $COMPOSER_ENV \\"
echo "      --location=$REGION trigger_dag -- smogon_master_dag"
echo ""
echo "  Or open the Airflow UI and trigger smogon_master_dag"
echo "  with config:  {\"format\": \"gen9ou\"}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
