from __future__ import annotations

import hashlib
import hmac
import io
import struct

import pytest

import primitive_bridge_target_adapter as adapter
import primitive_bridge_target_entry as entry


def _digest(value: int) -> bytes:
    return bytes([value]) * 32


def _frame_bytes(
    *,
    bridge_manifest_digest: bytes = _digest(7),
    bridge_tree_digest: bytes = _digest(8),
) -> bytes:
    socket_share = b"service-owned-socket-share"
    startup_material = bytes(range(1, 33))
    values = (
        *tuple(_digest(value) for value in range(1, 7)),
        bridge_manifest_digest,
        bridge_tree_digest,
    )
    payload = bytearray().join(values)
    payload.extend(struct.pack(">Q", 0x0102_0304_0506_0708))
    payload.extend(struct.pack(">BHHHIHQ", 1, 2, 1, 6, 0x7F00_0001, 49_221, 99))
    payload.extend(struct.pack(">I", len(socket_share)))
    payload.extend(socket_share)
    payload.extend(struct.pack(">I", len(startup_material)))
    payload.extend(startup_material)
    socket_share_digest = hashlib.sha256(socket_share).digest()
    startup_material_digest = hashlib.sha256(startup_material).digest()
    payload.extend(socket_share_digest)
    payload.extend(startup_material_digest)
    request_auth = hmac.new(startup_material, digestmod=hashlib.sha256)
    request_auth.update(adapter.BRIDGE_TARGET_REQUEST_AUTH_KEY_DOMAIN)
    for value in values:
        request_auth.update(value)
    request_auth.update(struct.pack(">Q", 0x0102_0304_0506_0708))
    request_auth.update(struct.pack(">H", 49_221))
    request_auth.update(struct.pack(">Q", 99))
    request_auth.update(socket_share_digest)
    request_auth.update(startup_material_digest)
    payload.extend(
        hashlib.sha256(
            adapter.BRIDGE_TARGET_REQUEST_AUTH_KEY_DIGEST_DOMAIN
            + request_auth.digest()
        ).digest()
    )
    header = adapter.BRIDGE_TARGET_FRAME_MAGIC + struct.pack(
        ">HI", adapter.BRIDGE_TARGET_PROTOCOL_VERSION, len(payload) + 32
    )
    payload.extend(
        hashlib.sha256(adapter.BRIDGE_TARGET_FRAME_DOMAIN + header + payload).digest()
    )
    return header + payload


def _ack_bytes() -> bytes:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    return adapter.encode_bridge_target_ack(
        frame,
        adapter.BridgeTargetProcessIdentity(4_242, 9_999, _digest(6), _digest(7)),
        adapter.BridgeTargetRequestAuthSnapshot(1, 0, 0, 0, 0, False),
    )


def _shutdown_bytes() -> bytes:
    return adapter.encode_bridge_target_shutdown_request(
        adapter.BridgeTargetShutdownRequest(
            run_binding_digest=_digest(1),
            ticket_digest=_digest(2),
            bridge_launch_binding_digest=_digest(3),
            private_pipe_binding_digest=_digest(4),
            private_pipe_instance_id=0x0102_0304_0506_0708,
            sequence=1,
            requested_at=0x1112_1314_1516_1718,
        )
    )


def _accounting_bytes() -> bytes:
    return adapter.encode_bridge_target_shutdown_accounting(
        adapter.BridgeTargetShutdownAccounting(
            run_binding_digest=_digest(1),
            ticket_digest=_digest(2),
            bridge_launch_binding_digest=_digest(3),
            private_pipe_binding_digest=_digest(4),
            private_pipe_instance_id=0x0102_0304_0506_0708,
            target_port=49_221,
            listener_socket_object_id=99,
            request_auth_key_digest=_digest(8),
            request_auth=adapter.BridgeTargetRequestAuthSnapshot(
                1, 2, 3, 0, 0, True
            ),
            observed_at_shutdown=0x2122_2324_2526_2728,
            owner=adapter.BridgeTargetProcessIdentity(
                4_242, 9_999, _digest(6), _digest(7)
            ),
            request_auth_header_stripped=True,
        )
    )


def _minimal_environment() -> dict[str, str]:
    return adapter.build_minimal_bridge_target_child_environment(
        windows_directory=r"C:\Windows",
        private_temp_directory=r"C:\ProgramData\VRCForge\runs\fixed\tmp",
    )


