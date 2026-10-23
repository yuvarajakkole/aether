import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id.get()
        if rid:
            out["request_id"] = rid
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()}"
        extras = {k: v for k, v in record.__dict__.items() if k not in _STD and not k.startswith("_")}
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
