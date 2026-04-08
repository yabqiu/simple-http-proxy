import asyncio

import click

from .collector import Collector
from .filters import parse_filter_config
from .server import build_app, run_app


@click.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface to listen on.")
@click.option("--port", default=9090, show_default=True, help="Port to listen on.")
@click.option(
    "--filter-port",
    "filter_ports",
    multiple=True,
    type=int,
    metavar="PORT",
    help="Only proxy to this destination TCP port. Repeatable.",
)
@click.option(
    "--filter-src",
    "filter_srcs",
    multiple=True,
    metavar="CIDR",
    help="Only proxy requests from this source IP or CIDR. Repeatable.",
)
@click.option(
    "--filter-dst",
    "filter_dsts",
    multiple=True,
    metavar="CIDR",
    help="Only proxy requests to this destination IP or CIDR. Repeatable.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["pretty", "json"]),
    default="pretty",
    show_default=True,
    help="Output format for captured traffic.",
)
@click.option(
    "--log-file",
    "log_file",
    type=click.Path(dir_okay=False, writable=True),
    default="http-proxy.log",
    show_default=True,
    help="Append full (untruncated) request/response to this file.",
)
def main(
    host: str,
    port: int,
    filter_ports: tuple[int, ...],
    filter_srcs: tuple[str, ...],
    filter_dsts: tuple[str, ...],
    output_format: str,
    log_file: str,
) -> None:
    """HTTP forward proxy with traffic inspection and filtering.

    Configure your HTTP client to use this proxy, then all traffic
    will be captured and printed to stdout.

    \b
    Examples:
      # Basic usage
      http-proxy

      # Only capture traffic to ports 80 and 8080
      http-proxy --filter-port 80 --filter-port 8080

      # Only capture traffic from localhost to a specific subnet
      http-proxy --filter-src 127.0.0.1 --filter-dst 10.0.0.0/8

      # JSON output (pipe to jq for pretty-printing)
      http-proxy --format json | jq .
    """
    try:
        filter_config = parse_filter_config(filter_ports, filter_srcs, filter_dsts)
    except ValueError as e:
        raise click.BadParameter(str(e)) from e

    collector = Collector(output_format=output_format, log_file=log_file)
    click.echo(f"Logging traffic to: {log_file}")
    app = build_app(filter_config, collector)
    asyncio.run(run_app(app, host, port))
