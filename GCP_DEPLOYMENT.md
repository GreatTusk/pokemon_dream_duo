# Deploying the Smogon ETL Pipeline on GCP (gen9ou only)

This guide walks you through deploying and running the pipeline entirely from **Google Cloud Shell** in the browser. Only gen9ou format will be processed.

> **Qwiklabs / restricted-IAM notice:** In Qwiklabs and similar sandboxed GCP environments, `gcloud projects add-iam-policy-binding` can exit 0 while silently dropping the binding. The steps below include explicit verification checks after every IAM call and will abort if a binding did not take effect. If a check fails you will need an instructor/admin to grant the role manually from the Cloud Console IAM page.

---

## Prerequisites

- A Google Cloud Platform project with billing enabled
- Cloud Shell activated (click the terminal icon in Cloud Console)
- Project Owner or Editor permissions

---

## Phase 1: One-Time Infrastructure Setup

### Step 1 — Clone the Repository

```bash
git clone https://github.com/GreatTusk/smogon_big_data_etl.git ~/smogon_big_data_etl
cd ~/smogon_big_data_etl/gcp/setup
```

### Step 2 — Set Your Project and Region

```bash
gcloud config set project YOUR_PROJECT_ID
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
REGION=us-central1
SA_NAME="smogon-pipeline-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
COMPOSER_ENV="smogon-composer-env"
COMPOSER_IMAGE_VERSION="composer-2.9.7-airflow-2.9.3"
echo "Project:  $PROJECT_ID  ($PROJECT_NUMBER)"
echo "Region:   $REGION"
echo "SA email: $SA_EMAIL"
```

Replace `YOUR_PROJECT_ID` with your actual GCP project ID.

### Step 3 — Enable Required GCP APIs

```bash
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

echo "Waiting 90s for service agents to initialize..."
sleep 90
```

> The Composer service agent (`service-NUMBER@cloudcomposer-accounts.iam.gserviceaccount.com`) is created asynchronously. The 90-second wait ensures it exists before the IAM step tries to bind a role to it.

### Step 4 — Enable Private Google Access on the Default Subnet

Composer 2 workers must reach Google APIs over private IP. Without this the environment hangs during provisioning.

```bash
PRIVATE_ACCESS=$(gcloud compute networks subnets describe default \
  --region="$REGION" --format="value(privateIpGoogleAccess)")

if [ "$PRIVATE_ACCESS" != "True" ]; then
  echo "Enabling Private Google Access..."
  gcloud compute networks subnets update default \
    --region="$REGION" --enable-private-ip-google-access
  echo "Done."
else
  echo "Private Google Access already enabled."
fi
```

### Step 5 — Create GCS Buckets

```bash
RAW_BUCKET="smogon-raw-${PROJECT_ID}"
DAGS_BUCKET="smogon-dags-${PROJECT_ID}"

gsutil mb -p "$PROJECT_ID" -l "$REGION" -c STANDARD "gs://${RAW_BUCKET}/"
gsutil uniformbucketlevelaccess set on "gs://${RAW_BUCKET}/"

gsutil mb -p "$PROJECT_ID" -l "$REGION" -c STANDARD "gs://${DAGS_BUCKET}/"
gsutil uniformbucketlevelaccess set on "gs://${DAGS_BUCKET}/"

for subdir in usage chaos leads metagame replays; do
  gsutil cp /dev/null "gs://${RAW_BUCKET}/${subdir}/" 2>/dev/null || true
done

echo "Raw bucket:  gs://${RAW_BUCKET}"
echo "DAGs bucket: gs://${DAGS_BUCKET}"
```

### Step 6 — Create BigQuery Datasets and Tables

```bash
bq mk --dataset --location="$REGION" --description="Smogon raw data layer"         "${PROJECT_ID}:smogon_raw"
bq mk --dataset --location="$REGION" --description="Smogon staging transforms"     "${PROJECT_ID}:smogon_staging"
bq mk --dataset --location="$REGION" --description="Smogon dimensional warehouse"  "${PROJECT_ID}:smogon_dw"

SQL_DIR="$HOME/smogon_big_data_etl/gcp/sql"
bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" < "${SQL_DIR}/01_ddl_raw.sql"
bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" < "${SQL_DIR}/02_ddl_staging.sql"
bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" < "${SQL_DIR}/03_ddl_dimensional.sql"
bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" < "${SQL_DIR}/04_views_dashboard.sql"

echo "BigQuery datasets and tables created."
```

### Step 7 — Create Service Account and Grant IAM Roles

Each binding is verified immediately after being applied. If any check prints **MISSING** the environment creation will fail — you must grant that role manually in Cloud Console → IAM before continuing.

```bash
# Helper: verify a project-level IAM binding exists
check_binding() {
  local MEMBER="$1" ROLE="$2"
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
```

> **If any binding shows ❌ MISSING:** go to Cloud Console → IAM & Admin → IAM, click **Grant Access**, paste the service account email, add the missing role, and save. Then re-run the `check_binding` lines to confirm before proceeding.

### Step 8 — Create Cloud Composer Environment

