from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    EXEMPT = {"/health", "/health/db", "/events/stream", "/fhir/events/observation"}

    def __init__(self, app, requests_per_minute: int = 600):
        super().__init__(app)
        self.limit = max(20, int(requests_per_minute))
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.lock = Lock()

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT:
            return await call_next(request)
        # Detrás de cloudflared + Nginx todas las peticiones llegan desde la IP
        # del proxy. Se usa la IP real del cliente para no compartir un único
        # cupo entre todos los usuarios.
        ip = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-real-ip")
            or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
            or (request.client.host if request.client else "unknown")
        )
        now = time.monotonic()
        with self.lock:
            q = self.events[ip]
            while q and q[0] < now - 60:
                q.popleft()
            if len(q) >= self.limit:
                return JSONResponse(status_code=429, content={"detail": "Demasiadas solicitudes. Intente más tarde."})
            q.append(now)
        return await call_next(request)
