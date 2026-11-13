"""slowapi limiter and the friendly 429 response."""
from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

RATE_LIMIT_MESSAGE = "You're asking a little fast. Give it a minute and try again."


def build_limiter() -> Limiter:
    # Behind Lightsail's proxy, uvicorn --proxy-headers makes request.client the real IP.
    return Limiter(key_func=get_remote_address)


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited", "message": RATE_LIMIT_MESSAGE, "spoken": RATE_LIMIT_MESSAGE},
    )
