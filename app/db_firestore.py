from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud import firestore

_db: Optional[firestore.Client] = None


def _utc_now() -> datetime:
    # Firestore maneja timestamps; datetime naive se interpreta como UTC
    return datetime.utcnow()


def get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def invoice_doc_ref(tenant_id: str, invoice_id: str) -> firestore.DocumentReference:
    db = get_db()
    return (
        db.collection("tenants")
        .document(tenant_id)
        .collection("invoices")
        .document(invoice_id)
    )


def upsert_invoice_metadata(tenant_id: str, invoice_id: str, data: Dict[str, Any]) -> None:
    # Merge=True para no pisar campos si en el futuro agregamos más cosas (parsed, amount, due_date, etc.)
    ref = invoice_doc_ref(tenant_id, invoice_id)
    ref.set(data, merge=True)


def get_invoice_metadata(tenant_id: str, invoice_id: str) -> Optional[Dict[str, Any]]:
    ref = invoice_doc_ref(tenant_id, invoice_id)
    snap = ref.get()
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    return d


def list_invoices_metadata(tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    db = get_db()
    q = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("invoices")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )

    out: List[Dict[str, Any]] = []
    for snap in q.stream():
        d = snap.to_dict() or {}
        out.append(d)
    return out
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from google.cloud import firestore

_db = None


def _client():
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def _invoices_col(tenant_id: str):
    # Estructura multi-tenant
    return _client().collection("tenants").document(tenant_id).collection("invoices")


def find_invoice_by_idempotency_key(tenant_id: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
    key = (idempotency_key or "").strip()
    if not key:
        return None

    q = _invoices_col(tenant_id).where("idempotency_key", "==", key).limit(1)
    docs = list(q.stream())
    if not docs:
        return None
    return docs[0].to_dict()


def find_recent_invoice_by_content_hash(
    tenant_id: str,
    content_hash: str,
    window_minutes: int = 60,
) -> Optional[Dict[str, Any]]:
    h = (content_hash or "").strip()
    if not h:
        return None

    # Buscamos el más reciente con ese hash (Firestore index normal para single-field)
    q = _invoices_col(tenant_id).where("content_hash", "==", h).limit(5)
    docs = list(q.stream())
    if not docs:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    # Filtramos “reciente” en Python para evitar requerir índices compuestos por ahora
    best = None
    best_ts = None
    for d in docs:
        data = d.to_dict() or {}
        ts = data.get("created_at") or data.get("updated_at")
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff and (best_ts is None or ts > best_ts):
                best = data
                best_ts = ts

    return best
