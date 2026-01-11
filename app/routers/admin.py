from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from google.cloud import firestore
from google.api_core.exceptions import (
    AlreadyExists,
    PermissionDenied,
    NotFound,
    FailedPrecondition,
    ServiceUnavailable,
    GoogleAPICallError,
)

from app.auth import get_principal  # misma protección que invoices
from app.core.errors import AppError

TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db() -> firestore.Client:
    # toma credenciales ADC (gcloud app-default) + proyecto default de gcloud init
    return firestore.Client()


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_principal)],  # PROTEGE TODO /admin/*
)


class TenantCreate(BaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=128)


class TenantOut(BaseModel):
    tenant_id: str
    display_name: Optional[str] = None
    created_at: str


class AdminUserCreate(BaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64)
    user_id: str = Field(..., min_length=2, max_length=64)
    role: str = Field(..., min_length=2, max_length=32)
    email: Optional[str] = Field(default=None, max_length=254)
    full_name: Optional[str] = Field(default=None, max_length=128)


class AdminUserOut(BaseModel):
    tenant_id: str
    user_id: str
    role: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    created_at: str


def _tenant_ref(db: firestore.Client, tenant_id: str):
    return db.collection("tenants").document(tenant_id)


def _map_firestore_error(e: Exception, *, action: str, tenant_id: Optional[str] = None):
    details = {"action": action}
    if tenant_id:
        details["tenant_id"] = tenant_id

    # Errores típicos (más informativos que 500)
    if isinstance(e, PermissionDenied):
        raise AppError(
            code="PERMISSION_DENIED",
            message="Firestore permission denied (check IAM roles for your account/project).",
            status_code=403,
            details=details,
        )
    if isinstance(e, FailedPrecondition):
        raise AppError(
            code="FIRESTORE_NOT_READY",
            message="Firestore is not ready (API disabled or database not initialized).",
            status_code=412,
            details=details,
        )
    if isinstance(e, NotFound):
        raise AppError(
            code="NOT_FOUND",
            message="Firestore resource not found.",
            status_code=404,
            details=details,
        )
    if isinstance(e, ServiceUnavailable):
        raise AppError(
            code="FIRESTORE_UNAVAILABLE",
            message="Firestore temporarily unavailable.",
            status_code=503,
            details=details,
        )
    if isinstance(e, GoogleAPICallError):
        # Cualquier otro error de Google API
        details["google_error"] = str(e)
        raise AppError(
            code="FIRESTORE_ERROR",
            message="Firestore request failed.",
            status_code=502,
            details=details,
        )

    # Fallback (mantiene robustez sin ocultar el contexto)
    details["error"] = str(e)
    raise AppError(
        code="INTERNAL_ERROR",
        message="Unexpected backend error.",
        status_code=500,
        details=details,
    )


@router.post("/tenants", response_model=TenantOut, status_code=201)
def create_tenant(payload: TenantCreate):
    if not TENANT_ID_RE.match(payload.tenant_id):
        raise AppError(
            code="VALIDATION_ERROR",
            message="Invalid tenant_id format",
            status_code=422,
            details={"tenant_id": payload.tenant_id},
        )

    db = get_db()
    created_at = _utcnow()

    try:
        _tenant_ref(db, payload.tenant_id).create(
            {
                "tenant_id": payload.tenant_id,
                "display_name": payload.display_name,
                "created_at": created_at,
                "status": "active",
            }
        )
    except AlreadyExists:
        raise AppError(
            code="TENANT_ALREADY_EXISTS",
            message="Tenant already exists",
            status_code=409,
            details={"tenant_id": payload.tenant_id},
        )
    except Exception as e:
        _map_firestore_error(e, action="create_tenant", tenant_id=payload.tenant_id)

    return TenantOut(
        tenant_id=payload.tenant_id,
        display_name=payload.display_name,
        created_at=created_at.isoformat(),
    )


@router.post("/users", response_model=AdminUserOut, status_code=201)
def create_user(payload: AdminUserCreate):
    db = get_db()

    try:
        if not _tenant_ref(db, payload.tenant_id).get().exists:
            raise AppError(
                code="TENANT_NOT_FOUND",
                message="Tenant not found",
                status_code=404,
                details={"tenant_id": payload.tenant_id},
            )
    except AppError:
        raise
    except Exception as e:
        _map_firestore_error(e, action="check_tenant_exists", tenant_id=payload.tenant_id)

    created_at = _utcnow()
    user_ref = _tenant_ref(db, payload.tenant_id).collection("users").document(payload.user_id)

    try:
        user_ref.create(
            {
                "tenant_id": payload.tenant_id,
                "user_id": payload.user_id,
                "role": payload.role,
                "email": payload.email,
                "full_name": payload.full_name,
                "created_at": created_at,
                "status": "active",
            }
        )
    except AlreadyExists:
        raise AppError(
            code="USER_ALREADY_EXISTS",
            message="User already exists for tenant",
            status_code=409,
            details={"tenant_id": payload.tenant_id, "user_id": payload.user_id},
        )
    except Exception as e:
        _map_firestore_error(e, action="create_user", tenant_id=payload.tenant_id)

    return AdminUserOut(
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        role=payload.role,
        email=payload.email,
        full_name=payload.full_name,
        created_at=created_at.isoformat(),
    )


@router.get("/tenants", response_model=List[TenantOut])
def list_tenants(limit: int = Query(default=100, ge=1, le=500)):
    db = get_db()
    out: List[TenantOut] = []
    try:
        for d in db.collection("tenants").limit(limit).stream():
            data = d.to_dict() or {}
            ca = data.get("created_at")
            out.append(
                TenantOut(
                    tenant_id=data.get("tenant_id", d.id),
                    display_name=data.get("display_name"),
                    created_at=ca.isoformat() if hasattr(ca, "isoformat") else str(ca),
                )
            )
    except Exception as e:
        _map_firestore_error(e, action="list_tenants")
    return out


@router.get("/users", response_model=List[AdminUserOut])
def list_users(
    tenant_id: str = Query(..., min_length=2, max_length=64),
    limit: int = Query(default=200, ge=1, le=500),
):
    db = get_db()

    try:
        if not _tenant_ref(db, tenant_id).get().exists:
            raise AppError(
                code="TENANT_NOT_FOUND",
                message="Tenant not found",
                status_code=404,
                details={"tenant_id": tenant_id},
            )
    except AppError:
        raise
    except Exception as e:
        _map_firestore_error(e, action="check_tenant_exists", tenant_id=tenant_id)

    out: List[AdminUserOut] = []
    try:
        for d in _tenant_ref(db, tenant_id).collection("users").limit(limit).stream():
            data = d.to_dict() or {}
            ca = data.get("created_at")
            out.append(
                AdminUserOut(
                    tenant_id=data.get("tenant_id", tenant_id),
                    user_id=data.get("user_id", d.id),
                    role=data.get("role", "member"),
                    email=data.get("email"),
                    full_name=data.get("full_name"),
                    created_at=ca.isoformat() if hasattr(ca, "isoformat") else str(ca),
                )
            )
    except Exception as e:
        _map_firestore_error(e, action="list_users", tenant_id=tenant_id)

    return out
