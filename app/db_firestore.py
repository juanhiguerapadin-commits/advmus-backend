from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore

_db: Optional[firestore.Client] = None


def get_db() -> firestore.Client:
    """Singleton Firestore client. En Cloud Run usa el service account (ADC)."""
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def invoices_col(tenant_id: str) -> firestore.CollectionReference:
    return get_db().collection("tenants").document(tenant_id).collection("invoices")


def invoice_doc_ref(tenant_id: str, invoice_id: str) -> firestore.DocumentReference:
    return invoices_col(tenant_id).document(invoice_id)


def create_invoice(tenant_id: str, invoice_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea el documento inicial (ideal en upload si quisieras modo "create only").
    """
    ref = invoice_doc_ref(tenant_id, invoice_id)

    payload: Dict[str, Any] = {
        **data,
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    ref.set(payload, merge=False)
    return payload


@firestore.transactional
def _upsert_txn(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    tenant_id: str,
    invoice_id: str,
    data: Dict[str, Any],
) -> None:
    snap = ref.get(transaction=transaction)
    existing = snap.to_dict() if snap.exists else {}

    patch: Dict[str, Any] = {
        **data,
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    # Seteamos created_at solo si no existe (no lo pisamos en updates).
    if not existing or "created_at" not in existing:
        patch["created_at"] = firestore.SERVER_TIMESTAMP

    transaction.set(ref, patch, merge=True)


def upsert_invoice_metadata(tenant_id: str, invoice_id: str, data: Dict[str, Any]) -> None:
    """
    Upsert con merge=True y timestamps correctos.
    No pisa created_at si ya existe.
    """
    ref = invoice_doc_ref(tenant_id, invoice_id)
    tx = get_db().transaction()
    _upsert_txn(tx, ref, tenant_id, invoice_id, data)


def patch_invoice_metadata(tenant_id: str, invoice_id: str, patch: Dict[str, Any]) -> None:
    """
    Patch rápido: solo updates. Ideal para status, docai_job_id, etc.
    """
    ref = invoice_doc_ref(tenant_id, invoice_id)
    ref.update({**patch, "updated_at": firestore.SERVER_TIMESTAMP})


def get_invoice_metadata(tenant_id: str, invoice_id: str) -> Optional[Dict[str, Any]]:
    ref = invoice_doc_ref(tenant_id, invoice_id)
    snap = ref.get()
    if not snap.exists:
        return None

    d = snap.to_dict() or {}
    d.setdefault("invoice_id", snap.id)
    d.setdefault("tenant_id", tenant_id)
    return d


def list_invoices_metadata(tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    q = (
        invoices_col(tenant_id)
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )

    out: List[Dict[str, Any]] = []
    for snap in q.stream():
        d = snap.to_dict() or {}
        d.setdefault("invoice_id", snap.id)
        d.setdefault("tenant_id", tenant_id)
        out.append(d)

    return out


def find_invoice_by_idempotency_key(tenant_id: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
    key = (idempotency_key or "").strip()
    if not key:
        return None

    q = invoices_col(tenant_id).where("idempotency_key", "==", key).limit(1)
    snaps = list(q.stream())
    if not snaps:
        return None

    snap = snaps[0]
    d = snap.to_dict() or {}
    d.setdefault("invoice_id", snap.id)
    d.setdefault("tenant_id", tenant_id)
    return d


def find_invoice_by_sha256(tenant_id: str, sha256: str) -> Optional[Dict[str, Any]]:
    h = (sha256 or "").strip()
    if not h:
        return None

    q = invoices_col(tenant_id).where("sha256", "==", h).limit(1)
    snaps = list(q.stream())
    if not snaps:
        return None

    snap = snaps[0]
    d = snap.to_dict() or {}
    d.setdefault("invoice_id", snap.id)
    d.setdefault("tenant_id", tenant_id)
    return d


def find_recent_invoice_by_content_hash(
    tenant_id: str,
    content_hash: str,
    window_minutes: int = 60,
) -> Optional[Dict[str, Any]]:
    """
    Dedupe “reciente” sin exigir índices compuestos:
    - query por igualdad (content_hash)
    - filtro por ventana en Python
    """
    h = (content_hash or "").strip()
    if not h:
        return None

    q = invoices_col(tenant_id).where("content_hash", "==", h).limit(10)
    docs = list(q.stream())
    if not docs:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    best: Optional[Dict[str, Any]] = None
    best_ts: Optional[datetime] = None

    for snap in docs:
        data = snap.to_dict() or {}
        ts = data.get("created_at") or data.get("updated_at")

        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            if ts >= cutoff and (best_ts is None or ts > best_ts):
                best = data
                best_ts = ts
                best.setdefault("invoice_id", snap.id)
                best.setdefault("tenant_id", tenant_id)

    return best
