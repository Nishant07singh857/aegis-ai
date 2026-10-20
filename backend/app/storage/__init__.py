# backend/app/storage/__init__.py
"""Object storage plug point (Phase 22 / W3).

Default: local disk (the 1-PC product). S3 / Cloudflare R2 activate by
setting ``STORAGE_BACKEND=s3`` once a bucket exists — see ``base.py``.
"""

from .base import LocalDiskStorage, S3Storage, StorageBackend, get_storage

__all__ = ["StorageBackend", "LocalDiskStorage", "S3Storage", "get_storage"]
