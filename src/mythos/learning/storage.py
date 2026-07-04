"""Object storage adapters for Mythos dataset artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


class StoredObject(StrictModel):
    source_path: str
    uri: str
    size_bytes: int
    sha256: str


class ObjectStore(Protocol):
    def put_file(self, path: str | Path, *, key: str | None = None) -> StoredObject:
        ...

    def put_json(self, payload: dict[str, Any], *, key: str) -> StoredObject:
        ...


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, path: str | Path, *, key: str | None = None) -> StoredObject:
        source = Path(path)
        target = self.root / (key or source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return StoredObject(
            source_path=str(source),
            uri=str(target),
            size_bytes=target.stat().st_size,
            sha256=file_sha256(target),
        )

    def put_json(self, payload: dict[str, Any], *, key: str) -> StoredObject:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return StoredObject(source_path=str(target), uri=str(target), size_bytes=target.stat().st_size, sha256=file_sha256(target))


class S3ObjectStore:
    def __init__(self, uri: str, *, endpoint_url: str | None = None) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - optional production dependency
            raise RuntimeError("boto3 is required for s3/minio object storage") from exc
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError("S3 object store URI must look like s3://bucket/prefix")
        self.bucket = parsed.netloc
        self.prefix = parsed.path.strip("/")
        self.client = boto3.client("s3", endpoint_url=endpoint_url)

    def _object_key(self, key: str) -> str:
        return f"{self.prefix}/{key}".strip("/") if self.prefix else key

    def put_file(self, path: str | Path, *, key: str | None = None) -> StoredObject:
        source = Path(path)
        object_key = self._object_key(key or source.name)
        self.client.upload_file(str(source), self.bucket, object_key)
        return StoredObject(
            source_path=str(source),
            uri=f"s3://{self.bucket}/{object_key}",
            size_bytes=source.stat().st_size,
            sha256=file_sha256(source),
        )

    def put_json(self, payload: dict[str, Any], *, key: str) -> StoredObject:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        object_key = self._object_key(key)
        self.client.put_object(Bucket=self.bucket, Key=object_key, Body=body, ContentType="application/json")
        digest = hashlib.sha256(body).hexdigest()
        return StoredObject(source_path=f"memory:{key}", uri=f"s3://{self.bucket}/{object_key}", size_bytes=len(body), sha256=digest)


class ObjectStoreConfig(StrictModel):
    uri: str = Field(default="local://artifacts/mythos/object-store", min_length=1)
    endpoint_url: str | None = None


def create_object_store(config: ObjectStoreConfig) -> ObjectStore:
    parsed = urlparse(config.uri)
    if len(parsed.scheme) == 1 and config.uri[1:3] in {":\\", ":/"}:
        return LocalObjectStore(config.uri)
    if parsed.scheme in {"", "file"}:
        return LocalObjectStore(parsed.path or config.uri)
    if parsed.scheme == "local":
        root = f"{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
        return LocalObjectStore(root)
    if parsed.scheme == "s3":
        return S3ObjectStore(config.uri, endpoint_url=config.endpoint_url)
    raise ValueError(f"unsupported object store URI: {config.uri}")


def upload_paths(store: ObjectStore, paths: list[str | Path], *, prefix: str) -> list[StoredObject]:
    uploaded: list[StoredObject] = []
    for path in paths:
        source = Path(path)
        uploaded.append(store.put_file(source, key=f"{prefix}/{source.name}"))
    return uploaded
