from datetime import date
from typing import Optional

# ---------- Workflow mínimo ----------
ALLOWED_STATUSES = {"uploaded", "processing", "parsed", "failed"}

# Transiciones permitidas (workflow mínimo)
ALLOWED_TRANSITIONS = {
    "uploaded": {"processing"},
    "processing": {"parsed", "failed"},
    "parsed": set(),
    "failed": set(),
}

# ---------- Pydantic compat (v1/v2) ----------
try:
    # pydantic v2
    from pydantic import BaseModel, Field, ConfigDict
except ImportError:
    # pydantic v1
    from pydantic import BaseModel, Field  # type: ignore
    ConfigDict = None  # type: ignore


class InvoicePatch(BaseModel):
    # Editable metadata
    status: Optional[str] = None
    supplier: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    due_date: Optional[date] = None
    note: Optional[str] = None

    # No permitir campos inesperados
    if ConfigDict is not None:
        model_config = ConfigDict(extra="forbid")  # pydantic v2
    else:

        class Config:  # pydantic v1
            extra = "forbid"
