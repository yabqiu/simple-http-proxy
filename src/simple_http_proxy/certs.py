"""Per-host certificate generation via openssl subprocess for HTTPS interception."""

import asyncio
import os
import random
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path


def _gen_host_cert(hostname: str, ca_cert: str, ca_key: str) -> tuple[str, str]:
    """
    Call openssl to produce a 2048-bit key and a leaf cert for *hostname*,
    signed by the given CA.  Returns (cert_path, key_path) inside a temp dir;
    caller must remove that directory when done.
    """
    tmpdir = tempfile.mkdtemp(prefix="shp-certs-")
    key_file = os.path.join(tmpdir, "host.key")
    csr_file = os.path.join(tmpdir, "host.csr")
    cert_file = os.path.join(tmpdir, "host.crt")
    conf_file = os.path.join(tmpdir, "host.cnf")

    Path(conf_file).write_text(
        "[req]\n"
        "req_extensions = v3_req\n"
        "distinguished_name = dn\n"
        "prompt = no\n"
        f"[dn]\nCN = {hostname}\n"
        f"[v3_req]\nsubjectAltName = DNS:{hostname}\n"
    )

    subprocess.run(
        ["openssl", "genrsa", "-out", key_file, "2048"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["openssl", "req", "-new", "-key", key_file, "-out", csr_file, "-config", conf_file],
        check=True, capture_output=True,
    )
    subprocess.run(
        [
            "openssl", "x509", "-req", "-days", "365",
            "-in", csr_file,
            "-CA", ca_cert, "-CAkey", ca_key,
            "-set_serial", str(random.randint(1, 2**31)),
            "-out", cert_file,
            "-extfile", conf_file, "-extensions", "v3_req",
        ],
        check=True, capture_output=True,
    )
    return cert_file, key_file


class CertManager:
    """Generates and caches per-host SSL server contexts for HTTPS interception."""

    def __init__(self, ca_cert_path: str, ca_key_path: str) -> None:
        self._ca_cert = ca_cert_path
        self._ca_key = ca_key_path
        self._cache: dict[str, ssl.SSLContext] = {}

    async def get_ssl_ctx(self, hostname: str) -> ssl.SSLContext:
        if hostname not in self._cache:
            loop = asyncio.get_running_loop()
            ctx = await loop.run_in_executor(None, self._make_ctx, hostname)
            self._cache[hostname] = ctx
        return self._cache[hostname]

    def _make_ctx(self, hostname: str) -> ssl.SSLContext:
        cert_file, key_file = _gen_host_cert(hostname, self._ca_cert, self._ca_key)
        tmpdir = os.path.dirname(cert_file)
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert_file, key_file)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return ctx
