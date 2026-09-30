"""Structured (JSON-lines) logging to stdout; docker handles rotation."""
import json
import logging
import sys
import time

from .config import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z", "level": record.levelname,
             "logger": record.name, "msg": record.getMessage()}
        for k in ("method", "path", "status", "ms", "ip", "user_id", "request_id"):
            v = getattr(record, k, None)
            if v is not None:
                d[k] = v
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)


def setup_logging():
    s = get_settings()
    root = logging.getLogger()
    if getattr(root, "_hsa_configured", False):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter() if s.is_production else logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.handlers[:] = [h]
    root.setLevel(s.log_level)
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our request log
    root._hsa_configured = True
