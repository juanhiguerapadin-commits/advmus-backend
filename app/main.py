import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError
from app.middlewares.request_id import RequestIdMiddleware
from app.routers.invoices import router as invoices_router

logger = logging.getLogger("app")

app = FastAPI(title="AdVMus API", version="0.1.0")

# --- CORS (para que el front pueda llamar al backend desde el browser) ---
# Podés sobreescribirlo por env var:
# ADVMUS_CORS_ORIGINS="https://tu-front.com,http://localhost:5500"
cors_env = os.getenv("ADVMUS_CORS_ORIGINS", "").strip()
if cors_env:
    origins = [o.strip() for o in cors_env.split(",") if o.strip()]
else:
    origins = [
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        # dev futuros (por si migran a Vite/Next)
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],  # Authorization + X-Tenant-Id + Content-Type, etc.
    expose_headers=["X-Request-Id"],  # para que el front pueda leerlo
)

# RequestId después de CORS (CORS debe poder responder preflight)
app.add_middleware(RequestIdMiddleware)


def _err(code: str, message: str, request: Request, details=None):
    rid = getattr(request.state, "request_id", None)
    payload = {"error": {"code": code, "message": message, "request_id": rid}}
    if details:
        payload["error"]["details"] = details
    return payload


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content=_err(exc.code, exc.message, request, exc.details),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exc_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=_err("http_error", str(exc.detail), request),
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=_err(
            "validation_error",
            "Invalid request",
            request,
            {"errors": exc.errors()},
        ),
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception("unhandled_exception")
    return JSONResponse(
        status_code=500,
        content=_err("internal_error", "Unexpected error", request),
    )


@app.get("/")
def root():
    return {"message": "AdVMus backend running. Go to /docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(invoices_router, prefix="/v1")
