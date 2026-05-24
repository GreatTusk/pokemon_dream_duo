# Deploying the Smogon ETL Pipeline on GCP (gen9ou only)

This guide walks you through deploying and running the pipeline entirely from **Google Cloud Shell** in the browser. Only gen9ou format will be processed.

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

Replace `YOUR_USERNAME` with your actual GitHub username.

### Step 2 — Set Your Project and Region

```bash
gcloud config set project YOUR_PROJECT_ID
PROJECT_ID=$(gcloud config get-value project)
REGION=us-central1
echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
```

Replace `YOUR_PROJECT_ID` with your actual GCP project ID.

---

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
```

---

### Step 4 — Create GCS Buckets

```bash
PROJECT_ID=$(gcloud config get-value project)
RAW_BUCKET="smogon-raw-${PROJECT_ID}"
DAGS_BUCKET="smogon-dags-${PROJECT_ID}"
REGION=us-central1

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

---

### Step 6 — Create BigQuery Datasets and Tables

```bash
bq mk --dataset --location="$REGION" --description="Smogon raw data layer" "${PROJECT_ID}:smogon_raw"
bq mk --dataset --location="$REGION" --description="Smogon staging transforms" "${PROJECT_ID}:smogon_staging"
bq mk --dataset --location="$REGION" --description="Smogon dimensional warehouse" "${PROJECT_ID}:smogon_dw"

SQL_DIR="$HOME/smogon_big_data_etl/gcp/sql"

bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" < "${SQL_DIR}/01_ddl_raw.sql"
bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" < "${SQL_DIR}/02_ddl_staging.sql"
bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" < "${SQL_DIR}/03_ddl_dimensional.sql"
bq query --use_legacy_sql=false --project_id="$PROJECT_ID" --location="$REGION" < "${SQL_DIR}/04_views_dashboard.sql"

echo "BigQuery datasets created: smogon_raw, smogon_staging, smogon_dw"
```

---

### Step 7 — Create Cloud Composer Environment

```bash
PROJECT_ID=$(gcloud config get-value project)
COMPOSER_ENV="smogon-composer-env"

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

echo "Composer environment created: $COMPOSER_ENV"
```

> This takes **15–20 minutes** to provision. Cloud Shell may disconnect — that's fine. Reconnect and check status with:
> ```bash
> gcloud composer environments describe "$COMPOSER_ENV" --location="$REGION"
> ```

---

### Step 8 — Create Service Account and IAM

```bash
SA_NAME="smogon-pipeline-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create "$SA_NAME" \
  --project="$PROJECT_ID" \
  --display-name="Smogon ETL Pipeline Service Account"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.dataEditor" --quiet

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.jobUser" --quiet

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin" --quiet

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectViewer" --quiet

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/composer.worker" --quiet

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/logging.logWriter" --quiet

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/monitoring.metricWriter" --quiet

echo "Service account: $SA_EMAIL"
```

---

### Step 9 — Assign Service Account to Composer

```bash
gcloud composer environments update "$COMPOSER_ENV" \
  --location="$REGION" \
  --service-account="$SA_EMAIL"
```

---

### Step 10 — Deploy DAGs and Pipeline Code

```bash
DAGS_BUCKET="smogon-dags-${PROJECT_ID}"

gcloud composer environments storage dags import \
  --environment="$COMPOSER_ENV" \
  --location="$REGION" \
  --source="$HOME/smogon_big_data_etl/gcp/dags/" \
  --project="$PROJECT_ID"
```

Upload pipeline modules to the plugins folder:

```bash
PYTHON_PLUGINS_DEST="gs://${DAGS_BUCKET}/plugins/pipeline/"

gsutil -m rsync -r "$HOME/smogon_big_data_etl/gcp/pipeline/" "$PYTHON_PLUGINS_DEST"
```

Install custom PyPI packages in Composer:

