"""Finished projects go to a local folder, or to any S3-compatible bucket (moto stands in for S3)."""

import zipfile

import boto3
from moto import mock_aws

from agent import storage


def _zip(tmp_path):
    folder = tmp_path / "run1"
    folder.mkdir()
    path = folder / "blog_api.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("blog_api/README.md", "# hi\n")
    return str(path)


def test_local_storage_keeps_the_file(tmp_path, monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    path = _zip(tmp_path)
    assert storage.keep("run1", path) == path
    assert storage.download_link(path) is None


@mock_aws
def test_s3_storage_uploads_and_signs_a_download_link(tmp_path, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "artifacts")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="artifacts")
    path = _zip(tmp_path)

    location = storage.keep("run1", path)
    assert location == "s3://artifacts/runs/run1/blog_api.zip"
    assert not (tmp_path / "run1").exists()  # the server's disk stays clean
    body = boto3.client("s3", region_name="us-east-1").get_object(Bucket="artifacts", Key="runs/run1/blog_api.zip")["Body"]
    assert body.read()[:2] == b"PK"
    link = storage.download_link(location)
    assert "runs/run1/blog_api.zip" in link and "Signature" in link
