# Payments & access model

The app sells two independent things.

| | what it unlocks | how it is bought |
|---|---|---|
| **Practice plan**: FREE / PRO (`plan`, `plan_subscription`) | FREE practises a fixed pool of *N* questions per subject. PRO practises the whole served bank for the plan's duration. | an order of kind `pro` |
| **Mock exams** (`exam_blueprint.access`) | one purchase gives *k* attempts of a paid exam; free exams are open to everyone | an order of kind `exam` (one exam) or `product` (attempt bundles/passes) |

A PRO plan does **not** unlock paid mock exams unless an admin enables
`mock_exams.pro_includes_paid_exams`.

## Changing prices and limits (Admin UI, no code change)

| what | where |
|---|---|
| FREE questions per subject (default 100) | Admin → Cài đặt → **Luyện tập (gói Miễn phí)**: “Số câu / môn” (presets 50/100/200/500) |
| PRO price (default 300 000 VND), duration (default 365 days), benefits, on sale | Admin → **Gói luyện tập & giá** → Gói Pro → Sửa gói |
| Default price of a paid mock exam (default 20 000 VND), paid exams on/off, PRO includes paid exams | Admin → Cài đặt → **Đề thi thử** |
| One exam: free/paid, own price, promotional price, attempts per purchase, published | Admin → **Đề thi & giá** → (exam) → Truy cập / Giá / Giá khuyến mãi / Số lượt mỗi lần mua |
| Destination bank, account number, account holder, transfer prefix, order expiry, QR on/off, extra instructions, payments on/off | Admin → Cài đặt → **Thanh toán** (“Điền sẵn MB Bank” fills BIN 970422) |

Every change is validated server-side, saved with its time and author, and written to the audit log
(before → after). New orders use the new values. Existing orders keep the amount, reference, product
snapshot and destination account they were created with (enforced by the database trigger
`trg_order_immutable`), and a running PRO period keeps its dates.

## FREE practice pool

`backend/app/commerce/access.py`. For each subject, the pool is chosen among questions that are
**already served to students** (never a non-eligible question). Units, meaning a shared passage
group or a single question, are ranked by `sha256("free|<subject>|<unit>")` and taken greedily while
they fit the limit. The pool is therefore the same for every FREE account, stable across requests,
and keeps passages whole. A subject with fewer served questions than the limit is fully open.

The backend applies the pool to every practice request of a FREE account:

- any subjects or types;
- any `source` (all, unseen, wrong or bookmarks);
- any count.

A FREE account cannot reach other questions by changing parameters, refreshing or calling the API
directly. When nothing in the pool fits the request, the API answers `402 free_limit` and the UI shows
an upgrade card instead of an error. Admins and PRO accounts are unrestricted.

## PRO subscriptions

A paid `pro` order creates one `plan_subscription` row (unique per order). The row holds the plan
snapshot (name, price, duration), `starts_at`, `expires_at`, `source` (order or admin_grant) and status.

- **Renewal:** a renewal starts when the current period ends, so no paid days are lost.
- **Expiry:** by time; the account returns to FREE with all history, results and bookmarks intact.
- **Admin changes:** an admin can grant PRO (days + mandatory reason) or revoke a period (mandatory
  reason). Revoking keeps the row, with who, when and why.
- **Refund:** refunding the order revokes the period it granted.

## Orders

| transition | trigger |
|---|---|
| `pending → paid` | webhook match or admin reconciliation |
| `pending → expired` | TTL, default 30 minutes |
| `expired → paid` | a late transfer with the right reference is still honoured |
| `pending → cancelled` | the student |
| `pending/expired → cancelled \| failed` | an admin, with a reason |
| `paid → refunded` | an admin |

Every transition (and every admin note) is a row in `payment_order_event`: from, to, source
(user/admin/webhook/system), actor, note.

An order has a unique code `HSA` + 8 unambiguous characters. The transfer content is `HSA <8 chars>`
and is matched even when banks add spaces or change case. The amount always comes from server-side
configuration: a client-sent price is ignored. The checkout page states that showing a QR code does
not mean the payment is verified.

## Confirmation

| provider | endpoint | authentication (env only) |
|---|---|---|
| SePay | `POST /api/payments/webhook/sepay` | `Authorization: Apikey $HSA_SEPAY_API_KEY` |
| Casso | `POST /api/payments/webhook/casso` | `Secure-Token: $HSA_CASSO_SECURE_TOKEN` |
| generic bridge | `POST /api/payments/webhook/generic` | `X-Signature: hex(HMAC-SHA256(body, $HSA_GENERIC_WEBHOOK_SECRET))`, body `{"txn_id","amount","content","occurred_at"?}` |
| manual | Admin → Thanh toán & đơn hàng → (order) → Xác nhận đã thanh toán | admin session |

Idempotency:

- The order row is locked and moves to `paid` once.
- `payment_transaction` is unique per `(provider, provider_txn_id)`.
- A manual confirmation is unique per order.
- Subscriptions and entitlements are unique per order.

A duplicate webhook or a second confirmation never grants twice. Transfers without a recognisable
code, underpaid, or for an already paid or cancelled order are kept as `unmatched`, `underpaid` or
`late` for review (Admin → Giao dịch ngân hàng → Gán vào đơn).

**Manual reconciliation** (the reliable fallback):

1. Search the order by code, user, reference, amount or date.
2. Check the amount and reference against the bank app.
3. Confirm with a note (required when the order is no longer pending).

The subscription or entitlement is created in the same transaction, and the confirming admin is
recorded.

The app never stores Internet Banking logins, passwords or OTPs and does not log in to or scrape any
bank. Provider secrets live only in environment variables.

## VietQR

The QR is generated locally (EMVCo/NAPAS 247 payload + CRC16, rendered as SVG) from the order's
destination snapshot: BIN, account, amount and the transfer content. No third-party QR service is
called. The account holder name is optional. When it is not configured, the checkout tells the payer
to check the name their banking app shows; the app never invents one.

## Adding a provider

1. Write an adapter in `backend/app/commerce/providers.py` that authenticates the request and returns
   `IncomingTxn` records.
2. Register it in `ADAPTERS`.
3. Add its secret to `config.Settings`.

Matching, idempotency and granting are provider-agnostic.
