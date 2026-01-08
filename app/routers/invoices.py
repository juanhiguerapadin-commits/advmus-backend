import os
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.auth import Principal, get_principal
from app.db_firestore import (
    get_invoice_metadata,
    list_invoices_metadata,
    upsert_invoice_metadata,
    find_invoice_by_idempotency_key,
    find_recent_invoice_by_content_hash,
)
from app.schemas import ALLOWED_STATUSES, ALLOWED_TRANSITIONS, InvoicePatch
from app.storage import (
    list_invoices_from_gcs,
    open_invoice_pdf_from_gcs,
    upload_invoice_pdf_to_gcs,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now_dt() -> datetime:
    # Firestore acepta datetime; mejor timezone-aware
    return datetime.now(timezone.utc)


def _safe_filename(name: Optional[str]) -> str:
    base = os.path.basename(name or "")
    return base[:200] if base else ""


def _sha256_fileobj(fileobj, chunk_size: int = 1024 * 1024) -> str:
    """
    Calcula SHA-256 leyendo en chunks (stream), sin cargar todo el PDF en memoria.
    """
    h = hashlib.sha256()
    pos = None
    try:
        pos = fileobj.tell()
    except Exception:
        pos = None

    try:
        try:
            fileobj.seek(0)
        except Exception:
            pass

        while True:
            chunk = fileobj.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    finally:
        try:
            fileobj.seek(0)
        except Exception:
            pass
        if pos is not None:
            try:
                fileobj.seek(pos)
            except Exception:
                pass

    return h.hexdigest()


@router.get("")
@router.get("/")
async def get_invoices(principal: Principal = Depends(get_principal)):
    tenant_id = principal.tenant_id

    # 1) Intentamos listar desde Firestore (metadata)
    items = await run_in_threadpool(list_invoices_metadata, tenant_id, 50)

    # 2) Si Firestore está vacío, fallback a GCS y bootstrap a metadata
    if not items:
        gcs_items = await run_in_threadpool(list_invoices_from_gcs, tenant_id)

        for it in gcs_items:
            invoice_id = it.get("invoice_id")
            if not invoice_id:
                continue

            now_iso = _utc_now_iso()
            now_dt = _utc_now_dt()

            meta = {
                "tenant_id": tenant_id,
                "invoice_id": invoice_id,
                "original_filename": it.get("original_filename"),
                "bytes": it.get("bytes"),
                "gcs_bucket": it.get("gcs_bucket"),
                "gcs_object": it.get("gcs_object"),
                "gcs_uri": it.get("gcs_uri"),
                "status": it.get("status") or "uploaded",
                # timestamps para API
                "created": it.get("updated") or now_iso,
                "updated": it.get("updated") or now_iso,
                # timestamps para Firestore (ordenables)
                "created_at": now_dt,
                "updated_at": now_dt,
            }
            await run_in_threadpool(upsert_invoice_metadata, tenant_id, invoice_id, meta)

        items = await run_in_threadpool(list_invoices_metadata, tenant_id, 50)

    return {"count": len(items), "items": items}


@router.get("/{invoice_id}")
async def get_invoice(invoice_id: str, principal: Principal = Depends(get_principal)):
    tenant_id = principal.tenant_id
    meta = await run_in_threadpool(get_invoice_metadata, tenant_id, invoice_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Invoice not found in metadata DB.")
    return meta


@router.post("/upload")
async def upload_invoice(
    file: UploadFile = File(...),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    principal: Principal = Depends(get_principal),
):
    tenant_id = principal.tenant_id

    original_name = _safe_filename(file.filename)
    content_type = (file.content_type or "").lower()

    # Aceptamos PDF por content-type o por extensión
    is_pdf = content_type in ("application/pdf", "application/x-pdf") or original_name.lower().endswith(".pdf")
    if not is_pdf:
        raise HTTPException(status_code=415, detail="Only PDF invoices are supported.")

    idem_key = (x_idempotency_key or "").strip() or None

    # 1) Idempotency: si ya existe (tenant_id, idempotency_key) devolvemos el mismo invoice (no duplicamos)
    if idem_key:
        existing = await run_in_threadpool(find_invoice_by_idempotency_key, tenant_id, idem_key)
        if existing:
            return existing

    # 2) Hash del contenido para dedupe real
    await file.seek(0)
    content_hash = await run_in_threadpool(_sha256_fileobj, file.file)
    await file.seek(0)

    # (bonus) Dedupe por contenido reciente: devolvemos 409 con invoice existente
    dup = await run_in_threadpool(find_recent_invoice_by_content_hash, tenant_id, content_hash, 60)
    if dup:
        raise HTTPException(
            status_code=409,
            detail={"message": "Duplicate content (recent)", "existing_invoice_id": dup.get("invoice_id")},
        )

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
            idem_key,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant id format.")
    finally:
        await file.close()

    now_iso = _utc_now_iso()
    now_dt = _utc_now_dt()

    meta = {
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "original_filename": original_name or None,
        "content_type": "application/pdf",
        "bytes": gcs_info["bytes"],
        "gcs_bucket": gcs_info["bucket"],
        "gcs_object": gcs_info["object_name"],
        "gcs_uri": gcs_info["gcs_uri"],
        "status": "uploaded",
        "created": now_iso,
        "updated": now_iso,
        "created_at": now_dt,
        "updated_at": now_dt,
        # Dedupe / idempotency
        "idempotency_key": idem_key,
        "content_hash": content_hash,
    }

    await run_in_threadpool(upsert_invoice_metadata, tenant_id, invoice_id, meta)
    return meta


@router.patch("/{invoice_id}")
async def patch_invoice(
    invoice_id: str,
    payload: InvoicePatch,
    principal: Principal = Depends(get_principal),
):
    tenant_id = principal.tenant_id

    current = await run_in_threadpool(get_invoice_metadata, tenant_id, invoice_id)
    if not current:
        raise HTTPException(status_code=404, detail="Invoice not found in metadata DB.")

    # Compat Pydantic v1/v2
    updates: Dict[str, Any] = (
        payload.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
        if hasattr(payload, "model_dump")
        else payload.dict(exclude_unset=True)  # type: ignore[call-arg]
    )

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided.")

    # ----- Workflow mínimo: validar status + transición -----
    if "status" in updates:
        new_status = updates["status"]
        if new_status not in ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status.")

        old_status = (current.get("status") or "uploaded")
        allowed_next = ALLOWED_TRANSITIONS.get(old_status, set())

        if new_status != old_status and new_status not in allowed_next:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status transition: {old_status} -> {new_status}",
            )

    # Normalizaciones
    if "currency" in updates and updates["currency"]:
        updates["currency"] = str(updates["currency"]).upper()

    if "due_date" in updates and updates["due_date"] is not None:
        # llega como date -> guardamos ISO
        updates["due_date"] = updates["due_date"].isoformat()

    now_iso = _utc_now_iso()
    updates["updated"] = now_iso
    updates["updated_at"] = _utc_now_dt()

    await run_in_threadpool(upsert_invoice_metadata, tenant_id, invoice_id, updates)

    merged = dict(current)
    merged.update(updates)
    return merged


@router.get("/{invoice_id}/download")
def download_invoice(invoice_id: str, principal: Principal = Depends(get_principal)):
    tenant_id = principal.tenant_id

    try:
        stream, meta = open_invoice_pdf_from_gcs(tenant_id, invoice_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Invoice PDF not found.")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant id format.")

    filename = meta.get("original_filename") or f"{invoice_id}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{_safe_filename(filename) or (invoice_id + ".pdf")}"'}
    return StreamingResponse(stream, media_type="application/pdf", headers=headers)