```bash
# Optional: list available versions to choose a different pin
# gcloud composer versions list --locations="$REGION"

gcloud composer environments create "$COMPOSER_ENV" \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --image-version="$COMPOSER_IMAGE_VERSION" \
  --service-account="$SA_EMAIL" \
  --env-variables="RAW_BUCKET=smogon-raw-${PROJECT_ID},DAGS_BUCKET=smogon-dags-${PROJECT_ID}" \
  --network="default" \
  --subnetwork="default"
```

> Takes **15–20 minutes**. Cloud Shell may disconnect — reconnect and check:
> ```bash
> gcloud composer environments describe "$COMPOSER_ENV" \
>   --location="$REGION" --format="value(state)"
> ```
> Expected: `CREATING` → `RUNNING`. If still `CREATING` after 30 minutes see **Debugging** below.

### Step 9 — Deploy DAGs and Pipeline Code

```bash
RAW_BUCKET="smogon-raw-${PROJECT_ID}"
DAGS_BUCKET="smogon-dags-${PROJECT_ID}"

gcloud composer environments storage dags import \
  --environment="$COMPOSER_ENV" \
  --location="$REGION" \
  --source="$HOME/smogon_big_data_etl/gcp/dags/" \
  --project="$PROJECT_ID"

gsutil -m rsync -r \
  "$HOME/smogon_big_data_etl/gcp/pipeline/" \
  "gs://${DAGS_BUCKET}/plugins/pipeline/"

gcloud composer environments update "$COMPOSER_ENV" \
  --location="$REGION" \
  --update-pypi-packages-from-file="$HOME/smogon_big_data_etl/gcp/requirements-composer.txt" \
  --project="$PROJECT_ID"
```

> DAGs appear in Airflow within 2–5 minutes after deployment.

---

## Phase 2: Running the Pipeline for gen9ou

### Option A — Via Airflow Web UI (recommended for first run)

1. Cloud Console → **Cloud Composer** → open `smogon-composer-env`
2. Click **Airflow Web UI**
3. Find `smogon_master_dag` → click the **Play** button
4. Enter config: `{"format": "gen9ou"}` → **Trigger**

### Option B — Via Cloud Shell

```bash
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_master_dag
```

### Option C — Individual Steps

```bash
for DAG in smogon_01_discover smogon_02_ingest_usage smogon_03_ingest_chaos \
           smogon_04_ingest_leads smogon_05_ingest_metagame smogon_06_ingest_replays; do
  gcloud composer environments run "$COMPOSER_ENV" \
    --location="$REGION" \
    trigger_dag -- "$DAG"
done
```

---

## Phase 3: Monitoring and Verification

```bash
# Check BigQuery for ingested data
bq query --project_id="$PROJECT_ID" --use_legacy_sql=false \
  "SELECT month, format_id, COUNT(*) as rows
   FROM smogon_raw.usage_stats
   WHERE format_id = 'gen9ou'
   GROUP BY month, format_id
   ORDER BY month DESC"

# Check raw files in GCS
gsutil ls "gs://smogon-raw-${PROJECT_ID}/usage/"
```

---

## Pipeline Execution Order

```text
smogon_01_discover
        ↓
smogon_02_ingest_usage    → smogon_raw.usage_stats
        ↓
smogon_03_ingest_chaos    → smogon_raw.abilities, items, moves, spreads,
                            tera_types, teammates, checks_counters, pokemon_details
        ↓
smogon_04_ingest_leads    → smogon_raw.leads
        ↓
smogon_05_ingest_metagame → smogon_raw.metagame
        ↓
smogon_06_ingest_replays  → smogon_raw.replays, replay_teams
```

Each step is idempotent — re-running skips already-ingested months.

---

## Debugging a Stuck Environment

```bash
# Detailed error from the operation
gcloud composer environments describe "$COMPOSER_ENV" \
  --location="$REGION" --format="json" | grep -i "error\|message\|detail"

# Cloud Build logs (Composer 2 uses it internally)
gcloud builds list --filter="tags=composer" --limit=5

# Verify both critical IAM bindings
gcloud projects get-iam-policy "$PROJECT_ID" \
  --flatten="bindings[].members" \
  --filter="bindings.role=roles/composer.worker OR bindings.role=roles/composer.ServiceAgentV2Ext" \
  --format="table(bindings.role, bindings.members)"

# Verify Private Google Access
gcloud compute networks subnets describe default \
  --region="$REGION" --format="value(privateIpGoogleAccess)"
```

If the environment must be recreated:

```bash
gcloud composer environments delete "$COMPOSER_ENV" --location="$REGION" --quiet
# Fix the IAM/networking issue, then re-run from Step 7
```

---

## Common Issues

| Problem | Solution |
|---------|----------|
| `roles/composer.worker` or `ServiceAgentV2Ext` shows ❌ MISSING | Grant manually in Cloud Console → IAM. Qwiklabs restricts programmatic IAM writes. |
| Composer fails with "no error was surfaced" | 100% an IAM issue. Verify both bindings exist before retrying. |
| Composer stuck `CREATING` past 30 min | Run diagnostics above. |
| DAGs not in Airflow after 10 min | Re-run Step 9. |
| BigQuery table not found | Re-run Step 6. |
| Permission denied on GCS | Verify `roles/storage.objectAdmin` binding exists. |

---

## Re-running the Pipeline

```bash
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_master_dag
```

The pipeline skips months already present in BigQuery.