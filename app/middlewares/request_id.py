import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    header_name = "X-Request-Id"

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(self.header_name) or f"req_{uuid.uuid4().hex}"
        request.state.request_id = rid

        response: Response = await call_next(request)

        # No pisar si ya existe
        if self.header_name not in response.headers:
            response.headers[self.header_name] = rid

        return response
