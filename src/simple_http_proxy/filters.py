from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network


@dataclass
class FilterConfig:
    # Empty set = allow all
    allowed_ports: frozenset[int] = field(default_factory=frozenset)
    allowed_src_cidrs: list[IPv4Network] = field(default_factory=list)
    allowed_dst_cidrs: list[IPv4Network] = field(default_factory=list)


def matches(config: FilterConfig, src_ip: str, dst_ip: str, dst_port: int) -> bool:
    """Return True iff the connection passes all configured filters.

    Within each criterion type, values are OR'd (any match passes).
    Across criterion types, they are AND'd (all must pass).
    An empty criterion set matches everything.
    """
    if config.allowed_ports and dst_port not in config.allowed_ports:
        return False

    if config.allowed_src_cidrs:
        addr = IPv4Address(src_ip)
        if not any(addr in cidr for cidr in config.allowed_src_cidrs):
            return False

    if config.allowed_dst_cidrs:
        addr = IPv4Address(dst_ip)
        if not any(addr in cidr for cidr in config.allowed_dst_cidrs):
            return False

    return True


def parse_filter_config(
    ports: tuple[int, ...],
    src_ips: tuple[str, ...],
    dst_ips: tuple[str, ...],
) -> FilterConfig:
    """Validate and construct a FilterConfig from CLI-supplied values."""
    src_cidrs: list[IPv4Network] = []
    for raw in src_ips:
        try:
            src_cidrs.append(IPv4Network(raw, strict=False))
        except ValueError as e:
            raise ValueError(f"Invalid source IP/CIDR {raw!r}: {e}") from e

    dst_cidrs: list[IPv4Network] = []
    for raw in dst_ips:
        try:
            dst_cidrs.append(IPv4Network(raw, strict=False))
        except ValueError as e:
            raise ValueError(f"Invalid destination IP/CIDR {raw!r}: {e}") from e

    return FilterConfig(
        allowed_ports=frozenset(ports),
        allowed_src_cidrs=src_cidrs,
        allowed_dst_cidrs=dst_cidrs,
    )
