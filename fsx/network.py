"""Active IPv4 interfaces and subnet-directed broadcast addresses for the GUI."""

from dataclasses import dataclass
import ipaddress
import socket


@dataclass(frozen=True)
class Adapter:
    name: str
    address: str
    netmask: str
    broadcast: str
    prefix: int
    preferred: bool = False

    @property
    def label(self):
        return f"{self.name}  |  {self.address}/{self.prefix}"


def default_route_address():
    """Ask the OS which local IPv4 would route outward; send no UDP packets.

    No connection to an Internet service is made: UDP connect only selects a
    route locally. Offline LANs without a default route use the fallback sort.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 9))
            return sock.getsockname()[0]
    except OSError:
        return None


def adapters_from_interfaces(addresses, stats, preferred=None):
    """Pure conversion, kept separate from platform-specific enumeration."""
    result = []
    seen = set()
    for name, entries in addresses.items():
        status = stats.get(name)
        if status is None or not status.isup:
            continue
        for entry in entries:
            if entry.family != socket.AF_INET or not entry.netmask:
                continue
            try:
                interface = ipaddress.IPv4Interface(f"{entry.address}/{entry.netmask}")
            except ValueError:
                continue
            ip, network = interface.ip, interface.network
            if (ip.is_loopback or ip.is_link_local or ip.is_unspecified or
                    ip.is_multicast or ip.is_reserved or network.prefixlen >= 31 or
                    ip in (network.network_address, network.broadcast_address)):
                continue
            key = (name, str(ip), str(network.netmask))
            if key in seen:
                continue
            seen.add(key)
            result.append(Adapter(name, str(ip), str(network.netmask),
                                  str(network.broadcast_address), network.prefixlen,
                                  str(ip) == preferred))
    return sorted(result, key=lambda item: (
        not item.preferred, not ipaddress.IPv4Address(item.address).is_private,
        item.name.casefold(), int(ipaddress.IPv4Address(item.address))))


def discover_adapters():
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("Network discovery requires psutil. For source use, run "
                           "python -m pip install -r fsx/requirements-gui.txt "
                           "with your 32-bit Python. The EXE bundles this dependency.") from exc
    return adapters_from_interfaces(psutil.net_if_addrs(), psutil.net_if_stats(),
                                    default_route_address())
