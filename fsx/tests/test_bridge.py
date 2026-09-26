"""Offline tests: python -m unittest discover -s fsx/tests -v"""

import contextlib
import ctypes as C
import io
import math
import socket
import struct
import unittest
from unittest.mock import Mock, patch

from fsx import bridge, protocol as p
from fsx.simconnect_source import (
    DATA, HEADER, VARIABLES, FlightData, SimConnectSource, find_dll,
)


def crc_reference(data):
    # Bitwise equivalent of the Garmin recurrence, without the encoder's table.
    crc = 0
    for byte in data:
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
        crc ^= byte
    return crc


def unframe(frame):
    assert frame[0] == frame[-1] == 0x7E
    result = bytearray()
    escaped = False
    for byte in frame[1:-1]:
        if escaped:
            result.append(byte ^ 0x20)
            escaped = False
        elif byte == 0x7D:
            escaped = True
        else:
            assert byte != 0x7E
            result.append(byte)
    assert not escaped
    payload = result[:-2]
    assert struct.unpack("<H", result[-2:])[0] == crc_reference(payload)
    return bytes(payload)


def values():
    # Crosswind: heading 4 degrees, track 350. High VS must NOT be guessed as ft/s.
    return [47.45, -122.3, 4500, 4700, 4, 350, 120, 7200, 0, -10, -20, 110, 130]


def sample():
    return FlightData.from_values(values())


def native_packet(data=None, request=1, count=len(VARIABLES)):
    body = DATA.pack(*(values() if data is None else data))
    return HEADER.pack(HEADER.size + len(body), 0, 8, request, 0, 1, 0, 1, 1, count) + body


class ProtocolTests(unittest.TestCase):
    def test_garmin_published_crc_vector(self):
        # GDL90 ICD Rev A, section 2.2.4; independent published wire example.
        self.assertEqual(p._gdl90_frame(bytes.fromhex("008141dbd00802")),
                         bytes.fromhex("7e008141dbd00802b38b7e"))

    def test_crc_and_escaping_round_trip_all_bytes(self):
        data = bytes(range(256))
        frame = p._gdl90_frame(data)
        self.assertIn(b"\x7d\x5e", frame)
        self.assertIn(b"\x7d\x5d", frame)
        self.assertEqual(unframe(frame), data)

    def test_heartbeat_day_high_bit_and_invalid(self):
        for seconds in (0, 65535, 65536, 86399):
            msg = unframe(p._build_heartbeat(seconds))
            self.assertEqual(len(msg), 7)
            self.assertEqual((msg[3] | msg[4] << 8 | (msg[2] >> 7) << 16), seconds)
            self.assertEqual(msg[1] & 0x80, 0x80)
        self.assertEqual(unframe(p._build_heartbeat(0, False))[1] & 0x80, 0)

    def test_foreflight_ahrs_known_payload(self):
        msg = p._build_foreflight_ahrs(10, 20, 4, 110, 130)
        self.assertEqual(unframe(msg), bytes.fromhex("650100c800640028006e0082"))
        self.assertEqual(struct.unpack(">BBhhHHH", unframe(
            p._build_foreflight_ahrs(-10, -20, 359.99, 0, 0))),
            (0x65, 1, -200, -100, 0, 0, 0))

    def test_legacy_packet_preserves_original_layout_and_bank_sign(self):
        msg = unframe(bridge.flight_messages(sample(), ahrs="legacy")[-1])
        self.assertEqual(len(msg), 17)
        msg_id, pitch, bank, hdg, valid = struct.unpack("<BfffI", msg)
        self.assertEqual((msg_id, hdg, valid), (0x66, 4, 1))
        self.assertAlmostEqual(pitch, math.radians(10), places=6)
        self.assertAlmostEqual(bank, math.radians(-20), places=6)

    def test_ownship_pressure_altitude_track_and_signed_position(self):
        msg = unframe(bridge.flight_messages(sample())[0])
        self.assertEqual(len(msg), 28)
        self.assertEqual(msg[0], 10)
        self.assertEqual(msg[2:5], b"\0\0\0")
        for offset, degrees in ((5, 47.45), (8, -122.3)):
            encoded = int.from_bytes(msg[offset:offset + 3], "big")
            if encoded & 0x800000:
                encoded -= 1 << 24
            self.assertAlmostEqual(encoded * 180 / (1 << 23), degrees, places=4)
        altitude = ((msg[11] << 4) | (msg[12] >> 4)) * 25 - 1000
        self.assertEqual(altitude, 4700)
        self.assertEqual(msg[12] & 0xF, 0x9)  # airborne + true track
        self.assertEqual(msg[17], round(350 * 256 / 360))
        self.assertEqual(msg[19:27], b"FSX     ")

    def test_ground_flag_and_negative_vertical_velocity(self):
        msg = unframe(p._build_ownship(0, 0, -500, 90, 0, -640, on_ground=True))
        self.assertEqual(msg[12] & 0xF, 1)
        self.assertEqual((msg[15] & 0xF) << 8 | msg[16], 4096 - 10)
        self.assertEqual(msg[14], 0)  # do not replace zero GS with TAS

    def test_altitude_and_id_msl_capability(self):
        geo = unframe(p._build_geo_altitude(-100))
        self.assertEqual(struct.unpack(">BhH", geo), (11, -20, 0x7FFF))
        device = unframe(p._build_foreflight_id("FSX"))
        self.assertEqual(len(device), 39)
        self.assertEqual(device[:3], b"\x65\x00\x01")
        self.assertEqual(device[11:19], b"FSX     ")
        self.assertEqual(struct.unpack(">I", device[-4:])[0], 1)

    def test_ahrs_selection(self):
        self.assertEqual(len(bridge.flight_messages(sample(), ahrs="both")), 4)
        ff = [unframe(m) for m in bridge.flight_messages(sample(), ahrs="foreflight")]
        self.assertEqual([m[0] for m in ff], [10, 11, 0x65])


