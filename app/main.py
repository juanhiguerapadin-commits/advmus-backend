import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, error_body
from app.middlewares.request_id import RequestIdMiddleware
from app.routers.admin import router as admin_router
from app.routers.invoices import router as invoices_router

logger = logging.getLogger("app")

app = FastAPI(title="AdVMus API", version="0.1.0")
app.add_middleware(RequestIdMiddleware)


@app.middleware("http")
async def ensure_request_id_header(request: Request, call_next):
    """
    Garantiza que SIEMPRE:
    - exista request.state.request_id (para logs/errores)
    - vuelva X-Request-Id en el response
    """
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex}"
        request.state.request_id = rid

    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


def _err(code: str, message: str, request: Request, details=None):
    rid = getattr(request.state, "request_id", None)
    return error_body(
        code=code,
        message=message,
        request_id=rid or "req_unknown",
        details=details,
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content=_err(exc.code, exc.message, request, exc.details),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exc_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code_toggle=exc.status_code,
        content=_err("HTTP_ERROR", str(exc.detail), request),
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=_err(
            "VALIDATION_ERROR",
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
        content=_err("INTERNAL_ERROR", "Unexpected error", request),
    )


@app.get("/")
def root():
    return {"message": "AdVMus backend running. Go to /docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


# Routers
app.include_router(admin_router, prefix="/v1")
app.include_router(invoices_router, prefix="/v1")
