import logging
import tempfile
from google.cloud import storage

from .config import BUCKET_NAME, RUN_ID

logger = logging.getLogger(__name__)


class StorageClient:
    def __init__(self, bucket_name=None, run_id=None):
        self.client = storage.Client()
        self.bucket_name = bucket_name or BUCKET_NAME
        self.run_id = run_id or RUN_ID
        self._bucket = self.client.bucket(self.bucket_name)

    @property
    def raw_prefix(self):
        return f"raw/{self.run_id}"

    @property
    def staged_prefix(self):
        return f"staged/{self.run_id}"

    @property
    def results_prefix(self):
        return f"results/{self.run_id}"

    def upload_text(self, text, blob_name, content_type="text/plain"):
        blob = self._bucket.blob(blob_name)
        blob.upload_from_string(text, content_type=content_type)
        logger.debug("Uploaded %s bytes to gs://%s/%s", len(text), self.bucket_name, blob_name)
        return f"gs://{self.bucket_name}/{blob_name}"

    def upload_bytes(self, data, blob_name, content_type="application/octet-stream"):
        blob = self._bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self.bucket_name}/{blob_name}"

    def download_text(self, blob_name):
        blob = self._bucket.blob(blob_name)
        return blob.download_as_text()

    def download_bytes(self, blob_name):
        blob = self._bucket.blob(blob_name)
        return blob.download_as_bytes()

    def exists(self, blob_name):
        blob = self._bucket.blob(blob_name)
        return blob.exists()

    def upload_file(self, local_path, blob_name):
        blob = self._bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        return f"gs://{self.bucket_name}/{blob_name}"

    def list_blobs(self, prefix):
        return list(self.client.list_blobs(self._bucket, prefix=prefix))

    def list_raw_blobs(self, source_type):
        prefix = f"{self.raw_prefix}/{source_type}"
        return list(self.client.list_blobs(self._bucket, prefix=prefix))

    def cache_path(self, source_type, month, format_id, elo_tier, ext):
        return f"{self.raw_prefix}/{source_type}/{month}/{format_id}-{elo_tier}.{ext}"

    def upload_jsonl_and_get_uri(self, rows, table_name, index=0):
        ext = ".json"
        blob_name = f"{self.staged_prefix}/{table_name}/batch_{index:05d}{ext}"
        blob = self._bucket.blob(blob_name)
        import json
        lines = "\n".join(json.dumps(r, default=str) for r in rows)
        blob.upload_from_string(lines, content_type="application/json")
        return f"gs://{self.bucket_name}/{blob_name}"
