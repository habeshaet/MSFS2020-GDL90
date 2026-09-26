"""Network/build/worker checks; Tk interaction tests run when Tk/display exist."""

import argparse
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fsx import bridge, build_exe
from fsx.network import Adapter, adapters_from_interfaces, default_route_address, discover_adapters
from fsx.tests.test_bridge import sample

try:
    from fsx import gui
except ImportError:
    gui = None


def address(ip, mask="255.255.255.0", family=socket.AF_INET):
    return SimpleNamespace(address=ip, netmask=mask, family=family)


class NetworkTests(unittest.TestCase):
    def test_requested_192_168_1_broadcast(self):
        adapters = adapters_from_interfaces(
            {"Wi-Fi": [address("192.168.1.13")]}, {"Wi-Fi": SimpleNamespace(isup=True)})
        self.assertEqual(adapters, [Adapter("Wi-Fi", "192.168.1.13", "255.255.255.0", "192.168.1.255", 24)])

    def test_actual_netmask_not_last_octet_guess(self):
        for mask, expected in (("255.255.0.0", "192.168.255.255"),
                               ("255.255.255.128", "192.168.1.127"),
                               ("255.255.255.252", "192.168.1.15")):
            with self.subTest(mask=mask):
                result = adapters_from_interfaces(
                    {"LAN": [address("192.168.1.13", mask)]}, {"LAN": SimpleNamespace(isup=True)})
                self.assertEqual(result[0].broadcast, expected)

    def test_default_route_preferred_and_multiple_adapters_preserved(self):
        interfaces = {"Ethernet": [address("10.0.0.2")], "Wi-Fi": [address("192.168.1.13")]}
        stats = {name: SimpleNamespace(isup=True) for name in interfaces}
        result = adapters_from_interfaces(interfaces, stats, "192.168.1.13")
        self.assertEqual([a.name for a in result], ["Wi-Fi", "Ethernet"])
        self.assertTrue(result[0].preferred)
        self.assertIn("192.168.1.13/24", result[0].label)

    def test_unusable_addresses_are_not_silently_broadcast(self):
        bad = [address("127.0.0.1", "255.0.0.0"), address("169.254.1.2"),
               address("0.0.0.0"), address("224.0.0.2"), address("192.168.1.0"),
               address("192.168.1.255"), address("192.168.1.13", None),
               address("192.168.1.13", "255.0.255.0"), address("invalid"),
               address("192.168.1.13", "255.255.255.255"),
               address("192.168.1.13", "255.255.255.254"),
               address("::1", "128", socket.AF_INET6)]
        self.assertEqual(adapters_from_interfaces(
            {"LAN": bad}, {"LAN": SimpleNamespace(isup=True)}), [])

    def test_down_missing_stats_and_duplicates(self):
        result = adapters_from_interfaces(
            {"Down": [address("10.0.0.2")], "Missing": [address("10.0.0.3")],
             "LAN": [address("192.168.1.13"), address("192.168.1.13")]},
            {"Down": SimpleNamespace(isup=False), "LAN": SimpleNamespace(isup=True)})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "LAN")

    def test_route_lookup_does_not_transmit_and_handles_offline(self):
        with patch("fsx.network.socket.socket") as factory:
            sock = factory.return_value.__enter__.return_value
            sock.getsockname.return_value = ("192.168.1.13", 4567)
            self.assertEqual(default_route_address(), "192.168.1.13")
            sock.send.assert_not_called()
            sock.sendto.assert_not_called()
            sock.connect.side_effect = OSError("no route")
            self.assertIsNone(default_route_address())

    def test_discovery_uses_platform_adapter_masks(self):
        psutil = Mock()
        psutil.net_if_addrs.return_value = {"Wi-Fi": [address("192.168.1.13")]}
        psutil.net_if_stats.return_value = {"Wi-Fi": SimpleNamespace(isup=True)}
        with patch.dict("sys.modules", {"psutil": psutil}), patch(
                "fsx.network.default_route_address", return_value="192.168.1.13"):
            self.assertEqual(discover_adapters()[0].broadcast, "192.168.1.255")


