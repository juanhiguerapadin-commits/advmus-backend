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
