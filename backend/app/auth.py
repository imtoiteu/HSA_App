"""Authentication: argon2id passwords, opaque server-side sessions, CSRF, rate limiting.

A session token is sent either as the HttpOnly cookie `hsa_session` (web) or as
`Authorization: Bearer <token>` (mobile/API clients). Only its SHA-256 is stored. Cookie-
authenticated state-changing requests must carry `X-CSRF-Token` equal to the session's CSRF
token (double-submit; the token is also readable from the `hsa_csrf` cookie).
"""
import datetime as dt
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuthSession, PasswordReset, User

COOKIE = "hsa_session"
CSRF_COOKIE = "hsa_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_ph = PasswordHasher(time_cost=3, memory_cost=19456, parallelism=1)  # ~20 MB; OWASP argon2id baseline


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


_DUMMY_HASH = None


def dummy_verify():
    """Equalise timing for unknown e-mails."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-for-timing")
    verify_password("x", _DUMMY_HASH)


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def password_problem(pw: str) -> str | None:
    if len(pw) < 8:
        return "Mật khẩu cần ít nhất 8 ký tự."
    if len(pw) > 200:
        return "Mật khẩu quá dài."
    if pw.isdigit() or pw.lower() in {"password", "12345678", "matkhau123", "qwertyui"}:
        return "Mật khẩu quá dễ đoán."
    return None


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy_headers:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()[:64]
        xr = request.headers.get("x-real-ip")
        if xr:
            return xr.strip()[:64]
    return (request.client.host if request.client else "?")[:64]


def create_session(db: Session, user: User, request: Request, response: Response | None) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    s = get_settings()
    row = AuthSession(token_hash=sha(token), user_id=user.id, csrf_token=secrets.token_urlsafe(24),
                      expires_at=utcnow() + dt.timedelta(days=s.session_days), ip=client_ip(request),
                      user_agent=(request.headers.get("user-agent") or "")[:300])
    db.add(row)
    user.last_login_at = utcnow()
    db.commit()
    if response is not None:
        set_cookies(response, token, row.csrf_token)
    return token, row


def set_cookies(response: Response, token: str, csrf: str):
    s = get_settings()
    age = s.session_days * 86400
    response.set_cookie(COOKIE, token, max_age=age, httponly=True, secure=s.cookie_secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=age, httponly=False, secure=s.cookie_secure, samesite="lax",
                        path="/")


def clear_cookies(response: Response):
    response.delete_cookie(COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _token_from(request: Request) -> tuple[str | None, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), True
    return request.cookies.get(COOKIE), False


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token, bearer = _token_from(request)
    if not token:
        return None
    row = db.get(AuthSession, sha(token))
    now = utcnow()
    if row is None or row.expires_at <= now or not row.user.is_active:
        return None
    if not bearer and request.method not in SAFE_METHODS:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), row.csrf_token):
            raise HTTPException(403, detail={"code": "csrf", "message": "Phiên làm việc không hợp lệ, hãy tải lại trang."})
    if row.last_seen_at is None or now - row.last_seen_at > dt.timedelta(minutes=10):
        row.last_seen_at = now
        db.commit()
    request.state.auth_session = row
    return row.user


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Vui lòng đăng nhập."})
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, detail={"code": "forbidden", "message": "Bạn không có quyền truy cập."})
    return user


def logout(db: Session, request: Request, response: Response):
    token, _ = _token_from(request)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == sha(token)))
        db.commit()
    clear_cookies(response)


def revoke_all_sessions(db: Session, user_id: int):
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


# ------------------------------------------------------------------------------------------------
# password reset
# ------------------------------------------------------------------------------------------------
def create_reset_token(db: Session, user: User, hours: int = 2) -> str:
    token = secrets.token_urlsafe(32)
    db.add(PasswordReset(token_hash=sha(token), user_id=user.id, expires_at=utcnow() + dt.timedelta(hours=hours)))
    db.commit()
    return token


def consume_reset_token(db: Session, token: str) -> User | None:
    row = db.get(PasswordReset, sha(token))
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        return None
    row.used_at = utcnow()
    return db.get(User, row.user_id)


# ------------------------------------------------------------------------------------------------
# rate limiting (fixed window, shared across workers through Postgres)
# ------------------------------------------------------------------------------------------------
def rate_limit(db: Session, key: str, limit: int, window_s: int):
    if not get_settings().rate_limits:
        return
    now = utcnow()
    start = dt.datetime.fromtimestamp(int(now.timestamp()) // window_s * window_s, dt.timezone.utc)
    n = db.scalar(text("INSERT INTO rate_limit(key, window_start, count) VALUES (:k, :w, 1) "
                       "ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limit.count + 1 "
                       "RETURNING count"), {"k": key[:200], "w": start})
    db.commit()
    if n > limit:
        raise HTTPException(429, detail={"code": "rate_limited",
                                         "message": "Bạn thao tác quá nhanh, vui lòng thử lại sau ít phút."},
                            headers={"Retry-After": str(window_s)})


def cleanup(db: Session):
    now = utcnow()
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    db.execute(delete(PasswordReset).where(PasswordReset.expires_at < now - dt.timedelta(days=7)))
    db.execute(text("DELETE FROM rate_limit WHERE window_start < now() - interval '1 day'"))
    db.commit()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower()))