class WorkerTests(unittest.TestCase):
    def test_stop_before_connect_never_opens_native_client(self):
        args = bridge.parser().parse_args(["--target", "127.0.0.1"])
        stop = threading.Event()
        stop.set()
        with patch.object(bridge, "SimConnectSource") as source:
            bridge.run(args, stop_event=stop)
            source.return_value.connect.assert_not_called()
            source.return_value.close.assert_called_once()

    def test_gui_callbacks_binding_broadcast_and_cooperative_stop(self):
        args = bridge.parser().parse_args([
            "--target", "192.168.1.255", "--broadcast", "--bind-ip", "192.168.1.13"])
        stop = threading.Event()
        states, samples = [], []
        def receive(data):
            samples.append(data)
            stop.set()
        with patch.object(bridge, "SimConnectSource") as source, patch.object(
                bridge.socket, "socket") as factory, patch.object(
                bridge.time, "monotonic", return_value=100):
            src = source.return_value
            src.poll.return_value = sample()
            src.last_received = 100
            bridge.run(args, stop_event=stop, on_sample=receive, on_state=states.append)
            self.assertEqual(states, ["connecting", "live"])
            self.assertEqual(samples, [sample()])
            sock = factory.return_value.__enter__.return_value
            sock.bind.assert_called_once_with(("192.168.1.13", 0))
            sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.assertTrue(sock.sendto.called)
            self.assertTrue(all(c.args[1] == ("192.168.1.255", 4000) for c in sock.sendto.call_args_list))
            src.close.assert_called_once()
            factory.return_value.__exit__.assert_called_once()

    def test_stop_during_connect_closes_on_owner_thread(self):
        args = bridge.parser().parse_args(["--target", "127.0.0.1"])
        stop = threading.Event()
        with patch.object(bridge, "SimConnectSource") as source, patch.object(bridge.socket, "socket") as sock:
            source.return_value.connect.side_effect = stop.set
            bridge.run(args, stop_event=stop)
            source.return_value.close.assert_called_once()
            sock.assert_not_called()

    def test_bind_failure_closes_source_and_socket(self):
        args = bridge.parser().parse_args(["--target", "192.168.1.255", "--bind-ip", "192.168.1.13"])
        with patch.object(bridge, "SimConnectSource") as source, patch.object(bridge.socket, "socket") as factory:
            factory.return_value.__enter__.return_value.bind.side_effect = OSError("Adapter disconnected")
            with self.assertRaisesRegex(OSError, "Adapter disconnected"):
                bridge.run(args, stop_event=threading.Event())
            source.return_value.close.assert_called_once()
            factory.return_value.__exit__.assert_called_once()


class BuildTests(unittest.TestCase):
    def test_windowed_single_file_build_bundles_manifest_in_package(self):
        folder = Path(__file__).resolve().parents[1]
        command = build_exe.pyinstaller_command("x86-python.exe", folder)
        self.assertIn("--windowed", command)
        self.assertIn("--onefile", command)
        data = command[command.index("--add-data") + 1]
        self.assertTrue(data.startswith(str(folder / "SimConnect.manifest")))
        self.assertTrue(data.endswith("fsx"))
        self.assertEqual(command[-1], str(folder / "gui.py"))
        self.assertEqual(command[command.index("--paths") + 1], str(folder.parent))
        self.assertFalse(any("SimConnect.dll" in arg for arg in command))

    def test_rejects_non_windows_or_non_x86_without_installing(self):
        with patch.object(build_exe, "os") as os_mock, patch.object(
                build_exe.ctypes, "sizeof", return_value=8), patch.object(build_exe, "subprocess") as process:
            os_mock.name = "nt"
            with patch("builtins.print"):
                self.assertEqual(build_exe.main(), 1)
            process.run.assert_not_called()


