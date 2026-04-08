import asyncio
import socket
import time
from datetime import datetime, timezone

import aiohttp
from aiohttp import web

from .collector import Collector, RequestRecord, ResponseRecord, TransactionRecord
from .filters import FilterConfig, matches

MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB capture cap

# Hop-by-hop headers that must not be forwarded
HOP_BY_HOP = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ]
)


async def _resolve_ip(host: str) -> str:
    """Resolve hostname to first IPv4 address (runs getaddrinfo in a thread)."""
    loop = asyncio.get_event_loop()
    infos = await loop.run_in_executor(
        None, lambda: socket.getaddrinfo(host, None, socket.AF_INET)
    )
    if not infos:
        raise OSError(f"Could not resolve {host!r}")
    return infos[0][4][0]


def _filter_headers(headers: "aiohttp.CIMultiDictProxy | dict") -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


async def proxy_handler(request: web.Request) -> web.Response:
    filter_config: FilterConfig = request.app["filter_config"]
    collector: Collector = request.app["collector"]
    session: aiohttp.ClientSession = request.app["session"]

    # Determine source
    src_peer = request.transport.get_extra_info("peername") if request.transport else None
    src_ip = src_peer[0] if src_peer else (request.remote or "0.0.0.0")
    src_port = src_peer[1] if src_peer else 0

    # Parse target
    url = request.url
    dst_host = url.host or ""
    dst_port = url.port or (443 if url.scheme == "https" else 80)

    # Resolve destination IP for filtering
    try:
        dst_ip = await _resolve_ip(dst_host)
    except OSError:
        dst_ip = dst_host  # fallback: use hostname as-is (filter may fail)

    # Apply filter
    if not matches(filter_config, src_ip, dst_ip, dst_port):
        return web.Response(
            status=403,
            text=f"Blocked by proxy filter (src={src_ip}, dst={dst_ip}:{dst_port})\n",
        )

    # Read request body (capped)
    req_body = await request.read()
    req_body_captured = req_body[:MAX_BODY_BYTES]

    # Build forwarded headers
    fwd_headers = _filter_headers(request.headers)
    fwd_headers["Host"] = dst_host if dst_port in (80, 443) else f"{dst_host}:{dst_port}"

    req_record = RequestRecord(
        timestamp=datetime.now(timezone.utc),
        src_ip=src_ip,
        src_port=src_port,
        method=request.method,
        url=str(request.url),
        headers=dict(request.headers),
        body=req_body_captured,
    )

    # Forward to upstream
    upstream_url = str(url)
    t_start = time.perf_counter()
    resp_record: ResponseRecord | None = None
    resp_body = b""
    resp_headers: dict[str, str] = {}
    status = 502
    reason = "Bad Gateway"

    try:
        async with session.request(
            request.method,
            upstream_url,
            headers=fwd_headers,
            data=req_body,
            allow_redirects=False,
        ) as upstream_resp:
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            resp_body_full = await upstream_resp.read()
            resp_body = resp_body_full[:MAX_BODY_BYTES]
            resp_headers_fwd = _filter_headers(upstream_resp.headers)
            resp_headers_all = dict(upstream_resp.headers)
            status = upstream_resp.status
            reason = upstream_resp.reason or ""

            resp_record = ResponseRecord(
                timestamp=datetime.now(timezone.utc),
                status_code=status,
                status_reason=reason,
                headers=resp_headers_all,
                body=resp_body,
                elapsed_ms=elapsed_ms,
            )

            txn = TransactionRecord(request=req_record, response=resp_record)
            collector.record(txn)

            return web.Response(
                status=status,
                reason=reason,
                headers=resp_headers_fwd,
                body=resp_body_full,
            )

    except aiohttp.ClientError as exc:
        collector.record(TransactionRecord(request=req_record, response=None))
        return web.Response(status=502, text=f"Upstream error: {exc}\n")


def build_app(filter_config: FilterConfig, collector: Collector) -> web.Application:
    app = web.Application()
    app["filter_config"] = filter_config
    app["collector"] = collector

    async def on_startup(app: web.Application) -> None:
        connector = aiohttp.TCPConnector(limit=100)
        app["session"] = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def on_cleanup(app: web.Application) -> None:
        await app["session"].close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    # Catch-all route for all methods
    app.router.add_route("*", "/{path_info:.*}", proxy_handler)

    return app


async def run_app(app: web.Application, host: str, port: int) -> None:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"Proxy listening on http://{host}:{port}")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
