"""Upload size limiting for the portfolio services.

FastAPI reads an ``UploadFile`` into a spooled temp file before the handler runs,
so a size check inside the handler is too late to protect memory. This module
provides a dependency that inspects ``Content-Length`` first and rejects an
oversized request with 413 before the body is read.

``Content-Length`` is advisory — a client can lie or use chunked encoding — so
the handler should also verify the bytes it actually read. ``read_upload`` does
both.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, UploadFile, status

MAX_UPLOAD_BYTES_ENV = "MAX_UPLOAD_BYTES"
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB


def max_upload_bytes() -> int:
    """Configured upload ceiling in bytes."""
    raw = os.getenv(MAX_UPLOAD_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES
    return value if value > 0 else DEFAULT_MAX_UPLOAD_BYTES


def enforce_content_length(
    content_length: int | None = Header(default=None, alias="Content-Length"),
) -> None:
    """Reject an oversized request before the body is read.

    A missing Content-Length is allowed through: chunked uploads do not send one,
    and ``read_upload`` still enforces the limit on the bytes actually read.
    """
    limit = max_upload_bytes()
    if content_length is not None and content_length > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload exceeds the {limit} byte limit",
        )


async def read_upload(file: UploadFile) -> bytes:
    """Read an upload, enforcing the limit on the bytes actually received.

    This is the authoritative check: it does not trust Content-Length.
    """
    limit = max_upload_bytes()
    data = await file.read()
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload exceeds the {limit} byte limit",
        )
    return data