class NativeTests(unittest.TestCase):
    def test_explicit_units_and_signs(self):
        data = sample()
        self.assertEqual((data.pitch, data.roll, data.hdg_true, data.vs_fpm), (10, 20, 4, 7200))
        self.assertEqual(VARIABLES[7][2], "feet per minute")
        self.assertEqual(VARIABLES[9][2], "degrees")
        self.assertEqual(DATA.size, len(VARIABLES) * 8)
        self.assertEqual(HEADER.size, 40)

    def test_dispatch_packed_doubles_at_byte_40(self):
        src = SimConnectSource()
        src._receive(native_packet(), 123)
        self.assertEqual(src.latest, sample())
        self.assertEqual(src.last_received, 123)

    def test_unrelated_request_and_open_are_ignored(self):
        src = SimConnectSource()
        src._receive(native_packet(request=99), 1)
        src._receive(struct.pack("<III", 12, 0, 2), 1)
        self.assertIsNone(src.latest)
        self.assertIsNone(src.last_received)

    def test_malformed_messages(self):
        src = SimConnectSource()
        for packet in (b"", native_packet()[:-1], native_packet(count=99),
                       struct.pack("<III", 4, 0, 2), struct.pack("<III", 12, 0, 8)):
            with self.subTest(packet=packet), self.assertRaises(OSError):
                src._receive(packet, 1)

    def test_nonfinite_and_invalid_coordinates_rejected(self):
        for index, bad in ((0, math.nan), (1, math.inf), (0, 91), (1, -181), (9, 181)):
            data = values()
            data[index] = bad
            with self.subTest(index=index, bad=bad), self.assertRaises(ValueError):
                FlightData.from_values(data)

    def test_quit_and_async_exception(self):
        src = SimConnectSource()
        src._receive(native_packet(), 1)
        with self.assertRaises(ConnectionError):
            src._receive(struct.pack("<III", 12, 0, 3), 2)
        self.assertIsNone(src.latest)
        with self.assertRaisesRegex(OSError, "exception 19.*send 7, index 2"):
            src._receive(struct.pack("<6I", 24, 0, 1, 19, 7, 2), 2)

    def test_poll_copies_native_data_and_handles_empty_queue(self):
        src = SimConnectSource()
        buf = C.create_string_buffer(native_packet())
        src.dll = Mock()
        def dispatch(handle, pointer, size):
            C.cast(pointer, C.POINTER(C.c_void_p))[0] = C.addressof(buf)
            C.cast(size, C.POINTER(C.c_uint32))[0] = len(native_packet())
            return 0
        src.dll.SimConnect_GetNextDispatch.side_effect = [0, -2147467259]
        # First call fills the out-parameters; second returns E_FAIL (empty).
        calls = iter([True, False])
        src.dll.SimConnect_GetNextDispatch.side_effect = lambda *args: (
            dispatch(*args) if next(calls) else -2147467259)
        self.assertEqual(src.poll(), sample())

    def test_hresult_and_close_idempotent(self):
        with self.assertRaisesRegex(OSError, "80004005"):
            SimConnectSource._check(-2147467259, "test")
        src = SimConnectSource()
        src.dll = Mock()
        src.handle = C.c_void_p(123)
        src.close()
        src.close()
        src.dll.SimConnect_Close.assert_called_once()

    def test_connect_requests_fsx_variables_and_cleans_partial_failure(self):
        for fail_definition in (False, True):
            with self.subTest(fail_definition=fail_definition):
                dll = Mock()
                def opened(pointer, *args):
                    C.cast(pointer, C.POINTER(C.c_void_p))[0] = 123
                    return 0
                dll.SimConnect_Open.side_effect = opened
                dll.SimConnect_AddToDataDefinition.return_value = (
                    -2147467259 if fail_definition else 0)
                dll.SimConnect_RequestDataOnSimObject.return_value = 0
                with patch("fsx.simconnect_source.os") as os_mock, patch(
                        "fsx.simconnect_source.C.sizeof", return_value=4), patch(
                        "fsx.simconnect_source.find_dll", return_value="FSX/SimConnect.dll"), patch(
                        "fsx.simconnect_source.load_dll", return_value=dll):
                    os_mock.name = "nt"
                    src = SimConnectSource()
                    if fail_definition:
                        with self.assertRaises(OSError):
                            src.connect()
                        dll.SimConnect_Close.assert_called_once()
                    else:
                        src.connect()
                        calls = dll.SimConnect_AddToDataDefinition.call_args_list
                        self.assertEqual(len(calls), len(VARIABLES))
                        for call, (_, name, unit) in zip(calls, VARIABLES):
                            self.assertEqual(call.args[1:], (
                                1, name.encode("ascii"), unit.encode("ascii"), 4, 0.0, 0xFFFFFFFF))
                        self.assertEqual(dll.SimConnect_RequestDataOnSimObject.call_args.args[1:],
                                         (1, 1, 0, 3, 0, 0, 0, 0))
                        src.close()

    def test_native_function_signatures(self):
        src = SimConnectSource()
        src.dll = Mock()
        src._bind()
        self.assertEqual(len(src.dll.SimConnect_RequestDataOnSimObject.argtypes), 9)
        self.assertIs(src.dll.SimConnect_Open.restype, C.c_int32)

    def test_missing_explicit_dll(self):
        with self.assertRaisesRegex(OSError, "does not exist"):
            find_dll("nonexistent-test-directory/SimConnect.dll")


