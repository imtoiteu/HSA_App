import logging
import re

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import auth
from ..config import get_settings
from ..db import get_db
from ..mailer import send_mail
from ..models import User
from .common import err, user_public

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class ForgotIn(BaseModel):
    email: str = Field(max_length=254)


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


class ChangePwIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


def _tok(request: Request, token: str) -> str | None:
    """The raw token is only returned to non-browser clients (they send X-Client-Type: mobile);
    browsers keep it in the HttpOnly cookie, out of reach of scripts."""
    return token if request.headers.get("x-client-type") == "mobile" else None


def _me(user: User, request: Request) -> dict:
    d = user_public(user)
    s = getattr(request.state, "auth_session", None)
    d["csrf_token"] = s.csrf_token if s else None
    return d


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    auth.rate_limit(db, f"register:{auth.client_ip(request)}", 10, 3600)
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise err(422, "invalid_email", "Email không hợp lệ.")
    prob = auth.password_problem(body.password)
    if prob:
        raise err(422, "weak_password", prob)
    if auth.get_user_by_email(db, email):
        raise err(409, "email_taken", "Email này đã được đăng ký.")
    u = User(email=email, display_name=body.display_name.strip(), password_hash=auth.hash_password(body.password),
             role="student")
    db.add(u)
    db.commit()
    token, row = auth.create_session(db, u, request, response)
    request.state.auth_session = row
    return {"user": _me(u, request), "token": _tok(request, token)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = auth.client_ip(request)
    auth.rate_limit(db, f"login-ip:{ip}", 30, 900)
    auth.rate_limit(db, f"login-email:{body.email.strip().lower()}", 10, 900)
    u = auth.get_user_by_email(db, body.email)
    if u is None:
        auth.dummy_verify()
        raise err(401, "bad_credentials", "Email hoặc mật khẩu không đúng.")
    if not auth.verify_password(body.password, u.password_hash) or not u.is_active:
        raise err(401, "bad_credentials", "Email hoặc mật khẩu không đúng.")
    token, row = auth.create_session(db, u, request, response)
    request.state.auth_session = row
    return {"user": _me(u, request), "token": _tok(request, token)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           user: User | None = Depends(auth.optional_user)):
    auth.logout(db, request, response)
    return {"ok": True}


@router.get("/me")
def me(request: Request, user: User | None = Depends(auth.optional_user)):
    return {"user": _me(user, request) if user else None}


@router.patch("/me")
def update_profile(body: ProfileIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(auth.current_user)):
    user.display_name = body.display_name.strip()
    db.commit()
    return {"user": _me(user, request)}


@router.post("/forgot-password")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    auth.rate_limit(db, f"forgot:{auth.client_ip(request)}", 5, 3600)
    u = auth.get_user_by_email(db, body.email)
    if u and u.is_active:
        token = auth.create_reset_token(db, u)
        link = f"{get_settings().public_base_url.rstrip('/')}/dat-lai-mat-khau?token={token}"
        sent = send_mail(u.email, "Đặt lại mật khẩu",
                         f"Xin chào {u.display_name},\n\nMở liên kết sau để đặt lại mật khẩu (hiệu lực 2 giờ):\n{link}\n\n"
                         "Nếu bạn không yêu cầu, hãy bỏ qua email này.")
        if not sent:
            log.warning("password reset requested for user %s; SMTP not configured — admin can issue a link", u.id)
    # identical answer whether or not the address exists
    return {"ok": True, "message": "Nếu email tồn tại, hướng dẫn đặt lại mật khẩu đã được gửi."}


@router.post("/reset-password")
def reset(body: ResetIn, request: Request, response: Response, db: Session = Depends(get_db)):
    auth.rate_limit(db, f"reset:{auth.client_ip(request)}", 20, 3600)
    prob = auth.password_problem(body.password)
    if prob:
        raise err(422, "weak_password", prob)
    u = auth.consume_reset_token(db, body.token)
    if u is None:
        raise err(400, "invalid_token", "Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.")
    u.password_hash = auth.hash_password(body.password)
    auth.revoke_all_sessions(db, u.id)
    db.commit()
    token, row = auth.create_session(db, u, request, response)
    request.state.auth_session = row
    return {"user": _me(u, request), "token": _tok(request, token)}


@router.post("/change-password")
def change_password(body: ChangePwIn, request: Request, response: Response, db: Session = Depends(get_db),
                    user: User = Depends(auth.current_user)):
    if not auth.verify_password(body.current_password, user.password_hash):
        raise err(400, "bad_password", "Mật khẩu hiện tại không đúng.")
    prob = auth.password_problem(body.new_password)
    if prob:
        raise err(422, "weak_password", prob)
    user.password_hash = auth.hash_password(body.new_password)
    auth.revoke_all_sessions(db, user.id)
    db.commit()
    token, row = auth.create_session(db, user, request, response)
    request.state.auth_session = row
    return {"user": _me(user, request), "token": _tok(request, token)}
