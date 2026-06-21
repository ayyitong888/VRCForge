from __future__ import annotations

import dashboard_server


def test_dashboard_cli_forwards_global_cli_flags() -> None:
    args = dashboard_server.parse_args(["--cli", "--json", "--endpoint", "http://127.0.0.1:8757", "doctor"])

    assert args.cli is True
    assert args.cli_args == ["--json", "--endpoint", "http://127.0.0.1:8757", "doctor"]
