"""Approved writes must not lose receipts to the interactive read window."""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = (REPO / "vrchat_blendshape_agent.py").read_text(encoding="utf-8-sig")


def test_write_window_floor_is_wired_to_the_claim_lane():
    assert 'write_window_floor = 300 if claim_context.get("lane") in {' in SOURCE
    assert '"external_mcp_write", "approved_write"' in SOURCE
    # The floor participates in the timeout max(), not a separate override.
    assert re.search(r'"timeout_seconds"\s*:\s*max\(\s*write_window_floor', SOURCE, re.S)


import re  # noqa: E402


def test_floor_never_lowers_interactive_reads():
    # max(floor, capped settings value) can only raise the window.
    assert "max(\n                    write_window_floor," in SOURCE
