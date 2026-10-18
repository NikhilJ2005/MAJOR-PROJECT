"""Where finished projects (zips) are kept: a local folder, or any S3-compatible bucket.

A local folder (DATA_DIR) ties the platform to a disk that survives restarts, such as a Railway
volume. With S3_BUCKET set, zips go to object storage instead (Cloudflare R2, AWS S3, MinIO,
Backblaze B2 ...), so the server itself keeps no state on disk and can run on any host:

    S3_BUCKET=vibestack-artifacts
    S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com   (empty for AWS S3)
    S3_ACCESS_KEY_ID=...   S3_SECRET_ACCESS_KEY=...   S3_REGION=auto
    S3_PUBLIC_ENDPOINT_URL=...   only when browsers reach the bucket at another address (self-hosted MinIO)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

S3_PREFIX = "s3://"
LINK_SECONDS = 600  # download links stay valid for 10 minutes


def bucket() -> str:
    return os.environ.get("S3_BUCKET", "")


def _client(public: bool = False):
    """The S3 client; `public` uses the address browsers reach (S3_PUBLIC_ENDPOINT_URL) for signed links."""
    import boto3

    endpoint = (public and os.environ.get("S3_PUBLIC_ENDPOINT_URL")) or os.environ.get("S3_ENDPOINT_URL") or None
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID") or None,
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY") or None,
        region_name=os.environ.get("S3_REGION") or "auto",
    )


def keep(run_id: str, local_zip: str) -> str:
    """Store a finished zip. Returns where it is: the local path, or s3://bucket/key."""
    if not bucket():
        return local_zip
    key = f"runs/{run_id}/{Path(local_zip).name}"
    _client().upload_file(local_zip, bucket(), key, ExtraArgs={"ContentType": "application/zip"})
    shutil.rmtree(Path(local_zip).parent, ignore_errors=True)  # the bucket has it now; keep the disk clean
    return f"{S3_PREFIX}{bucket()}/{key}"


def download_link(location: str) -> str | None:
    """A short-lived download URL for a zip in a bucket (None for a local file)."""
    if not location.startswith(S3_PREFIX):
        return None
    name, _, key = location[len(S3_PREFIX):].partition("/")
    return _client(public=True).generate_presigned_url(
        "get_object", Params={"Bucket": name, "Key": key, "ResponseContentDisposition": f'attachment; filename="{Path(key).name}"'},
        ExpiresIn=LINK_SECONDS,
    )
