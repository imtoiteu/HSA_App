"""VietQR (NAPAS 247, EMVCo merchant-presented QR) payload generation — no external service.

The payload encodes: receiving bank BIN + account, amount (VND) and the transfer content that
carries the order reference code. Every Vietnamese banking app can scan it.
"""
import io
import re
import unicodedata

NAPAS_GUID = "A000000727"
SERVICE_TO_ACCOUNT = "QRIBFTTA"


def _tlv(tag: str, value: str) -> str:
    if len(value) > 99:
        raise ValueError(f"EMV field {tag} too long")
    return f"{tag}{len(value):02d}{value}"


def crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for b in data.encode("utf-8"):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def ascii_content(s: str, limit: int = 50) -> str:
    """Transfer content must be plain ASCII (banks strip diacritics/special characters)."""
    s = unicodedata.normalize("NFD", s).replace("đ", "d").replace("Đ", "D")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^A-Za-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:limit]


def build_payload(bank_bin: str, account_number: str, amount_vnd: int | None, content: str) -> str:
    if not re.fullmatch(r"\d{6}", bank_bin or ""):
        raise ValueError("bank BIN must be 6 digits")
    if not re.fullmatch(r"[0-9A-Za-z]{1,19}", account_number or ""):
        raise ValueError("invalid account number")
    beneficiary = _tlv("00", bank_bin) + _tlv("01", account_number)
    mai = _tlv("00", NAPAS_GUID) + _tlv("01", beneficiary) + _tlv("02", SERVICE_TO_ACCOUNT)
    p = _tlv("00", "01") + _tlv("01", "12" if amount_vnd else "11") + _tlv("38", mai) + _tlv("53", "704")
    if amount_vnd:
        p += _tlv("54", str(int(amount_vnd)))
    p += _tlv("58", "VN")
    info = ascii_content(content)
    if info:
        p += _tlv("62", _tlv("08", info))
    p += "6304"
    return p + crc16_ccitt(p)


def qr_svg(payload: str) -> str:
    import segno
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111827", xmldecl=False,
                                        svgns=True, nl=False)
    return buf.getvalue().decode("utf-8")
