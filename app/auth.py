# app/auth.py
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

from fastapi import Header, HTTPException, status

TENANT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

# Cache (por proceso) para no parsear env/JSON en cada request
_KEYS_LOADED = False
_API_KEYS_MAP: Dict[str, str] = {}
_SINGLE_API_KEY: Optional[str] = None
_API_KEYS_LIST: Set[str] = set()


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    auth_mode: str  # "api_key" | "firebase" | "none"


def _load_api_keys_map_from_env() -> Dict[str, str]:
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

    out: Dict[str, str] = {}
    for k, v in data.items():
        if k is None or v is None:
            continue
        kk = str(k).strip()
        vv = str(v).strip()
        if kk and vv:
            out[kk] = vv
    return out


def _load_single_api_key_from_env() -> Optional[str]:
    """
    Single API key (dev/simple).
    Env:
      API_KEY='KEY_LARGA_123...'
    """
    key = (os.getenv("API_KEY", "") or "").strip()
    return key or None


def _load_api_keys_list_from_env() -> Set[str]:
    """
    Optional: comma-separated keys.
    Env:
      API_KEYS='KEY1,KEY2'
    """
    raw = (os.getenv("API_KEYS", "") or "").strip()
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


def _get_keys_config() -> Tuple[Dict[str, str], Optional[str], Set[str]]:
    """
    Carga y cachea env vars de auth 1 sola vez por proceso.
    En Cloud Run esto es perfecto (env no cambia “en caliente”).
    """
    global _KEYS_LOADED, _API_KEYS_MAP, _SINGLE_API_KEY, _API_KEYS_LIST
    if not _KEYS_LOADED:
        _API_KEYS_MAP = _load_api_keys_map_from_env()
        _SINGLE_API_KEY = _load_single_api_key_from_env()
        _API_KEYS_LIST = _load_api_keys_list_from_env()
        _KEYS_LOADED = True
    return _API_KEYS_MAP, _SINGLE_API_KEY, _API_KEYS_LIST


def _require_tenant(x_tenant_id: Optional[str]) -> str:
    tenant_id = (x_tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Missing X-Tenant-Id")
    if not TENANT_RE.match(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-Id format")
    return tenant_id


def _require_api_key(tenant_id: str, x_api_key: Optional[str]) -> None:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key",
        )

    provided = str(x_api_key).strip()
    keys_map, single, keys_list = _get_keys_config()

    # 1) Multi-tenant map (preferred)
    if keys_map:
        expected = keys_map.get(tenant_id)
        if expected and hmac.compare_digest(str(expected), provided):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # 2) Single key (simple dev)
    if single and hmac.compare_digest(single, provided):
        return

    # 3) Comma list (optional) - compare_digest para consistencia
    if keys_list and any(hmac.compare_digest(k, provided) for k in keys_list):
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
        # Stub: validación real después (JWT verify + claims)
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Bearer token",
            )
        return Principal(tenant_id=tenant_id, auth_mode="firebase")

    raise HTTPException(status_code=500, detail=f"Unknown AUTH_MODE={mode}")
