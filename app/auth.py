# app/auth.py
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional, Set

from fastapi import Header, HTTPException, status

TENANT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    auth_mode: str  # "api_key" | "firebase" | "none"


def _load_api_keys_map() -> Dict[str, str]:
    """
    Multi-tenant API keys.
    Expected env:
      API_KEYS_JSON='{"demo":"KEY1","tenant2":"KEY2"}'
    """
    raw = (os.getenv("API_KEYS_JSON", "") or "").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except Exception:
        # Env mal formado -> tratamos como "no configurado"
        return {}

    if not isinstance(data, dict):
        return {}

    # Normalizamos a str->str
    out: Dict[str, str] = {}
    for k, v in data.items():
        if k is None or v is None:
            continue
        out[str(k).strip()] = str(v).strip()
    return out


def _load_single_api_key() -> Optional[str]:
    """
    Single API key (dev/simple).
    Env:
      API_KEY='KEY_LARGA_123...'
    """
    key = (os.getenv("API_KEY", "") or "").strip()
    return key or None


def _load_api_keys_list() -> Set[str]:
    """
    Optional: comma-separated keys.
    Env:
      API_KEYS='KEY1,KEY2'
    """
    raw = (os.getenv("API_KEYS", "") or "").strip()
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


def _require_tenant(x_tenant_id: Optional[str]) -> str:
    tenant_id = (x_tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Missing X-Tenant-Id")
    if not TENANT_RE.match(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-Id format")
    return tenant_id


def _require_api_key(
    tenant_id: str,
    x_api_key: Optional[str],
) -> None:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key",
        )

    provided = str(x_api_key).strip()

    # 1) Multi-tenant map (preferred)
    keys_map = _load_api_keys_map()
    if keys_map:
        expected = keys_map.get(tenant_id)
        if expected and hmac.compare_digest(str(expected), provided):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # 2) Single key (simple dev)
    single = _load_single_api_key()
    if single and hmac.compare_digest(single, provided):
        return

    # 3) Comma list (optional)
    keys_list = _load_api_keys_list()
    if keys_list and provided in keys_list:
        return

    # 4) Nothing configured -> server misconfigured
    if not single and not keys_list and not keys_map:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: set API_KEYS_JSON or API_KEY (or API_KEYS).",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    )


async def get_principal(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> Principal:
    tenant_id = _require_tenant(x_tenant_id)

    mode = (os.getenv("AUTH_MODE", "api_key") or "api_key").lower().strip()

    if mode == "none":
        return Principal(tenant_id=tenant_id, auth_mode="none")

    if mode == "api_key":
        _require_api_key(tenant_id=tenant_id, x_api_key=x_api_key)
        return Principal(tenant_id=tenant_id, auth_mode="api_key")

    if mode == "firebase":
        # Stub: validación real mañana (JWT verify + claims)
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Bearer token",
            )
        return Principal(tenant_id=tenant_id, auth_mode="firebase")

    raise HTTPException(status_code=500, detail=f"Unknown AUTH_MODE={mode}")
