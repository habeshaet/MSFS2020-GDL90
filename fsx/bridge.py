#!/usr/bin/env python3
"""FSX -> GDL90 UDP bridge. Run: py -3-32 -m fsx.bridge --target IP"""

import argparse
import ipaddress
import logging
import math
import socket
import sys
import time

if __package__ in (None, ""):
    # Also support: python fsx/bridge.py (no installation required).
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsx import __version__, protocol as p
from fsx.simconnect_source import SimConnectSource

LOG = logging.getLogger("fsx")


def flight_messages(data, callsign="FSX", icao=0, ahrs="both"):
    """One update: position, MSL altitude and selected attitude extensions."""
    messages = [
        p._build_ownship(data.lat, data.lon, data.pressure_alt_ft,
                        data.track_true, data.gs_kt, data.vs_fpm,
                        icao=icao, callsign=callsign, on_ground=data.on_ground),
        p._build_geo_altitude(data.alt_ft),
    ]
    if ahrs in ("foreflight", "both"):
        messages.append(p._build_foreflight_ahrs(
            data.pitch, data.roll, data.hdg_true, data.ias_kt, data.tas_kt))
    if ahrs in ("legacy", "both"):
        # Preserve the MSFS script's 0x66 wire layout AND its original bank sign.
        # This proprietary compatibility packet is not ForeFlight's AHRS format.
        messages.append(p._build_attitude(data.pitch, -data.roll, data.hdg_true))
    return messages


class PacketStream:
    """Monotonic scheduling independent of the number of UDP messages sent."""

    def __init__(self, rate=5.0, timeout=2.0, callsign="FSX", icao=0, ahrs="both"):
        self.period = 1.0 / rate
        self.timeout = timeout
        self.callsign = callsign
        self.icao = icao
        self.ahrs = ahrs
        self.next_update = 0.0
        self.next_heartbeat = 0.0

    def is_fresh(self, data, received, now):
        return data is not None and received is not None and 0 <= now - received < self.timeout

    def packets(self, data, received, now, utc_seconds):
        valid = self.is_fresh(data, received, now)
        messages = []
        if now >= self.next_heartbeat:
            messages.extend((p._build_heartbeat(utc_seconds, valid),
                             p._build_foreflight_id(self.callsign)))
            self.next_heartbeat = now + 1.0
        if now >= self.next_update:
            if valid:
                messages.extend(flight_messages(data, self.callsign, self.icao, self.ahrs))
            # Don't send catch-up bursts after a slow/stalled simulator.
            self.next_update = now + self.period
        return messages


def run(args, stop_event=None, on_sample=None, on_state=None):
    """Run on one thread; callbacks must not access Tk widgets directly.

    CLI callers may omit the optional controls. A GUI requests stop via an
    Event; this thread alone owns and closes both native and UDP resources.
    """
    source = SimConnectSource(args.dll)
    stream = PacketStream(args.rate, args.stale_after, args.callsign, args.icao, args.ahrs)
    destinations = [(target, args.port) for target in dict.fromkeys(args.target)]
    try:
        if stop_event is not None and stop_event.is_set():
            return
        if on_state:
            on_state("connecting")
        source.connect()
        if stop_event is not None and stop_event.is_set():
            return
        LOG.info("SimConnect handle opened; waiting for server acknowledgement/flight data.")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            if getattr(args, "bind_ip", None):
                sock.bind((args.bind_ip, 0))
                LOG.info("Using local network address %s", args.bind_ip)
            if args.broadcast:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            LOG.info("Sending to %s at %.1f Hz; AHRS=%s.",
                     destinations, args.rate, args.ahrs)
            started = time.monotonic()
            last_log = 0.0
            was_fresh = None
            next_sample = 0.0
            while stop_event is None or not stop_event.is_set():
                data = source.poll()
                now = time.monotonic()
                fresh = stream.is_fresh(data, source.last_received, now)
                if fresh != was_fresh:
                    if fresh:
                        LOG.info("Receiving live FSX data.")
                    else:
                        LOG.warning("No fresh FSX data: GPS invalid; position/AHRS suspended.")
                    was_fresh = fresh
                    if on_state:
                        on_state("live" if fresh else "waiting" if data is None else "stale")
                if fresh and on_sample and now >= next_sample:
                    on_sample(data)
                    next_sample = now + 0.2
                if data is None and now - started >= 15.0:
                    raise TimeoutError(
                        "No FSX flight data after 15 seconds. Receive diagnostics: "
                        f"{source.diagnostics()}. Restart with --debug and share "
                        "the complete log if the loaded flight is unpaused.")
                utc = time.gmtime()
                seconds = utc.tm_hour * 3600 + utc.tm_min * 60 + utc.tm_sec
                for message in stream.packets(data, source.last_received, now, seconds):
                    for destination in destinations:
                        sock.sendto(message, destination)
                if fresh and now - last_log >= 2.0:
                    LOG.info("%+.5f %+.5f | MSL %.0f ft | GS %.0f kt | "
                             "HDG %.1f | pitch %+.1f roll %+.1f",
                             data.lat, data.lon, data.alt_ft, data.gs_kt,
                             data.hdg_true, data.pitch, data.roll)
                    last_log = now
                if stop_event is None:
                    time.sleep(0.01)
                else:
                    stop_event.wait(0.01)
    finally:
        source.close()


