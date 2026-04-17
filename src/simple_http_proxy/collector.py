import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import click


@dataclass
class RequestRecord:
    timestamp: datetime
    src_ip: str
    src_port: int
    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    is_https: bool = False


@dataclass
class ResponseRecord:
    timestamp: datetime
    status_code: int
    status_reason: str
    headers: dict[str, str]
    body: bytes
    elapsed_ms: float


@dataclass
class TransactionRecord:
    request: RequestRecord
    response: Optional[ResponseRecord] = None


_SEP = "─" * 60
_MAX_BODY_DISPLAY = 4 * 1024  # 4 KB for display


def _decode_body(body: bytes, max_bytes: int | None = _MAX_BODY_DISPLAY) -> str:
    if not body:
        return "(no body)"
    if max_bytes is not None and len(body) > max_bytes:
        prefix = body[:max_bytes].decode("utf-8", errors="replace")
        return f"{prefix}\n... [{len(body) - max_bytes} more bytes truncated]"
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return f"<binary: {len(body)} bytes>"


class Collector:
    def __init__(self, output_format: str = "pretty", log_file: str | None = None) -> None:
        self._format = output_format
        self._log_fh = open(log_file, "a", encoding="utf-8") if log_file else None
        self._seq = 0

    def record(self, txn: TransactionRecord) -> None:
        self._seq += 1
        seq = self._seq
        if self._format == "json":
            print(self._format_json(txn, seq), flush=True)
        else:
            print(self._format_pretty(txn, seq), flush=True)

        if self._log_fh is not None:
            self._log_fh.write(self._format_file(txn, seq))
            self._log_fh.write("\n")
            self._log_fh.flush()

    def _format_pretty(self, txn: TransactionRecord, seq: int) -> str:
        req = txn.request
        req_color = "magenta" if req.is_https else "cyan"
        lines: list[str] = []

        ts = req.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        proto = "HTTPS" if req.is_https else "HTTP"
        lines.append(f"\n{click.style(_SEP, fg=req_color)}")
        lines.append(click.style(
            f"REQUEST [{proto}]  #{seq}  {ts}  {req.src_ip}:{req.src_port}",
            fg=req_color, bold=True,
        ))
        lines.append(click.style(_SEP, fg=req_color))
        lines.append(f"{req.method} {req.url}")
        for k, v in req.headers.items():
            lines.append(f"{k}: {v}")
        lines.append("")
        lines.append(_decode_body(req.body))

        if txn.response is not None:
            resp = txn.response
            resp_color = "yellow" if req.is_https else "green"
            lines.append(f"\n{click.style(_SEP, fg=resp_color)}")
            lines.append(click.style(
                f"RESPONSE [{proto}]  #{seq}  +{resp.elapsed_ms:.1f}ms",
                fg=resp_color, bold=True,
            ))
            lines.append(click.style(_SEP, fg=resp_color))
            lines.append(f"{resp.status_code} {resp.status_reason}")
            for k, v in resp.headers.items():
                lines.append(f"{k}: {v}")
            lines.append("")
            lines.append(_decode_body(resp.body))
        else:
            lines.append(f"\n{click.style(_SEP, fg='red')}")
            lines.append(click.style(f"RESPONSE [{proto}]  #{seq}  (none — upstream error)", fg="red", bold=True))

        lines.append(_SEP)
        return "\n".join(lines)

    def _format_file(self, txn: TransactionRecord, seq: int) -> str:
        """Plain text with full (untruncated) bodies, no color codes."""
        req = txn.request
        lines: list[str] = []

        ts = req.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        proto = "HTTPS" if req.is_https else "HTTP"
        lines.append(f"\n{_SEP}")
        lines.append(f"REQUEST [{proto}]  #{seq}  {ts}  {req.src_ip}:{req.src_port}")
        lines.append(_SEP)
        lines.append(f"{req.method} {req.url}")
        for k, v in req.headers.items():
            lines.append(f"{k}: {v}")
        lines.append("")
        lines.append(_decode_body(req.body, max_bytes=None))

        if txn.response is not None:
            resp = txn.response
            lines.append(f"\n{_SEP}")
            lines.append(f"RESPONSE [{proto}]  #{seq}  +{resp.elapsed_ms:.1f}ms")
            lines.append(_SEP)
            lines.append(f"{resp.status_code} {resp.status_reason}")
            for k, v in resp.headers.items():
                lines.append(f"{k}: {v}")
            lines.append("")
            lines.append(_decode_body(resp.body, max_bytes=None))
        else:
            lines.append(f"\n{_SEP}")
            lines.append(f"RESPONSE [{proto}]  #{seq}  (none — upstream error)")

        lines.append(_SEP)
        return "\n".join(lines)

    def _format_json(self, txn: TransactionRecord, seq: int) -> str:
        req = txn.request
        data: dict = {
            "seq": seq,
            "ts": req.timestamp.isoformat(),
            "protocol": "HTTPS" if req.is_https else "HTTP",
            "src": f"{req.src_ip}:{req.src_port}",
            "request": {
                "method": req.method,
                "url": req.url,
                "headers": req.headers,
                "body": _decode_body(req.body),
            },
        }
        if txn.response is not None:
            resp = txn.response
            data["response"] = {
                "status": resp.status_code,
                "reason": resp.status_reason,
                "headers": resp.headers,
                "body": _decode_body(resp.body),
                "elapsed_ms": round(resp.elapsed_ms, 2),
            }
        else:
            data["response"] = None
        return json.dumps(data, ensure_ascii=False)
