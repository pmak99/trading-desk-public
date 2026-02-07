"""
HTTP middleware for Trading Desk 5.0.

Provides rate limiting, security headers, and request ID tracking.
"""

import ipaddress
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from src.core.logging import log, set_request_id
from src.api.state import rate_limiter


async def rate_limit_middleware(request: Request, call_next):
    """Rate limit requests by client IP (60 req/min per IP)."""
    # Extract client IP (Cloud Run sets X-Forwarded-For)
    # Validate IP format to reject spoofed non-IP values
    forwarded_for = request.headers.get("X-Forwarded-For")
    client_ip = "unknown"
    if forwarded_for:
        raw_ip = forwarded_for.split(",")[0].strip()
        try:
            ipaddress.ip_address(raw_ip)
            client_ip = raw_ip
        except ValueError:
            client_ip = request.client.host if request.client else "unknown"
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


async def limit_request_size(request: Request, call_next):
    """Reject requests with body larger than 1MB to prevent DoS."""
    max_size = 1_000_000  # 1MB
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_size:
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large. Maximum: 1MB."},
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
