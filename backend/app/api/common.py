from fastapi import HTTPException


def err(status: int, code: str, message: str, **extra):
    return HTTPException(status, detail={"code": code, "message": message, **extra})


def page_params(page: int, size: int, max_size: int = 100) -> tuple[int, int]:
    page = max(1, page)
    size = max(1, min(max_size, size))
    return (page - 1) * size, size


def user_public(u) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role,
            "created_at": u.created_at.isoformat() if u.created_at else None}
