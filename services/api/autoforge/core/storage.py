"""Object storage abstraction (S3-compatible in staging/production).

Keys are always generated server-side from validated identifiers; user input
never becomes a path component (prevents path traversal). Every object is
written with its SHA-256 checksum, verified on upload and optionally on read.
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from .config import get_settings

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9/_.\-]{0,500}$")


class StorageError(Exception):
    pass


def _check_key(key: str) -> str:
    if not KEY_RE.match(key) or ".." in key or key.startswith("/"):
        raise StorageError(f"Invalid object key {key!r}")
    return key


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def get(self, key: str, expected_sha256: str | None = None) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def ping(self) -> bool: ...


def _verify(data: bytes, expected: str | None, key: str) -> bytes:
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise StorageError(f"Checksum mismatch for {key}")
    return data


class LocalStorage:
    """Filesystem backend for development and tests only."""

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _check_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError("Key escapes storage root")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        return hashlib.sha256(data).hexdigest()

    def get(self, key: str, expected_sha256: str | None = None) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise StorageError(f"Object {key} not found")
        return _verify(p.read_bytes(), expected_sha256, key)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def ping(self) -> bool:
        return self.root.exists()


class S3Storage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
            config=Config(retries={"max_attempts": 5, "mode": "standard"}, signature_version="s3v4"),
        )

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        import base64

        digest = hashlib.sha256(data)
        kwargs = {
            "Bucket": self.bucket,
            "Key": _check_key(key),
            "Body": data,
            "ContentType": content_type,
            "Metadata": {"sha256": digest.hexdigest()},
            # Server verifies integrity on receipt.
            "ChecksumSHA256": base64.b64encode(digest.digest()).decode(),
        }
        if not get_settings().s3_endpoint_url:
            # AWS S3: request SSE. S3-compatible stores (MinIO) configure encryption at the bucket.
            kwargs["ServerSideEncryption"] = "AES256"
        self.client.put_object(**kwargs)
        return digest.hexdigest()

    def get(self, key: str, expected_sha256: str | None = None) -> bytes:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=_check_key(key))
        except self.client.exceptions.NoSuchKey as exc:
            raise StorageError(f"Object {key} not found") from exc
        return _verify(obj["Body"].read(), expected_sha256, key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def ping(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(s.storage_local_dir)


def blob_key(project_id: str, sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise StorageError("Invalid sha256")
    return f"projects/{project_id}/blobs/{sha256}"