def test_private_pipe_runtime_is_disabled_before_input_or_output() -> None:
    class ForbiddenStream:
        def read(self, size: int) -> bytes:
            raise AssertionError(f"read attempted: {size}")

        def write(self, value: bytes) -> int:
            raise AssertionError(f"write attempted: {len(value)}")

    with pytest.raises(
        entry.BridgeTargetEntryError,
        match="bridge_target_private_pipe_runtime_unverified",
    ):
        entry.run_bridge_target_private_pipe(ForbiddenStream(), ForbiddenStream())


def test_private_pipe_runtime_contract_sequences_frames_without_live_io(
    monkeypatch,
) -> None:
    class RecordingOutput:
        def __init__(self) -> None:
            self.value = bytearray()
            self.closed = False

        def write(self, value: bytes) -> int:
            assert not self.closed
            self.value.extend(value)
            return len(value)

        def flush(self) -> None:
            assert not self.closed

        def close(self) -> None:
            assert not self.closed
            self.closed = True

    sentinel_dependencies = object()

    def serve(
        frame,
        emit_ack,
        dependencies,
        *,
        await_shutdown,
    ):
        assert dependencies is sentinel_dependencies
        assert frame.bridge_manifest_digest == _digest(7)
        assert frame.bridge_tree_digest == _digest(8)
        emit_ack(_ack_bytes())
        shutdown = await_shutdown(frame)
        assert shutdown == adapter.decode_bridge_target_shutdown_request(
            _shutdown_bytes()
        )
        return adapter.decode_bridge_target_shutdown_accounting(
            _accounting_bytes(), expected=shutdown
        )

    monkeypatch.setattr(entry, "BRIDGE_TARGET_PRIVATE_PIPE_RUNTIME_ENABLED", True)
    monkeypatch.setattr(adapter, "serve_adopted_bridge_target", serve)
    source = io.BytesIO(_frame_bytes() + _shutdown_bytes())
    output = RecordingOutput()

    accounting = entry.run_bridge_target_private_pipe(
        source,
        output,
        sentinel_dependencies,
    )

    assert accounting == adapter.decode_bridge_target_shutdown_accounting(
        _accounting_bytes(),
        expected=adapter.decode_bridge_target_shutdown_request(_shutdown_bytes()),
    )
    assert source.read() == b""
    assert bytes(output.value) == _ack_bytes() + _accounting_bytes()
    assert output.closed


def test_main_with_exact_mode_fails_closed_without_protocol_output(capsys) -> None:
    output = io.BytesIO()

    result = entry.main(
        environ=_minimal_environment(),
        argv=["bridge-target"],
        input_stream=io.BytesIO(_frame_bytes()),
        output_stream=output,
    )

    assert result == 1
    assert output.getvalue() == b""
    captured = capsys.readouterr()
    assert "refused" in captured.err
    assert "runtime_unverified" not in captured.err


def test_main_rejects_nonminimal_environment_before_protocol_io(capsys) -> None:
    class ForbiddenStream:
        def read(self, size: int) -> bytes:
            raise AssertionError(f"read attempted: {size}")

        def write(self, value: bytes) -> int:
            raise AssertionError(f"write attempted: {len(value)}")

    environment = _minimal_environment()
    environment["PATH"] = r"C:\Windows\System32"

    result = entry.main(
        environ=environment,
        argv=["bridge-target"],
        input_stream=ForbiddenStream(),
        output_stream=ForbiddenStream(),
    )

    assert result == 1
    assert capsys.readouterr().err == ""


