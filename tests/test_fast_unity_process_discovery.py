import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dashboard_server


class FastUnityProcessDiscoveryTests(unittest.TestCase):
    def test_windows_path_filters_names_before_expensive_process_metadata(self) -> None:
        class FakeProcess:
            def __init__(self, pid: int) -> None:
                self.pid = pid

            def as_dict(self, *, attrs):
                self.requested_attrs = attrs
                return {
                    "pid": self.pid,
                    "name": "Unity.exe",
                    "exe": r"C:\Unity\Editor\Unity.exe",
                    "cmdline": ["Unity.exe", "-projectPath", r"C:\Projects\Avatar"],
                }

        fake_psutil = SimpleNamespace(Process=FakeProcess)
        with (
            patch.object(dashboard_server.os, "name", "nt"),
            patch.object(
                dashboard_server,
                "_enumerate_windows_process_names",
                return_value=[
                    {"pid": 101, "name": "explorer.exe"},
                    {"pid": 202, "name": "Unity.exe"},
                ],
            ),
            patch.object(dashboard_server, "psutil", fake_psutil),
            patch.object(dashboard_server, "_iter_processes", side_effect=AssertionError("full process scan")),
        ):
            result = dashboard_server.list_running_unity_processes()

        self.assertEqual([item["processId"] for item in result], [202])
        self.assertEqual(result[0]["commandLine"], "Unity.exe -projectPath C:\\Projects\\Avatar")

    def test_strict_windows_discovery_fails_closed_when_fast_scan_is_unavailable(self) -> None:
        with (
            patch.object(dashboard_server.os, "name", "nt"),
            patch.object(
                dashboard_server,
                "_enumerate_windows_process_names",
                side_effect=dashboard_server.UnityProcessDiscoveryUnavailable("unavailable"),
            ),
        ):
            with self.assertRaises(dashboard_server.UnityProcessDiscoveryUnavailable):
                dashboard_server.list_running_unity_processes(require_discovery_evidence=True)


if __name__ == "__main__":
    unittest.main()
