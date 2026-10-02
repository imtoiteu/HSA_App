import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .api import admin, admin_business, auth_routes, payments, student
from .config import get_settings
from .db import SessionLocal
from .logging_setup import setup_logging

setup_logging()
log = logging.getLogger("hsa.request")
settings = get_settings()

app = FastAPI(title="HSA-app API", version="1.0.0",
              docs_url=None if settings.is_production else "/api/docs",
              redoc_url=None, openapi_url=None if settings.is_production else "/api/openapi.json")


# compress JSON once in-process (nginx does not gzip /api; see frontend/nginx.conf)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error", extra={"method": request.method, "path": request.url.path,
                                                "request_id": rid})
        response = JSONResponse({"detail": {"code": "server_error", "message": "Lỗi hệ thống, vui lòng thử lại."}},
                                status_code=500)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    response.headers["X-Request-ID"] = rid
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    if request.url.path != "/api/health":
        log.info("%s %s %s", request.method, request.url.path, response.status_code,
                 extra={"method": request.method, "path": request.url.path, "status": response.status_code,
                        "ms": ms, "request_id": rid})
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": {
        "code": "validation", "message": "Dữ liệu gửi lên không hợp lệ.",
        "errors": [{"loc": ".".join(str(x) for x in e["loc"]), "msg": e["msg"]} for e in exc.errors()][:20]}})


@app.get("/api/health")
def health():
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    finally:
        db.close()
    return JSONResponse({"status": "ok" if ok else "degraded", "db": ok}, status_code=200 if ok else 503)


app.include_router(auth_routes.router)
app.include_router(student.router)
app.include_router(payments.router)
app.include_router(admin.router)
app.include_router(admin_business.router)

if not settings.is_production:
    # in production nginx serves /media straight from the volume
    from fastapi.staticfiles import StaticFiles
    settings.media_root.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(settings.media_root)), name="media")
