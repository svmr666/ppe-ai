"""Энэ Mac-ийн Cloudflare quick tunnel хаягийг Supabase Storage-ийн public `runtime-config/endpoints.json`-д бичнэ.

Vercel дээрх web (BACKEND_REGISTRY_URL) энэ файлаас backend-ээ олдог тул Mac солих / дахин асаахад redeploy хэрэггүй.
Зөвхөн нийтийн хаяг бичигдэнэ — нууц утга энд орохгүй. worker.py автоматаар дууддаг:

    cd backend && python -m scripts.publish_endpoints https://api.trycloudflare.com https://video.trycloudflare.com
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from app.config import get_settings

BUCKET = "runtime-config"
OBJECT = "endpoints.json"


def main() -> int:
    if len(sys.argv) != 3 or not all(u.startswith("https://") for u in sys.argv[1:]):
        print("Хэрэглээ: python -m scripts.publish_endpoints <api_url> <video_url>", file=sys.stderr)
        return 2
    api_url, video_url = (u.rstrip("/") for u in sys.argv[1:])

    from supabase import create_client

    settings = get_settings()
    storage = create_client(settings.supabase_url, settings.supabase_service_role_key).storage
    if not any(bucket.id == BUCKET for bucket in storage.list_buckets()):
        storage.create_bucket(BUCKET, options={"public": True})

    body = json.dumps({"api_url": api_url, "video_url": video_url, "updated_at": datetime.now(timezone.utc).isoformat()})
    storage.from_(BUCKET).upload(
        OBJECT, body.encode("utf-8"),
        file_options={"content-type": "application/json", "cache-control": "5", "upsert": "true"},
    )
    print(f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{BUCKET}/{OBJECT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
