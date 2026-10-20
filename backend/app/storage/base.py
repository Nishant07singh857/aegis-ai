# backend/app/storage/base.py
"""
Storage backend interface + local-disk default (Phase 22 / W3, Tech).

The product today stores recordings/highlights on local disk
(``settings.DATA_DIR`` trees). Multi-venue SaaS needs object storage;
this package is the plug point so that day is a config change, not a
refactor:

* :class:`StorageBackend` — the interface (put/get/exists/url/delete).
* :class:`LocalDiskStorage` — the real default implementation.
* :class:`S3Storage` — PLUG-READY stub for S3 / Cloudflare R2
  (S3-compatible). It reads its config from env (``S3_BUCKET``,
  ``S3_ENDPOINT_URL`` for R2, ``S3_REGION``) and raises
  ``NotImplementedError`` until a bucket + credentials exist and the
  ``boto3`` client is wired — it never pretends to store anything.

Select via ``STORAGE_BACKEND`` env: ``local`` (default) | ``s3``.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class StorageBackend(ABC):
    """One object store. Keys are slash-separated relative paths."""

    @abstractmethod
    def put_file(self, key: str, local_path: Path) -> str:
        """Store a local file under ``key``; returns its locator."""

    @abstractmethod
    def get_to_file(self, key: str, local_path: Path) -> bool:
        """Fetch ``key`` to a local file; False when absent."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """True when ``key`` is stored."""

    @abstractmethod
    def url(self, key: str) -> Optional[str]:
        """Public/servable URL for ``key``, or None when not servable."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete ``key``; True when something was removed."""


class LocalDiskStorage(StorageBackend):
    """Local-disk implementation rooted at a base directory."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        if base_dir is None:
            try:
                from app.config import settings

                base_dir = Path(settings.DATA_DIR)
            except Exception:  # pragma: no cover - tooling fallback
                base_dir = Path(__file__).resolve().parents[2] / "data"
        self.base_dir = Path(base_dir)

    def _path(self, key: str) -> Path:
        # Keys must stay inside the root (no ../ escapes).
        path = (self.base_dir / key).resolve()
        if self.base_dir.resolve() not in path.parents and path != self.base_dir.resolve():
            raise ValueError(f"storage key escapes base dir: {key!r}")
        return path

    def put_file(self, key: str, local_path: Path) -> str:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(local_path).read_bytes())
        return str(dest)

    def get_to_file(self, key: str, local_path: Path) -> bool:
        src = self._path(key)
        if not src.exists():
            return False
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(src.read_bytes())
        return True

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def url(self, key: str) -> Optional[str]:
        # Local files are served by the app's own routes, not public URLs.
        return None

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False


class S3Storage(StorageBackend):
    """PLUG-READY S3 / Cloudflare R2 backend (not implemented).

    Blocker: no bucket or credentials exist for the current 1-PC
    product. Activation checklist: create the bucket, set
    ``STORAGE_BACKEND=s3``, ``S3_BUCKET``, ``S3_ENDPOINT_URL``
    (R2 endpoint; empty for AWS), ``S3_REGION``, provide credentials
    via the standard AWS env/role chain, add ``boto3`` and implement
    the five methods against it.
    """

    def __init__(self) -> None:
        self.bucket = os.environ.get("S3_BUCKET", "")
        self.endpoint_url = os.environ.get("S3_ENDPOINT_URL", "")  # R2
        self.region = os.environ.get("S3_REGION", "auto")

    def _blocked(self) -> NotImplementedError:
        return NotImplementedError(
            "S3/R2 storage is plug-ready but not implemented: no bucket "
            "configured for this deployment (see storage/base.py). "
            "Local disk remains the supported backend."
        )

    def put_file(self, key: str, local_path: Path) -> str:
        raise self._blocked()

    def get_to_file(self, key: str, local_path: Path) -> bool:
        raise self._blocked()

    def exists(self, key: str) -> bool:
        raise self._blocked()

    def url(self, key: str) -> Optional[str]:
        raise self._blocked()

    def delete(self, key: str) -> bool:
        raise self._blocked()


def get_storage() -> StorageBackend:
    """The configured backend (``STORAGE_BACKEND`` env, default local)."""
    backend = (os.environ.get("STORAGE_BACKEND") or "local").strip().lower()
    if backend == "s3":
        return S3Storage()
    return LocalDiskStorage()