class GuiInputTests(unittest.TestCase):
    def test_options_do_not_require_console_argparse(self):
        args = bridge.connection_options("192.168.1.255", "4000", "fsx", "5", "both", True, "192.168.1.13")
        self.assertEqual((args.target, args.bind_ip, args.broadcast, args.callsign),
                         (["192.168.1.255"], "192.168.1.13", True, "FSX"))
        for bad in ("nan", "0", "21"):
            with self.subTest(rate=bad), self.assertRaises(argparse.ArgumentTypeError):
                bridge.connection_options("192.168.1.16", "4000", "FSX", bad, "both", False)
        with self.assertRaises(ValueError):
            bridge.connection_options("192.168.1.16", "0", "FSX", "5", "both", False)


@unittest.skipIf(gui is None, "Tkinter is not installed")
class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = gui.tk.Tk()
        except gui.tk.TclError as exc:
            self.skipTest(f"No Tk display: {exc}")
        self.root.withdraw()
        self.app = gui.GDL90BridgeGUI(self.root, auto_discover=False)
        self.adapter = Adapter("Wi-Fi", "192.168.1.13", "255.255.255.0", "192.168.1.255", 24)
        self.app._apply_adapters([self.adapter])

    def tearDown(self):
        if hasattr(self, "app"):
            self.app._destroy()

    def drain(self):
        self.root.after_cancel(self.app.poll_id)
        self.app._poll_queue()

    def test_auto_manual_adapter_selection_and_empty_network(self):
        self.assertEqual(self.app.ip_var.get(), "192.168.1.255")
        self.assertEqual(self.app._entries['ip_var'].cget('state'), "readonly")
        self.app.auto_var.set(False)
        self.app._mode_changed()
        self.app.ip_var.set("192.168.1.16")
        self.app.broadcast_var.set(False)
        self.app._apply_adapters([self.adapter])
        self.assertEqual(self.app.ip_var.get(), "192.168.1.16")
        self.app.auto_var.set(True)
        self.app._mode_changed()
        self.assertTrue(self.app.broadcast_var.get())
        self.app._apply_adapters([])
        self.assertEqual(self.app.ip_var.get(), "")
        self.assertEqual(self.app._start_btn.cget('state'), "disabled")

    def test_worker_failure_recovers_controls_and_logs_error(self):
        with patch.object(gui.bridge, "run", side_effect=OSError("Test connection failed")):
            self.app._toggle()
            self.app.worker.join(timeout=2)
            self.drain()
        self.assertIsNone(self.app.worker)
        self.assertEqual(self.app._status_lbl.cget('text'), "Error")
        self.assertEqual(self.app._start_btn.cget('state'), "normal")
        self.assertIn("Test connection failed", self.app._log_box.get("1.0", "end"))

    def test_worker_gets_automatic_target_and_updates_from_queued_samples(self):
        observed = []
        ready = threading.Event()
        def fake_run(args, stop_event, on_sample, on_state):
            observed.append(args)
            on_state("live")
            on_sample(sample())
            ready.set()
            stop_event.wait(2)
        with patch.object(gui.bridge, "run", side_effect=fake_run):
            self.app._toggle()
            worker = self.app.worker
            self.assertTrue(ready.wait(1))
            self.drain()
            self.assertEqual(observed[0].target, ["192.168.1.255"])
            self.assertEqual(observed[0].bind_ip, "192.168.1.13")
            self.assertEqual(self.app._status_lbl.cget('text'), "Broadcasting")
            self.assertEqual(self.app._badges['pitch']._val.cget('text'), "+10.0°")
            self.app._toggle()
            worker.join(timeout=2)
            self.drain()
            self.assertIsNone(self.app.worker)
            self.assertEqual(self.app._badges['pitch']._val.cget('text'), "—")


if __name__ == "__main__":
    unittest.main()