def test_frame_reader_accepts_one_exact_chunked_frame_and_rejects_truncation() -> None:
    encoded = _frame_bytes()

    class ChunkedReader(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            return super().read(min(size, 7))

    assert entry.read_bridge_target_frame(ChunkedReader(encoded)) == encoded
    with pytest.raises(entry.BridgeTargetEntryError, match="frame_read_failed"):
        entry.read_bridge_target_frame(io.BytesIO(encoded[:-1]))


def test_entry_decoder_requires_both_parent_runtime_bindings() -> None:
    frame = entry.decode_parent_bound_bridge_target_frame(_frame_bytes())
    assert frame.bridge_manifest_digest == _digest(7)
    assert frame.bridge_tree_digest == _digest(8)

    for encoded in (
        _frame_bytes(bridge_manifest_digest=b"\0" * 32),
        _frame_bytes(bridge_tree_digest=b"\0" * 32),
    ):
        with pytest.raises(entry.BridgeTargetEntryError, match="frame_invalid"):
            entry.decode_parent_bound_bridge_target_frame(encoded)


def test_ack_writer_is_one_attempt_even_after_partial_write_failure() -> None:
    ack = _ack_bytes()

    class PartialFailure:
        def __init__(self) -> None:
            self.value = bytearray()
            self.calls = 0

        def write(self, value: bytes) -> int:
            self.calls += 1
            if self.calls == 1:
                self.value.extend(value[:9])
                return 9
            raise OSError("write failed")

        def flush(self) -> None:
            raise AssertionError("flush must not be reached")

    stream = PartialFailure()
    writer = entry.BridgeTargetPrivatePipeWriter(stream)
    with pytest.raises(entry.BridgeTargetEntryError, match="ack_write_failed"):
        writer.write_ack(ack)
    with pytest.raises(entry.BridgeTargetEntryError, match="ack_duplicate"):
        writer.write_ack(ack)
    assert bytes(stream.value) == ack[:9]
    assert not writer.acknowledged


def test_accounting_writer_requires_verified_shutdown_contract() -> None:
    output = io.BytesIO()
    writer = entry.BridgeTargetPrivatePipeWriter(output)
    ack = _ack_bytes()
    writer.write_ack(ack)

    with pytest.raises(entry.BridgeTargetEntryError, match="shutdown_not_verified"):
        writer.write_accounting(b"not-observable-without-a-shutdown-frame")
    with pytest.raises(entry.BridgeTargetEntryError, match="accounting_duplicate"):
        writer.write_accounting(b"retry")

    assert output.getvalue() == ack
    assert writer.acknowledged
    assert not writer.accounting_written


def test_shutdown_reader_and_writer_allow_one_accounting_then_close_for_eof() -> None:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    shutdown = _shutdown_bytes()
    guard = adapter.BridgeTargetShutdownReplayGuard()
    assert entry.read_bridge_target_shutdown_request(
        io.BytesIO(shutdown), frame, guard
    ) == adapter.decode_bridge_target_shutdown_request(shutdown)
    with pytest.raises(entry.BridgeTargetEntryError, match="shutdown_invalid"):
        entry.read_bridge_target_shutdown_request(io.BytesIO(shutdown), frame, guard)

    class RecordingOutput:
        def __init__(self) -> None:
            self.value = bytearray()
            self.closed = False

        def write(self, value: bytes) -> int:
            if self.closed:
                raise AssertionError("write after EOF")
            self.value.extend(value)
            return len(value)

        def flush(self) -> None:
            if self.closed:
                raise AssertionError("flush after EOF")

        def close(self) -> None:
            if self.closed:
                raise AssertionError("duplicate EOF")
            self.closed = True

    output = RecordingOutput()
    writer = entry.BridgeTargetPrivatePipeWriter(output)
    ack = _ack_bytes()
    writer.write_ack(ack)
    writer.accept_shutdown(shutdown, frame)
    with pytest.raises(entry.BridgeTargetEntryError, match="shutdown_invalid"):
        writer.accept_shutdown(shutdown, frame)
    accounting = _accounting_bytes()
    writer.write_accounting(accounting)

    assert bytes(output.value) == ack + accounting
    assert output.closed
    assert writer.acknowledged
    assert writer.shutdown_verified
    assert writer.accounting_written
    assert writer.eof_written
    with pytest.raises(entry.BridgeTargetEntryError, match="accounting_duplicate"):
        writer.write_accounting(accounting)


def test_accounting_close_failure_cannot_report_eof_or_retry() -> None:
    class CloseFailureOutput:
        def __init__(self) -> None:
            self.value = bytearray()

        def write(self, value: bytes) -> int:
            self.value.extend(value)
            return len(value)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            raise OSError("close failed")

    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    output = CloseFailureOutput()
    writer = entry.BridgeTargetPrivatePipeWriter(output)
    ack = _ack_bytes()
    shutdown = _shutdown_bytes()
    accounting = _accounting_bytes()
    writer.write_ack(ack)
    writer.accept_shutdown(shutdown, frame)

    with pytest.raises(entry.BridgeTargetEntryError, match="accounting_eof_failed"):
        writer.write_accounting(accounting)

    assert bytes(output.value) == ack + accounting
    assert writer.accounting_written
    assert not writer.eof_written
    with pytest.raises(entry.BridgeTargetEntryError, match="accounting_duplicate"):
        writer.write_accounting(accounting)
