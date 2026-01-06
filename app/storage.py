import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from google.cloud import storage
from google.api_core.exceptions import NotFound


# -------------------------
# Helpers
# -------------------------

_TENANT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

def _sanitize_tenant_id(tenant_id: str) -> str:
    t = (tenant_id or "").strip()
    if not _TENANT_RE.match(t):
        raise ValueError("Invalid tenant_id format")
    return t

def _sanitize_invoice_id(invoice_id: str) -> str:
    inv = (invoice_id or "").strip()
    # uuid hex => 32 chars; permitimos otros ids simples por si a futuro cambia,
    # pero evitamos '/', espacios, etc.
    if not re.fullmatch(r"[a-zA-Z0-9_\-]{8,128}", inv):
        raise ValueError("Invalid invoice_id format")
    return inv


# -------------------------
# GCS client/bucket (cached)
# -------------------------

_CLIENT: Optional[storage.Client] = None
_BUCKET: Optional[storage.Bucket] = None

def get_gcs_client() -> storage.Client:
    # ADC:
    # - Local: gcloud auth application-default login
    # - Cloud Run: Service Account (SIN JSON keys)
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = storage.Client()
    return _CLIENT

def get_bucket() -> storage.Bucket:
    global _BUCKET
    if _BUCKET is None:
        bucket_name = _require_env("GCS_BUCKET")
        _BUCKET = get_gcs_client().bucket(bucket_name)
    return _BUCKET


# -------------------------
# Paths
# -------------------------

@dataclass(frozen=True)
class GcsPaths:
    tenant_prefix: str
    invoices_prefix: str

def tenant_paths(tenant_id: str) -> GcsPaths:
    tenant_id = _sanitize_tenant_id(tenant_id)
    tenant_prefix = f"tenants/{tenant_id}"
    return GcsPaths(
        tenant_prefix=tenant_prefix,
        invoices_prefix=f"{tenant_prefix}/invoices/",
    )

def _invoice_object_name(tenant_id: str, invoice_id: str) -> str:
    paths = tenant_paths(tenant_id)
    invoice_id = _sanitize_invoice_id(invoice_id)
    # determinístico y simple: siempre PDF
    return f"{paths.invoices_prefix}{invoice_id}.pdf"


# -------------------------
# Core operations
# -------------------------

def upload_invoice_pdf_to_gcs(
    tenant_id: str,
    upload_file,  # FastAPI UploadFile
    invoice_id: str,
    original_filename: Optional[str],
    idempotency_key: Optional[str],
) -> dict[str, Any]:
    """
    Upload PDF to GCS without using local disk as storage.
    - Source: upload_file.file stream
    - Dest: tenants/<tenant>/invoices/<invoice_id>.pdf
    - Stores minimal useful metadata on the object (no invoices.json registry).
    """
    bucket = get_bucket()
    object_name = _invoice_object_name(tenant_id, invoice_id)
    blob = bucket.blob(object_name)

    blob.content_type = "application/pdf"
    blob.metadata = {
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "original_filename": (original_filename or "").strip() or None,
        "idempotency_key": (idempotency_key or "").strip() or None,
        "uploaded_at": _utc_now_iso(),
    }

    blob.upload_from_file(
        upload_file.file,
        rewind=True,
        content_type="application/pdf",
    )
    blob.reload()

    return {
        "bucket": bucket.name,
        "object_name": object_name,
        "gcs_uri": f"gs://{bucket.name}/{object_name}",
        "bytes": blob.size,
        "updated": _utc_now_iso(),
    }


def open_invoice_pdf_from_gcs(tenant_id: str, invoice_id: str) -> Tuple[Any, dict[str, Any]]:
    """
    Open the invoice PDF as a readable stream for StreamingResponse.
    Returns (stream, meta).
    Raises FileNotFoundError if not found.
    """
    bucket = get_bucket()
    object_name = _invoice_object_name(tenant_id, invoice_id)
    blob = bucket.blob(object_name)

    try:
        blob.reload()
    except NotFound:
        raise FileNotFoundError("Invoice PDF not found in GCS")

    md = blob.metadata or {}
    meta = {
        "invoice_id": invoice_id,
        "tenant_id": tenant_id,
        "bytes": blob.size,
        "updated": blob.updated.isoformat().replace("+00:00", "Z") if blob.updated else None,
        "original_filename": md.get("original_filename"),
        "gcs_object": object_name,
        "gcs_uri": f"gs://{bucket.name}/{object_name}",
    }

    return blob.open("rb"), meta


def list_invoices_from_gcs(tenant_id: str) -> list[dict[str, Any]]:
    """
    List invoices for tenant by enumerating objects under:
      tenants/<tenant>/invoices/
    No JSON registry.
    """
    bucket = get_bucket()
    paths = tenant_paths(tenant_id)

    items: list[dict[str, Any]] = []

    for blob in bucket.list_blobs(prefix=paths.invoices_prefix):
        name = blob.name or ""
        if not name.endswith(".pdf"):
            continue

        invoice_id = name.split("/")[-1].removesuffix(".pdf")
        md = blob.metadata or {}

        items.append({
            "tenant_id": tenant_id,
            "invoice_id": invoice_id,
            "bytes": blob.size,
            "updated": blob.updated.isoformat().replace("+00:00", "Z") if blob.updated else None,
            "original_filename": md.get("original_filename"),
            "gcs_bucket": bucket.name,
            "gcs_object": name,
            "gcs_uri": f"gs://{bucket.name}/{name}",
            "status": "uploaded",
        })

    items.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return items