class StreamTests(unittest.TestCase):
    def test_five_hz_not_divided_by_message_count(self):
        stream = bridge.PacketStream()
        batches = [stream.packets(sample(), 0, t, 0) for t in (0, .201, .402, .603, .804)]
        self.assertEqual(sum(unframe(m)[0] == 10 for batch in batches for m in batch), 5)
        self.assertEqual(sum(unframe(m)[0] == 0 for batch in batches for m in batch), 1)
        self.assertEqual(stream.packets(sample(), 0, .81, 0), [])

    def test_startup_stale_and_recovery(self):
        stream = bridge.PacketStream()
        start = [unframe(m) for m in stream.packets(None, None, 0, 0)]
        self.assertEqual(len(start), 2)
        self.assertEqual(start[0][1] & 0x80, 0)
        fresh = stream.packets(sample(), 1, 1, 1)
        self.assertEqual(len(fresh), 6)
        stale = [unframe(m) for m in stream.packets(sample(), 1, 3.1, 3)]
        self.assertEqual([m[0] for m in stale], [0, 0x65])
        self.assertEqual(stale[0][1] & 0x80, 0)
        self.assertEqual(len(stream.packets(sample(), 4.2, 4.2, 4)), 6)

    def test_loopback_udp_smoke(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rx, socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM) as tx:
            rx.bind(("127.0.0.1", 0))
            rx.settimeout(1)
            messages = bridge.PacketStream().packets(sample(), 1, 1, 12345)
            for msg in messages:
                tx.sendto(msg, rx.getsockname())
                received, _ = rx.recvfrom(1500)
                self.assertEqual(unframe(received), unframe(msg))

    def test_resource_cleanup_on_connect_failure(self):
        args = bridge.parser().parse_args(["--target", "127.0.0.1"])
        with patch.object(bridge, "SimConnectSource") as source:
            source.return_value.connect.side_effect = OSError("no sim")
            with self.assertRaises(OSError):
                bridge.run(args)
            source.return_value.close.assert_called_once()

    def test_run_uses_exact_targets_and_closes_on_send_failure(self):
        args = bridge.parser().parse_args([
            "--target", "192.168.1.42", "--target", "192.168.1.43"])
        with patch.object(bridge, "SimConnectSource") as source, patch.object(
                bridge.socket, "socket") as factory:
            src = source.return_value
            src.poll.return_value = sample()
            src.last_received = 100
            sock = factory.return_value.__enter__.return_value
            sock.sendto.side_effect = [None, OSError("network down")]
            with patch.object(bridge.time, "monotonic", return_value=100):
                with self.assertRaises(OSError):
                    bridge.run(args)
            self.assertEqual([c.args[1] for c in sock.sendto.call_args_list],
                             [("192.168.1.42", 4000), ("192.168.1.43", 4000)])
            sock.setsockopt.assert_not_called()
            factory.return_value.__exit__.assert_called_once()
            src.close.assert_called_once()


class CliTests(unittest.TestCase):
    def test_invalid_inputs_fail_before_connecting(self):
        for options in (["--port", "0"], ["--rate", "nan"], ["--rate", "21"],
                        ["--stale-after", "inf"], ["--stale-after", "-1"],
                        ["--icao", "1000000"], ["--callsign", "TOOLONG123"],
                        ["--target", "not-an-ip"]):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    bridge.main(["--target", "127.0.0.1"] + options)
                self.assertEqual(error.exception.code, 2)

    def test_normalized_arguments(self):
        args = bridge.parser().parse_args([
            "--target", "192.168.1.42", "--callsign", "n123ab", "--icao", "abcdef"])
        self.assertEqual((args.callsign, args.icao, args.rate), ("N123AB", 0xABCDEF, 5))


if __name__ == "__main__":
    unittest.main()
