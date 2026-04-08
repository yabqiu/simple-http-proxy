# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install as an executable command:**
```bash
uv tool install .
```

**Run directly after installation:**
```bash
simple-http-proxy                          # default: 127.0.0.1:9090
simple-http-proxy --host 0.0.0.0 --port 8080
simple-http-proxy --filter-port 443 --filter-dst 10.0.0.0/8
simple-http-proxy --format json --log-file custom.log
```

**Run without installing (during development):**
```bash
uv run simple-http-proxy
```

**Development setup:**
```bash
uv sync          # install dependencies
uv run python -m simple_http_proxy   # run directly from source
```

No test suite or linter is configured in this project.

## Architecture

The proxy is a single-process async HTTP forward proxy built on `aiohttp`. Traffic flows through these layers:

1. **`cli.py`** — Click CLI entry point; parses flags, calls `parse_filter_config()`, then `run_app()`
2. **`server.py`** — Core `proxy_handler()` coroutine: resolves DNS (in thread pool), evaluates filters, forwards request upstream, captures response, hands `TransactionRecord` to the collector
3. **`filters.py`** — `FilterConfig` dataclass + `matches()` function; filter logic is AND across types (port, src, dst) and OR within each type; empty config = allow all; returns 403 on mismatch
4. **`collector.py`** — `TransactionRecord` pairs a `RequestRecord` with an optional `ResponseRecord`; formats for three destinations: pretty (colorized, 4KB body truncation), JSON, and file (full bodies, appended)

### Key implementation details

- Bodies are capped at **1MB** during capture (`server.py`); display truncates further to **4KB** for pretty/JSON output
- Hop-by-hop headers are stripped before forwarding (defined as a frozenset in `server.py`)
- DNS resolution runs in a thread pool executor to avoid blocking the event loop
- Connection pool limit: 100; request timeout: 30s
- Upstream errors return 502; filtered connections return 403
- Log file is appended (not overwritten) on each run; transactions are sequence-numbered
