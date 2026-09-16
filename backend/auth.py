import hmac
import os
import time
from hashlib import sha256

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE_NAME = "ip_auth"
TOKEN_LIFETIME_SECONDS = 30 * 24 * 60 * 60  # 30 days

# If unset, the gate is fully disabled — local/personal use is unaffected. The
# public GCP deployment sets this via the systemd unit's environment.
ACCESS_PASSPHRASE = os.environ.get("ACCESS_PASSPHRASE")


def _secret() -> str:
    # Derived from the passphrase itself rather than a second env var — one
    # fewer thing to configure, and it's never exposed to the client anyway
    # (only the resulting signed token is).
    return ACCESS_PASSPHRASE or ""


def _sign(expiry: str) -> str:
    return hmac.new(_secret().encode(), expiry.encode(), sha256).hexdigest()


def _make_token() -> str:
    expiry = str(int(time.time()) + TOKEN_LIFETIME_SECONDS)
    return f"{expiry}.{_sign(expiry)}"


def _token_valid(token: str) -> bool:
    try:
        expiry, signature = token.split(".", 1)
    except ValueError:
        return False
    if int(expiry) < time.time():
        return False
    return hmac.compare_digest(signature, _sign(expiry))


class LoginRequest(BaseModel):
    passphrase: str


def login(req: LoginRequest):
    if not ACCESS_PASSPHRASE or not hmac.compare_digest(req.passphrase, ACCESS_PASSPHRASE):
        raise HTTPException(status_code=401, detail="Incorrect passphrase")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        _make_token(),
        max_age=TOKEN_LIFETIME_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


class PassphraseGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ACCESS_PASSPHRASE:
            return await call_next(request)

        path = request.url.path
        if not path.startswith("/api/") or path == "/api/login":
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        if not token or not _token_valid(token):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        return await call_next(request)
