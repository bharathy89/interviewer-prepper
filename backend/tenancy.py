import uuid

from fastapi import Request

# Lightweight placeholder multi-tenancy: an anonymous per-browser id, not real
# login. Good enough to prove session data is properly isolated per user (see
# main.py's /api/sessions leak this replaces) without building Firebase yet —
# swap this cookie for a verified Firebase UID later without touching the
# isolation logic itself, since callers only ever see a plain tenant_id string.
COOKIE_NAME = "ip_tenant"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year


def get_or_assign(request: Request) -> tuple[str, bool]:
    """Returns (tenant_id, is_new). is_new means the caller should set the cookie."""
    existing = request.cookies.get(COOKIE_NAME)
    if existing:
        return existing, False
    return str(uuid.uuid4()), True
