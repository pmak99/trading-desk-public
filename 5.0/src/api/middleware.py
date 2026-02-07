"""
HTTP middleware for Trading Desk 5.0.

Provides rate limiting, security headers, and request ID tracking.
"""

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from src.core.logging import log, set_request_id
from src.api.state import rate_limiter


async def rate_limit_middleware(request: Request, call_next):
    """Rate limit requests by client IP (60 req/min per IP)."""
    # Extract client IP (Cloud Run sets X-Forwarded-For)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

    if not await rate_limiter.is_allowed(client_ip):
        log("warn", "Rate limit exceeded", client_ip=client_ip)
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Limit: 60 per minute."},
            headers={"Retry-After": "60"},
        )
    return await call_next(request)


async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"
    # XSS protection (legacy browsers)
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # HSTS - enforce HTTPS (Cloud Run provides TLS)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Content Security Policy - restrict resource loading
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    # Don't send referrer to external sites
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def add_request_id(request: Request, call_next):
    """Add request ID to all requests."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    set_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
