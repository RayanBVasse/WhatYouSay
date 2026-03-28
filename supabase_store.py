import mimetypes
import os
from typing import Dict, List, Optional

from supabase import create_client


def guess_mime_type(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


class SupabaseStore:
    def __init__(self):
        self.url = os.getenv("SUPABASE_URL", "")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        self.upload_bucket = os.getenv("SUPABASE_UPLOADS_BUCKET", "wys_uploads")
        self.results_bucket = os.getenv("SUPABASE_RESULTS_BUCKET", "wys_results")
        self.usage_events_table = os.getenv("SUPABASE_USAGE_EVENTS_TABLE", "usage_events")
        self.client = create_client(self.url, self.key) if (self.url and self.key) else None

    @property
    def ready(self) -> bool:
        return self.client is not None

    def upload_bytes(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        if not self.client:
            raise RuntimeError("Supabase client not configured")
        self.client.storage.from_(bucket).upload(
            path=path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )

    def download_bytes(self, bucket: str, path: str) -> bytes:
        if not self.client:
            raise RuntimeError("Supabase client not configured")
        return self.client.storage.from_(bucket).download(path)

    def list_prefix(self, bucket: str, prefix: str) -> List[str]:
        if not self.client:
            return []
        items = self.client.storage.from_(bucket).list(prefix, {"limit": 1000, "offset": 0})
        if not items:
            return []
        return [f"{prefix}/{item['name']}" for item in items if item.get("name")]

    def remove_many(self, bucket: str, paths: List[str]) -> None:
        if not self.client or not paths:
            return
        self.client.storage.from_(bucket).remove(paths)

    def upsert_run(self, payload: Dict) -> None:
        if not self.client:
            return
        self.client.table("runs").upsert(payload).execute()

    def update_run(self, run_id: str, values: Dict) -> None:
        if not self.client:
            return
        self.client.table("runs").update(values).eq("run_id", run_id).execute()

    def safe_update_run(self, run_id: str, values: Dict) -> None:
        # Avoid breaking user flow if runs table schema differs during migration.
        try:
            self.update_run(run_id, values)
        except Exception as e:
            print(f"[Supabase runs update skipped] {e}")

    def safe_upsert_run(self, payload: Dict) -> None:
        try:
            self.upsert_run(payload)
        except Exception as e:
            print(f"[Supabase runs upsert skipped] {e}")

    def list_runs_older_than(self, cutoff_iso: str, limit: int = 1000) -> List[Dict]:
        if not self.client:
            return []
        result = (
            self.client.table("runs")
            .select("run_id,upload_path,created_at,deleted_at,status")
            .lt("created_at", cutoff_iso)
            .limit(limit)
            .execute()
        )
        rows = result.data or []
        return [r for r in rows if r.get("run_id")]

    def delete_run(self, run_id: str) -> None:
        if not self.client:
            return
        self.client.table("runs").delete().eq("run_id", run_id).execute()

    def safe_delete_run(self, run_id: str) -> None:
        try:
            self.delete_run(run_id)
        except Exception as e:
            print(f"[Supabase runs delete skipped] {e}")

    def insert_usage_event(self, payload: Dict) -> None:
        if not self.client:
            return
        self.client.table(self.usage_events_table).insert(payload).execute()

    def safe_insert_usage_event(self, payload: Dict) -> None:
        try:
            self.insert_usage_event(payload)
        except Exception as e:
            print(f"[Supabase usage_events insert skipped] {e}")
