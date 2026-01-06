import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.storage import (
    upload_invoice_pdf_to_gcs,
    open_invoice_pdf_from_gcs,
    list_invoices_from_gcs,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_filename(name: Optional[str]) -> str:
    base = os.path.basename(name or "")
    return base[:200] if base else ""


def _require_tenant(x_tenant_id: Optional[str]) -> str:
    tenant_id = (x_tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Missing tenant. Provide header X-Tenant-Id.")
    return tenant_id


@router.get("/")
async def get_invoices(x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id")):
    tenant_id = _require_tenant(x_tenant_id)
    try:
        items = await run_in_threadpool(list_invoices_from_gcs, tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant id format.")
    return {"count": len(items), "items": items}


@router.post("/upload")
async def upload_invoice(
    file: UploadFile = File(...),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    tenant_id = _require_tenant(x_tenant_id)

    original_name = _safe_filename(file.filename)
    content_type = (file.content_type or "").lower()

    # Aceptamos PDF por content-type o por extensión
    is_pdf = content_type in ("application/pdf", "application/x-pdf") or original_name.lower().endswith(".pdf")
    if not is_pdf:
        raise HTTPException(status_code=415, detail="Only PDF invoices are supported.")

    invoice_id = uuid.uuid4().hex
    await file.seek(0)

    try:
        # google-cloud-storage es sync -> threadpool
        gcs_info = await run_in_threadpool(
            upload_invoice_pdf_to_gcs,
            tenant_id,
            file,  # UploadFile (storage lee file.file)
            invoice_id,
            original_name,
            (x_idempotency_key or "").strip() or None,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant id format.")
    finally:
        await file.close()

    return {
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "original_filename": original_name or None,
        "content_type": "application/pdf",
        "bytes": gcs_info["bytes"],
        "uploaded_at": _utc_now_iso(),
        "gcs_bucket": gcs_info["bucket"],
        "gcs_object": gcs_info["object_name"],
        "gcs_uri": gcs_info["gcs_uri"],
        "status": "uploaded",
    }


@router.get("/{invoice_id}/download")
def download_invoice(
    invoice_id: str,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
):
    tenant_id = _require_tenant(x_tenant_id)

    try:
        stream, meta = open_invoice_pdf_from_gcs(tenant_id, invoice_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Invoice PDF not found.")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant id format.")

    filename = meta.get("original_filename") or f"{invoice_id}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{_safe_filename(filename) or (invoice_id + ".pdf")}"'}
    return StreamingResponse(stream, media_type="application/pdf", headers=headers)
