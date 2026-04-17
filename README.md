# simple-http-proxy

An async HTTP forward proxy with traffic inspection and filtering.

## Installation

```bash
uv tool install .
```

## Usage

```
$ simple-http-proxy --help
Usage: simple-http-proxy [OPTIONS]

  HTTP forward proxy with traffic inspection and filtering.

  Configure your HTTP client to use this proxy, then all traffic will be
  captured and printed to stdout.

Options:
  --host TEXT             Interface to listen on.  [default: 127.0.0.1]
  --port INTEGER          Port to listen on.  [default: 9090]
  --filter-port PORT      Only proxy to this destination TCP port. Repeatable.
  --filter-src CIDR       Only proxy requests from this source IP or CIDR.
                          Repeatable.
  --filter-dst CIDR       Only proxy requests to this destination IP or CIDR.
                          Repeatable.
  --format [pretty|json]  Output format for captured traffic.  [default: pretty]
  --log-file FILE         Append full (untruncated) request/response to this
                          file.  [default: http-proxy.log]
  --help                  Show this message and exit.
```

Configure your HTTP client to use this proxy, then all traffic will be captured and printed to stdout.

### Options

| Option | Default | Description |
|---|---|---|
| `--host HOST` | `127.0.0.1` | Interface to listen on |
| `--port PORT` | `9090` | Port to listen on |
| `--filter-port PORT` | _(none)_ | Only proxy to this destination TCP port (repeatable) |
| `--filter-src CIDR` | _(none)_ | Only proxy requests from this source IP or CIDR (repeatable) |
| `--filter-dst CIDR` | _(none)_ | Only proxy requests to this destination IP or CIDR (repeatable) |
| `--format [pretty\|json]` | `pretty` | Output format for captured traffic |
| `--log-file PATH` | `http-proxy.log` | Append full (untruncated) request/response to this file |

### Examples

```bash
# Start with defaults (listen on 127.0.0.1:9090)
simple-http-proxy

# Listen on all interfaces, port 8080
simple-http-proxy --host 0.0.0.0 --port 8080

# Only capture traffic to ports 80 and 8080
simple-http-proxy --filter-port 80 --filter-port 8080

# Only capture traffic from localhost to a specific subnet
simple-http-proxy --filter-src 127.0.0.1 --filter-dst 10.0.0.0/8

# Only proxy HTTPS traffic (port 443) to a private network
simple-http-proxy --filter-port 443 --filter-dst 10.0.0.0/8

# JSON output, piped to jq
simple-http-proxy --format json | jq .

# JSON output written to a custom log file
simple-http-proxy --format json --log-file custom.log
```

### Using the proxy

Point your HTTP client at the proxy:

```bash
# curl
curl -x http://127.0.0.1:9090 http://example.com

# wget
wget -e use_proxy=yes -e http_proxy=127.0.0.1:9090 http://example.com

# environment variables (many tools respect these)
export http_proxy=http://127.0.0.1:9090
export https_proxy=http://127.0.0.1:9090
```

## Output Colors

In `pretty` format, the separator lines and section headers are colorized:

| Color | Used for |
|-------|----------|
| Cyan | Request separator lines and `REQUEST #N` header |
| Green | Response separator lines and `RESPONSE #N` header (successful response) |
| Red | Response separator line and `RESPONSE #N` header when there is no response (upstream error) |

Method/URL, headers, and body text are uncolored plain text. The `json` format and log file output have no color codes.

## Filtering

Filters narrow which requests are proxied. Requests that do not match all active filters receive a `403` response.

- **`--filter-port`**: matches if the destination port equals any of the specified values
- **`--filter-src`**: matches if the client IP falls within any of the specified CIDRs
- **`--filter-dst`**: matches if the resolved destination IP falls within any of the specified CIDRs

Multiple values for the same filter type are combined with OR; different filter types are combined with AND. Omitting a filter type allows all values for that dimension.

## Development

```bash
uv sync                          # install dependencies
uv run simple-http-proxy         # run from source
uv run python -m simple_http_proxy
```
