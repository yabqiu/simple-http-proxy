import asyncio
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp

from .certs import CertManager
from .collector import Collector, RequestRecord, ResponseRecord, TransactionRecord
from .filters import FilterConfig, matches

MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB capture cap

HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailers", "transfer-encoding", "upgrade",
])


async def _resolve_ip(host: str) -> str:
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(
        None, lambda: socket.getaddrinfo(host, None, socket.AF_INET)
    )
    if not infos:
        raise OSError(f"Could not resolve {host!r}")
    return infos[0][4][0]


def _filter_headers(headers: dict) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def _write_http(write_fn, status: int, reason: str, headers: dict, body: bytes) -> None:
    write_fn(f"HTTP/1.1 {status} {reason}\r\n".encode())
    for k, v in headers.items():
        if k.lower() != "content-length":
            write_fn(f"{k}: {v}\r\n".encode())
    write_fn(f"Content-Length: {len(body)}\r\n\r\n".encode())
    if body:
        write_fn(body)


async def _noop_drain() -> None:
    pass


class _ProxyServer:
    def __init__(
        self,
        filter_config: FilterConfig,
        collector: Collector,
        session: aiohttp.ClientSession,
        cert_manager: CertManager | None,
    ) -> None:
        self._filter = filter_config
        self._collector = collector
        self._session = session
        self._certs = cert_manager

    # ------------------------------------------------------------------ entry

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        src_ip = peer[0] if peer else "0.0.0.0"
        src_port = peer[1] if peer else 0
        try:
            await self._serve(reader, writer, src_ip, src_port)
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------ reading

    async def _read_head(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, str, dict[str, str]] | None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            return None
        if not line or line.strip() == b"":
            return None
        parts = line.decode("utf-8", errors="replace").strip().split(" ", 2)
        if len(parts) < 3:
            return None
        method, path, version = parts
        headers: dict[str, str] = {}
        while True:
            try:
                hline = await asyncio.wait_for(reader.readline(), timeout=30)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                return None
            if not hline or hline in (b"\r\n", b"\n"):
                break
            decoded = hline.decode("utf-8", errors="replace").strip()
            if ":" in decoded:
                k, _, v = decoded.partition(":")
                headers[k.strip().lower()] = v.strip()
        return method, path, version, headers

    async def _read_body(
        self, reader: asyncio.StreamReader, headers: dict[str, str]
    ) -> bytes:
        cl = headers.get("content-length")
        if cl:
            try:
                n = int(cl)
                if n > 0:
                    return await asyncio.wait_for(reader.read(n), timeout=30)
            except (ValueError, asyncio.TimeoutError):
                pass
            return b""
        if "chunked" in headers.get("transfer-encoding", "").lower():
            return await self._read_chunked(reader)
        return b""

    async def _read_chunked(self, reader: asyncio.StreamReader) -> bytes:
        parts: list[bytes] = []
        while True:
            try:
                size_line = await asyncio.wait_for(reader.readline(), timeout=30)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                break
            chunk_size = int(size_line.strip().split(b";")[0], 16)
            if chunk_size == 0:
                await reader.readline()
                break
            try:
                chunk = await asyncio.wait_for(reader.read(chunk_size), timeout=30)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                break
            parts.append(chunk)
            await reader.readline()
        return b"".join(parts)

    # ------------------------------------------------------------------ dispatch

    async def _serve(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        src_ip: str,
        src_port: int,
    ) -> None:
        while True:
            head = await self._read_head(reader)
            if head is None:
                break
            method, path, version, headers = head
            if method.upper() == "CONNECT":
                if self._certs:
                    await self._intercept_connect(reader, writer, path, src_ip, src_port)
                else:
                    await self._tunnel_connect(reader, writer, path, src_ip, src_port)
                break
            await self._forward(
                reader, writer.write, writer.drain,
                method, path, headers, src_ip, src_port,
                is_https=False,
            )
            if version == "HTTP/1.0" or "close" in headers.get("connection", "").lower():
                break
            if reader.at_eof():
                break

    # ------------------------------------------------------------------ CONNECT

    async def _tunnel_connect(
        self,
        cr: asyncio.StreamReader,
        cw: asyncio.StreamWriter,
        host_port: str,
        src_ip: str,
        src_port: int,
    ) -> None:
        host, _, ps = host_port.rpartition(":")
        port = int(ps) if ps else 443

        t0 = time.perf_counter()
        try:
            ur, uw = await asyncio.open_connection(host, port)
        except Exception as exc:
            cw.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await cw.drain()
            req = RequestRecord(
                timestamp=datetime.now(timezone.utc),
                src_ip=src_ip, src_port=src_port,
                method="CONNECT", url=host_port,
                headers={}, body=b"", is_https=True,
            )
            self._collector.record(TransactionRecord(req, None))
            return

        elapsed = (time.perf_counter() - t0) * 1000
        req = RequestRecord(
            timestamp=datetime.now(timezone.utc),
            src_ip=src_ip, src_port=src_port,
            method="CONNECT", url=host_port,
            headers={}, body=b"", is_https=True,
        )
        resp = ResponseRecord(
            timestamp=datetime.now(timezone.utc),
            status_code=200,
            status_reason="Connection Established (tunnel — body encrypted)",
            headers={}, body=b"", elapsed_ms=elapsed,
        )
        self._collector.record(TransactionRecord(req, resp))

        cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await cw.drain()

        async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            try:
                while not r.at_eof():
                    data = await r.read(65536)
                    if not data:
                        break
                    w.write(data)
                    await w.drain()
            except Exception:
                pass

        await asyncio.gather(pipe(cr, uw), pipe(ur, cw), return_exceptions=True)

    async def _intercept_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host_port: str,
        src_ip: str,
        src_port: int,
    ) -> None:
        host, _, ps = host_port.rpartition(":")
        port = int(ps) if ps else 443

        try:
            dst_ip = await _resolve_ip(host)
        except OSError:
            dst_ip = host

        if not matches(self._filter, src_ip, dst_ip, port):
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            await writer.drain()
            return

        # Generate cert BEFORE writing 200: while awaiting here the client has
        # not yet received 200, so it cannot send a TLS ClientHello yet.
        ssl_ctx = await self._certs.get_ssl_ctx(host)

        # Write 200 WITHOUT draining.  start_tls() calls transport.set_protocol()
        # synchronously (before yielding to the event loop), so by the time
        # the event loop flushes the 200 and the client replies with a
        # ClientHello, the SSL protocol is already in place.
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")

        loop = asyncio.get_running_loop()
        transport = writer.transport
        protocol = transport.get_protocol()

        try:
            new_transport = await loop.start_tls(
                transport, protocol, ssl_ctx,
                server_side=True, ssl_handshake_timeout=10,
            )
        except Exception:
            return

        # reader still receives TLS-decrypted data; write to new_transport directly
        while not reader.at_eof():
            head = await self._read_head(reader)
            if head is None:
                break
            method, path, version, headers = head
            port_sfx = f":{port}" if port != 443 else ""
            full_url = f"https://{host}{port_sfx}{path}"
            await self._forward(
                reader, new_transport.write, _noop_drain,
                method, full_url, headers, src_ip, src_port,
                is_https=True,
            )
            if version == "HTTP/1.0" or "close" in headers.get("connection", "").lower():
                break

    # ------------------------------------------------------------------ forward

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        write_fn,
        drain_fn,
        method: str,
        url: str,
        headers: dict[str, str],
        src_ip: str,
        src_port: int,
        is_https: bool,
    ) -> None:
        parsed = urlparse(url)
        dst_host = parsed.hostname or ""
        dst_port = parsed.port or (443 if is_https else 80)

        try:
            dst_ip = await _resolve_ip(dst_host)
        except OSError:
            dst_ip = dst_host

        if not matches(self._filter, src_ip, dst_ip, dst_port):
            err = f"Blocked by proxy filter (src={src_ip}, dst={dst_ip}:{dst_port})\n".encode()
            _write_http(write_fn, 403, "Forbidden", {}, err)
            await drain_fn()
            return

        body = await self._read_body(reader, headers)

        fwd_hdrs = _filter_headers(dict(headers))
        fwd_hdrs["Host"] = dst_host if dst_port in (80, 443) else f"{dst_host}:{dst_port}"

        req_record = RequestRecord(
            timestamp=datetime.now(timezone.utc),
            src_ip=src_ip,
            src_port=src_port,
            method=method,
            url=url,
            headers=dict(headers),
            body=body[:MAX_BODY_BYTES],
            is_https=is_https,
        )

        t0 = time.perf_counter()
        try:
            async with self._session.request(
                method, url,
                headers=fwd_hdrs,
                data=body,
                allow_redirects=False,
            ) as resp:
                elapsed = (time.perf_counter() - t0) * 1000
                resp_body = await resp.read()
                resp_record = ResponseRecord(
                    timestamp=datetime.now(timezone.utc),
                    status_code=resp.status,
                    status_reason=resp.reason or "",
                    headers=dict(resp.headers),
                    body=resp_body[:MAX_BODY_BYTES],
                    elapsed_ms=elapsed,
                )
                self._collector.record(TransactionRecord(req_record, resp_record))
                _write_http(
                    write_fn, resp.status, resp.reason or "",
                    _filter_headers(dict(resp.headers)), resp_body,
                )
                await drain_fn()
        except aiohttp.ClientError as exc:
            self._collector.record(TransactionRecord(req_record, None))
            _write_http(write_fn, 502, "Bad Gateway", {}, f"Upstream error: {exc}\n".encode())
            await drain_fn()


async def run_app(
    filter_config: FilterConfig,
    collector: Collector,
    host: str,
    port: int,
    cert_manager: CertManager | None = None,
) -> None:
    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=100),
        timeout=aiohttp.ClientTimeout(total=30),
    )
    srv = _ProxyServer(filter_config, collector, session, cert_manager)
    proto = "HTTP + HTTPS interception" if cert_manager else "HTTP only"
    print(f"Proxy listening on http://{host}:{port}  [{proto}]")
    try:
        server = await asyncio.start_server(srv.handle, host, port)
        async with server:
            await server.serve_forever()
    finally:
        await session.close()
