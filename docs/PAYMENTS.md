# Payments & access model

## Access

* A blueprint with `price_vnd = 0` is free. With `price_vnd > 0`, each attempt consumes one
  **entitlement attempt** (row-locked inside the same transaction that creates the session). A
  running attempt is resumed, never charged twice.
* Entitlements come from paid orders (a single attempt of one exam, or an admin-defined
  **product**: N attempts and/or a validity period, for all paid exams or selected ones) or from
  admin grants. Admins can revoke them; refunds revoke automatically.
* Practice modes are free by default (`app_setting.practice.free`).

## Orders

`pending → paid` (webhook match or admin reconciliation) · `pending → expired` (TTL, default 30 min;
a late transfer with the right code is still honoured) · `pending → cancelled` (by the student) ·
`paid → refunded` (admin). Each order has a unique reference code (`HSA` + 8 unambiguous
characters) which the student puts in the transfer description; codes are found even when banks
insert spaces or change case.

## VietQR

The QR is generated locally (EMVCo/NAPAS 247 payload + CRC16, rendered as SVG) from the receiving
account configured in Admin → Cài đặt → Thanh toán (bank BIN, account number, account name — public
information, not secrets). No third-party QR service is called.

## Confirmation providers

| provider | endpoint | authentication (env only) |
|---|---|---|
| SePay | `POST /api/payments/webhook/sepay` | `Authorization: Apikey $HSA_SEPAY_API_KEY` |
| Casso | `POST /api/payments/webhook/casso` | `Secure-Token: $HSA_CASSO_SECURE_TOKEN` |
| generic bridge | `POST /api/payments/webhook/generic` | `X-Signature: hex(HMAC-SHA256(body, $HSA_GENERIC_WEBHOOK_SECRET))`, body `{"txn_id","amount","content","occurred_at"?}` |
| manual | Admin → Đơn hàng → Xác nhận | admin session |

A provider must be enabled in the payment settings **and** have its secret configured, otherwise
its webhook answers 404/401. Every notification is stored in `payment_transaction` with a unique
`(provider, provider_txn_id)`: repeated deliveries are no-ops, an order can move to `paid` only once
(conditional update + one entitlement per order). Transfers without a recognisable code, with a
smaller amount, or for an already-paid/cancelled order are kept as `unmatched` / `underpaid` /
`late` for the admin to reconcile (Admin → Giao dịch → Gán vào đơn). Every financial action is in
the audit log.

## Adding a provider

Write an adapter in `backend/app/commerce/providers.py` that authenticates the request and returns
`IncomingTxn` records, register it in `ADAPTERS`, add its secret to `config.Settings`. Matching,
idempotency and entitlements are provider-agnostic.
