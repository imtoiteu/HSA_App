"""Payment confirmation providers. Each adapter authenticates a webhook and normalises its payload
into `IncomingTxn` records; matching/confirmation is provider-agnostic (commerce.service).

Secrets come from the environment only (HSA_SEPAY_API_KEY, HSA_CASSO_SECURE_TOKEN,
HSA_GENERIC_WEBHOOK_SECRET). A provider without a configured secret rejects every call.
"""
import datetime as dt
import hashlib
import hmac
from dataclasses import dataclass


class ProviderAuthError(Exception):
    pass


@dataclass
class IncomingTxn:
    provider: str
    provider_txn_id: str
    amount_vnd: int
    content: str
    occurred_at: dt.datetime | None
    raw: dict
    incoming: bool = True  # outgoing transfers are ignored


def _parse_time(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            t = dt.datetime.strptime(str(s)[:26], fmt)
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone(dt.timedelta(hours=7)))  # Vietnamese banks report ICT
            return t
        except ValueError:
            continue
    return None


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def sepay(headers: dict, body: bytes, payload: dict, secret: str) -> list[IncomingTxn]:
    """SePay bank-transfer webhook. Auth: `Authorization: Apikey <key>`."""
    auth = headers.get("authorization", "")
    if not secret or not _eq(auth.removeprefix("Apikey ").removeprefix("apikey ").strip(), secret):
        raise ProviderAuthError("invalid SePay API key")
    return [IncomingTxn("sepay", str(payload["id"]), int(payload.get("transferAmount") or 0),
                        f"{payload.get('content') or ''} {payload.get('description') or ''}",
                        _parse_time(payload.get("transactionDate")), payload,
                        incoming=(payload.get("transferType", "in") == "in"))]


def casso(headers: dict, body: bytes, payload: dict, secret: str) -> list[IncomingTxn]:
    """Casso webhook (secure-token variant). Auth header `Secure-Token: <token>`."""
    if not secret or not _eq(headers.get("secure-token", ""), secret):
        raise ProviderAuthError("invalid Casso secure token")
    rows = payload.get("data") or []
    if isinstance(rows, dict):
        rows = [rows]
    out = []
    for r in rows:
        amt = int(r.get("amount") or 0)
        out.append(IncomingTxn("casso", str(r.get("tid") or r.get("id")), abs(amt), r.get("description") or "",
                               _parse_time(r.get("when") or r.get("transactionDateTime")), r, incoming=amt > 0))
    return out


def generic(headers: dict, body: bytes, payload: dict, secret: str) -> list[IncomingTxn]:
    """Generic signed webhook for any bank/aggregator bridge.
    Header `X-Signature: hex(HMAC-SHA256(raw body, HSA_GENERIC_WEBHOOK_SECRET))`;
    body {"txn_id", "amount", "content", "occurred_at"?}."""
    if not secret:
        raise ProviderAuthError("generic webhook disabled")
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not _eq(headers.get("x-signature", "").lower(), sig):
        raise ProviderAuthError("invalid signature")
    return [IncomingTxn("generic", str(payload["txn_id"]), int(payload["amount"]), payload.get("content") or "",
                        _parse_time(payload.get("occurred_at")), payload)]


ADAPTERS = {"sepay": sepay, "casso": casso, "generic": generic}