```bash
gcloud composer environments update "$COMPOSER_ENV" \
  --location="$REGION" \
  --update-pypi-packages-from-file="$HOME/smogon_big_data_etl/gcp/requirements-composer.txt" \
  --project="$PROJECT_ID"
```

> Installing packages takes ~5 minutes. DAGs will appear in Airflow within 2–5 minutes after deployment.

---

## Phase 2: Running the Pipeline for gen9ou

### Option A — Via Airflow Web UI (Recommended for first run)

1. Go to Cloud Console → search **"Cloud Composer"** → open `smogon-composer-env`
2. Click **"Airflow Web UI"** (opens a new tab)
3. Find the `smogon_master_dag` in the list
4. Click the **"Play" button** (trigger)
5. In the pop-up dialog, enter the configuration:

```json
{"format": "gen9ou"}
```

6. Click **Trigger**
7. Click on the DAG name to watch the task tree in real time

---

### Option B — Via Cloud Shell Command Line

```bash
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_master_dag
```

---

### Option C — Run Individual Steps (gen9ou only)

If you want to run steps one at a time (useful for debugging):

```bash
# Step 1: Discover available data
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_01_discover

# Step 2: Ingest usage stats
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_02_ingest_usage

# Step 3: Ingest chaos JSON
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_03_ingest_chaos

# Step 4: Ingest leads
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_04_ingest_leads

# Step 5: Ingest metagame
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_05_ingest_metagame

# Step 6: Ingest replays
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  trigger_dag -- smogon_06_ingest_replays
```

---

## Phase 3: Monitoring and Verification

### Check DAG Status

```bash
gcloud composer environments run "$COMPOSER_ENV" \
  --location="$REGION" \
  list_dags
```

### View Airflow Logs

1. Open Airflow Web UI
2. Click a task → click **"Log"** to see detailed output

### Check BigQuery for Ingested Data

```bash
bq query --project_id="$PROJECT_ID" --use_legacy_sql=false \
  "SELECT month, format_id, COUNT(*) as rows
   FROM smogon_raw.usage_stats
   WHERE format_id = 'gen9ou'
   GROUP BY month, format_id
   ORDER BY month DESC"
```

### Check GCS for Raw Files

```bash
gsutil ls "gs://smogon-raw-${PROJECT_ID}/usage/"
```

---

## Pipeline Execution Order

When triggering `smogon_master_dag` with `{"format": "gen9ou"}`, tasks run in this order:

```
smogon_01_discover
        ↓
smogon_02_ingest_usage  → smogon_raw.usage_stats
        ↓
smogon_03_ingest_chaos  → smogon_raw.abilities, items, moves, spreads,
                          tera_types, teammates, checks_counters, pokemon_details
        ↓
smogon_04_ingest_leads  → smogon_raw.leads
        ↓
smogon_05_ingest_metagame → smogon_raw.metagame
        ↓
smogon_06_ingest_replays → smogon_raw.replays, replay_teams
```

Each step is idempotent — re-running skips already-ingested months.

---

## Common Issues

| Problem | Solution |
|---------|----------|
| Composer environment is "CREATING" for 20+ min | Normal — wait and re-check with `gcloud composer environments describe` |
| DAGs not appearing in Airflow UI | Wait 5 min after deploy. If still missing, re-run step 8. |
| "Service account not found" error | Run step 7 to assign the SA to the Composer environment |
| BigQuery table not found | Run step 4 to execute all DDL scripts |
| Permission denied on GCS | Verify step 6 IAM bindings were applied correctly |

---

## Re-running the Pipeline

For subsequent monthly runs, you only need to:

1. Trigger the master DAG from Airflow UI (pass `{"format": "gen9ou"}`)
2. Or run from Cloud Shell:
   ```bash
   gcloud composer environments run "$COMPOSER_ENV" \
     --location="$REGION" \
     trigger_dag -- smogon_master_dag
   ```

The pipeline will automatically skip months that already exist in BigQuery.