def ipv4(value):
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError as exc:
        raise argparse.ArgumentTypeError("Expected an IPv4 address, e.g. 192.168.1.42") from exc


def positive_rate(value):
    try:
        rate = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Rate must be a number") from exc
    if not math.isfinite(rate) or not 1 <= rate <= 20:
        raise argparse.ArgumentTypeError("Rate must be between 1 and 20 Hz")
    return rate


def callsign(value):
    value = value.strip().upper()
    if not 1 <= len(value) <= 8 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -" for c in value):
        raise argparse.ArgumentTypeError("Callsign must be 1-8 ASCII letters, digits, spaces or hyphens")
    return value


def icao_address(value):
    try:
        address = int(value, 16)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ICAO must be a hexadecimal address") from exc
    if not 0 <= address <= 0xFFFFFF:
        raise argparse.ArgumentTypeError("ICAO must be between 000000 and FFFFFF")
    return address


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--target", required=True, action="append", type=ipv4,
                        help="iPad IPv4 address; repeat for multiple devices")
    result.add_argument("--port", type=int, default=4000, help="EFB UDP port (default: 4000)")
    result.add_argument("--broadcast", action="store_true",
                        help="enable broadcast to the EXACT --target address (unicast preferred)")
    result.add_argument("--bind-ip", type=ipv4,
                        help="local adapter IPv4 to send from (normally selected by the OS)")
    result.add_argument("--dll", help="full path to native 32-bit FSX SimConnect.dll")
    result.add_argument("--debug", action="store_true",
                        help="log SimConnect request/receive headers for troubleshooting")
    result.add_argument("--version", action="version", version=f"FSX EFB Connect {__version__}")
    result.add_argument("--rate", type=positive_rate, default=5.0, help="updates/sec (default: 5)")
    result.add_argument("--callsign", type=callsign, default="FSX")
    result.add_argument("--icao", type=icao_address, default=0, help="hex address (default: 000000)")
    result.add_argument("--ahrs", choices=("both", "foreflight", "legacy"), default="both",
                        help="both: documented ForeFlight AHRS plus original 0x66 packet")
    result.add_argument("--stale-after", type=float, default=2.0,
                        help="suspend flight data after this many seconds without updates (default: 2)")
    return result


def connection_options(ip, port, callsign_text, rate, ahrs, broadcast, bind_ip=None):
    """Validate GUI input without argparse printing to a nonexistent EXE console."""
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    if ahrs not in ("both", "foreflight", "legacy"):
        raise ValueError("Choose a supported AHRS mode.")
    target = ipv4(ip.strip())
    if target == "255.255.255.255" and not broadcast:
        raise ValueError("Enable broadcast for a broadcast target.")
    return argparse.Namespace(
        target=[target], port=port, callsign=callsign(callsign_text),
        rate=positive_rate(rate), ahrs=ahrs, broadcast=broadcast,
        bind_ip=ipv4(bind_ip) if bind_ip else None,
        icao=0, dll=None, stale_after=2.0, debug=False,
    )



def main(argv=None):
    arg_parser = parser()
    args = arg_parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        arg_parser.error("--port must be between 1 and 65535")
    if not math.isfinite(args.stale_after) or args.stale_after <= 0:
        arg_parser.error("--stale-after must be finite and positive")
    if "255.255.255.255" in args.target and not args.broadcast:
        arg_parser.error("Broadcast targets require --broadcast")
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
    LOG.info("FSX EFB Connect %s", __version__)
    try:
        run(args)
    except KeyboardInterrupt:
        LOG.info("Bridge stopped.")
        return 0
    except (OSError, ValueError) as exc:
        LOG.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
