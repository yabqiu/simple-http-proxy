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

  HTTP/HTTPS forward proxy with traffic inspection and filtering.

  Configure your HTTP client to use this proxy, then all traffic will be
  captured and printed to stdout.

Options:
  --host TEXT             Interface to listen on.  [default: 127.0.0.1]
  --port INTEGER          Port to listen on.  [default: 1080]
  --ca-cert FILE          CA certificate for HTTPS interception.
  --ca-key FILE           CA private key for HTTPS interception.
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
| `--port PORT` | `1080` | Port to listen on |
| `--ca-cert FILE` | _(none)_ | CA certificate file; enables HTTPS interception |
| `--ca-key FILE` | _(none)_ | CA private key file; required with `--ca-cert` |
| `--filter-port PORT` | _(none)_ | Only proxy to this destination TCP port (repeatable) |
| `--filter-src CIDR` | _(none)_ | Only proxy requests from this source IP or CIDR (repeatable) |
| `--filter-dst CIDR` | _(none)_ | Only proxy requests to this destination IP or CIDR (repeatable) |
| `--format [pretty\|json]` | `pretty` | Output format for captured traffic |
| `--log-file PATH` | `http-proxy.log` | Append full (untruncated) request/response to this file |

### Examples

```bash
# Start with defaults (listen on 127.0.0.1:1080)
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
curl -x http://127.0.0.1:1080 http://example.com

# wget
wget -e use_proxy=yes -e http_proxy=127.0.0.1:1080 http://example.com

# environment variables (many tools respect these)
export http_proxy=http://127.0.0.1:1080
export https_proxy=http://127.0.0.1:1080
```

## HTTPS Interception

When started **without** `--ca-cert`/`--ca-key`, the proxy forwards HTTPS traffic as a transparent TCP tunnel (via the HTTP `CONNECT` method). The connection still works, but the request and response headers and body are encrypted end-to-end and cannot be logged — only the destination host, port, and tunnel establishment time are recorded.

To decrypt and log HTTPS traffic, the proxy must act as a TLS man-in-the-middle: it terminates the client's TLS connection using a dynamically generated certificate, logs the plaintext request/response, then re-encrypts and forwards to the upstream server. This requires a CA certificate and key as described below.

### 1. Generate a CA certificate with openssl

```bash
# CA private key (keep this secret)
openssl genrsa -out ca.key 4096

# Self-signed CA certificate, valid 365 days
openssl req -new -x509 -days 365 \
  -key ca.key \
  -out ca.crt \
  -subj "/CN=simple-http-proxy CA/O=simple-http-proxy"
```

### 2. Trust the CA on client machines

The proxy signs a fresh leaf certificate for every upstream host it intercepts.
Clients will only accept those certificates if they trust `ca.crt`.

| Platform | How to install |
|----------|---------------|
| **macOS** | `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ca.crt` |
| **Linux (system)** | Copy to `/usr/local/share/ca-certificates/` and run `sudo update-ca-certificates` |
| **Firefox** | Preferences → Privacy & Security → Certificates → Import |
| **Chrome/Edge** | Settings → Privacy → Manage certificates → Authorities → Import |
| **curl** | Pass `--cacert ca.crt` or set `SSL_CERT_FILE=ca.crt` |

### 3. Start the proxy with HTTPS interception enabled

```bash
simple-http-proxy --ca-cert ca.crt --ca-key ca.key
```

Both `--ca-cert` and `--ca-key` must be supplied together. If either is omitted, `CONNECT` tunnels are forwarded transparently (no decryption).

### 4. Point your client at the proxy

```bash
# curl — trust the CA explicitly
curl --cacert ca.crt -x http://127.0.0.1:1080 https://example.com

# curl — use the system trust store (after installing ca.crt system-wide)
curl -x http://127.0.0.1:1080 https://example.com

# environment variables
export http_proxy=http://127.0.0.1:1080
export https_proxy=http://127.0.0.1:1080
export SSL_CERT_FILE=ca.crt   # if not installed system-wide
```

## Output Colors

In `pretty` format, the separator lines and section headers are colorized to distinguish HTTP from HTTPS traffic:

| Color | Used for |
|-------|----------|
| Cyan | **HTTP** request separator and `REQUEST #N` header |
| Green | **HTTP** response separator and `RESPONSE #N` header (successful) |
| Magenta | **HTTPS** request separator and `REQUEST #N` header |
| Yellow | **HTTPS** response separator and `RESPONSE #N` header (successful) |
| Red | Response separator and `RESPONSE #N` header when there is no response (upstream error, either protocol) |

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
