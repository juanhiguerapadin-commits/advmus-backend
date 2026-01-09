import json
import logging
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"
logger = logging.getLogger("app")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = rid

        t0 = time.time()
        logger.info(
            json.dumps(
                {
                    "event": "request_start",
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                }
            )
        )

        response: Response = await call_next(request)

        ms = int((time.time() - t0) * 1000)
        logger.info(
            json.dumps(
                {
                    "event": "request_end",
                    "request_id": rid,
                    "status_code": response.status_code,
                    "ms": ms,
                }
            )
        )

        response.headers[REQUEST_ID_HEADER] = rid
        return response
