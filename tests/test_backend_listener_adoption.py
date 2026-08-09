from __future__ import annotations

import hashlib
import io
import os
import socket
import struct
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import backend_listener_adoption as adoption
import primitive_basis_live_attestation as live


FRAME_DOMAIN = b"vrcforge-authority-backend-adoption-frame-v1\0"
ACK_DOMAIN = b"vrcforge-authority-backend-adoption-ack-v1\0"


def _digest(label: bytes) -> bytes:
    return hashlib.sha256(label).digest()


def _bootstrap(backend_bytes: bytes = b"backend") -> live.LiveBootstrap:
    return live.LiveBootstrap(
        key=b"k" * 32,
        challenge=b"c" * 32,
        runtime_binding_digest=_digest(b"runtime").hex(),
        desktop_executable_digest=_digest(b"desktop").hex(),
        backend_executable_digest=hashlib.sha256(backend_bytes).hexdigest(),
        runner_digest=_digest(b"runner").hex(),
        unity_package_digest=_digest(b"unity-package").hex(),
        unity_editor_digest=_digest(b"unity-editor").hex(),
        fixture_project_input_digest=_digest(b"fixture-project").hex(),
        fixture_set_descriptor_digest=_digest(b"fixture-set").hex(),
        fixture_descriptor_digest=_digest(b"fixture-descriptor").hex(),
        origin_ticket_digest=_digest(b"origin-ticket").hex(),
    )


def _outer_frame(
    *,
    socket_share: bytes = b"service-owned-share",
    bootstrap: bytes | None = None,
    run_binding: bytes | None = None,
    pipe_binding: bytes | None = None,
    challenge: bytes | None = None,
    listener_id: int = 0x0102_0304_0506_0708,
) -> bytes:
    bootstrap = bootstrap or live.encode_bootstrap_frame(_bootstrap())
    run_binding = run_binding or _digest(b"run-binding")
    pipe_binding = pipe_binding or _digest(b"pipe-binding")
    challenge = challenge or _digest(b"challenge")
    body = b"".join(
        (
            run_binding,
            pipe_binding,
            struct.pack(">BHHHIHQI", 1, 2, 1, 6, 0x7F000001, 8757, listener_id, len(socket_share)),
            socket_share,
            struct.pack(">HI", 4, len(bootstrap)),
            bootstrap,
            hashlib.sha256(bootstrap).digest(),
            challenge,
        )
    )
    prefix = b"VRCBSH01" + struct.pack(">HI", 1, len(body) + 32) + body
    return prefix + hashlib.sha256(FRAME_DOMAIN + prefix).digest()


def _resign(frame: bytearray) -> bytes:
    frame[-32:] = hashlib.sha256(FRAME_DOMAIN + frame[:-32]).digest()
    return bytes(frame)


class ChunkedReader(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = 3
        return super().read(min(size, 3))


class PartialWriter(io.BytesIO):
    def write(self, value: bytes) -> int:
        return super().write(value[:5])


class FakeSocket:
    def __init__(
        self,
        *,
        family: int = socket.AF_INET,
        socket_type: int = socket.SOCK_STREAM,
        protocol: int = socket.IPPROTO_TCP,
        address: tuple[str, int] = ("127.0.0.1", 8757),
        exclusive: int = 1,
        reuse: int = 0,
        accepting: int = 1,
    ) -> None:
        self.family = family
        self.type = socket_type
        self.proto = protocol
        self.address = address
        self.options = {
            adoption.SO_EXCLUSIVEADDRUSE_OPTION: exclusive,
            socket.SO_REUSEADDR: reuse,
            socket.SO_ACCEPTCONN: accepting,
        }
        self.closed = False
        self.bind_calls = 0

    def getsockname(self):
        return self.address

    def getsockopt(self, level: int, option: int) -> int:
        assert level == socket.SOL_SOCKET
        return self.options[option]

    def bind(self, _address) -> None:
        self.bind_calls += 1
        raise AssertionError("ordinary bind must never be used for adoption")

    def close(self) -> None:
        self.closed = True


def _identity() -> adoption.BackendProcessIdentity:
    return adoption.BackendProcessIdentity(
        process_id=0x0102_0304,
        process_creation_time=0x0102_0304_0506_0708,
        executable_digest=_digest(b"backend"),
        image_identity_digest=_digest(b"backend-image-identity"),
    )


def test_outer_frame_is_exact_big_endian_domain_bound_and_partial_read_safe() -> None:
    encoded = _outer_frame()
    parsed = adoption.parse_backend_adoption_frame(encoded)

    assert adoption.BACKEND_ADOPTION_FRAME_DOMAIN == FRAME_DOMAIN
    assert parsed.run_binding_digest == _digest(b"run-binding")
    assert parsed.private_pipe_binding_digest == _digest(b"pipe-binding")
    assert parsed.listener_socket_object_id == 0x0102_0304_0506_0708
    assert parsed.socket_share_bytes == b"service-owned-share"
    assert parsed.inner_live_bootstrap_bytes == live.encode_bootstrap_frame(_bootstrap())
    assert parsed.challenge == _digest(b"challenge")
    assert adoption.read_backend_adoption_frame(ChunkedReader(encoded)) == parsed


@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (lambda value: value.__setitem__(slice(0, 8), b"BADMAGIC"), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(slice(8, 10), b"\x00\x02"), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(78, 2), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(slice(79, 81), b"\x00\x17"), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(slice(83, 85), b"\x00\x11"), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(slice(85, 89), b"\x7f\x00\x00\x02"), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(slice(89, 91), b"\x22\x36"), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(slice(91, 99), b"\x00" * 8), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(slice(14, 46), b"\x00" * 32), "backend_adoption_frame_invalid"),
        (lambda value: value.__setitem__(slice(-64, -32), b"\x00" * 32), "backend_adoption_frame_invalid"),
    ],
)
def test_outer_frame_rejects_semantic_substitution_even_with_recomputed_digest(
    mutate, error_code: str
) -> None:
    changed = bytearray(_outer_frame())
    mutate(changed)
    with pytest.raises(adoption.BackendListenerAdoptionError, match=error_code):
        adoption.parse_backend_adoption_frame(_resign(changed))


def test_outer_frame_rejects_length_trailing_digest_share_and_bootstrap_faults() -> None:
    valid = _outer_frame()
    cases = [valid[:-1], valid + b"x"]

    wrong_length = bytearray(valid)
    wrong_length[10:14] = struct.pack(">I", len(valid))
    cases.append(bytes(wrong_length))

    bad_digest = bytearray(valid)
    bad_digest[-1] ^= 1
    cases.append(bytes(bad_digest))

    empty_share = bytearray(valid)
    empty_share[99:103] = b"\x00" * 4
    cases.append(_resign(empty_share))

    bootstrap_magic_offset = 103 + len(b"service-owned-share") + 6
    bad_bootstrap = bytearray(valid)
    bad_bootstrap[bootstrap_magic_offset] ^= 1
    cases.append(_resign(bad_bootstrap))

    bad_bootstrap_digest = bytearray(valid)
    bad_bootstrap_digest[-96] ^= 1
    cases.append(_resign(bad_bootstrap_digest))

    for case in cases:
        with pytest.raises(adoption.BackendListenerAdoptionError):
            adoption.parse_backend_adoption_frame(case)

    with pytest.raises(
        adoption.BackendListenerAdoptionError, match="backend_adoption_frame_invalid"
    ):
        adoption.parse_backend_adoption_frame(
            _outer_frame(socket_share=b"x" * (adoption.MAX_SOCKET_SHARE_BYTES + 1))
        )

    oversized_header = b"VRCBSH01" + struct.pack(
        ">HI", 1, adoption.MAX_BACKEND_ADOPTION_PAYLOAD_BYTES + 1
    )
    with pytest.raises(
        adoption.BackendListenerAdoptionError, match="backend_adoption_frame_invalid"
    ):
        adoption.read_backend_adoption_frame(io.BytesIO(oversized_header))


def test_adoption_uses_fromshare_once_validates_listener_and_never_binds(tmp_path: Path) -> None:
    backend_bytes = b"backend"
    backend = tmp_path / "backend.exe"
    backend.write_bytes(backend_bytes)
    parsed = adoption.parse_backend_adoption_frame(
        _outer_frame(bootstrap=live.encode_bootstrap_frame(_bootstrap(backend_bytes)))
    )
    listener = FakeSocket()
    shares: list[bytes] = []

    result = adoption.adopt_backend_listener(
        parsed,
        socket_fromshare=lambda value: shares.append(value) or listener,
        identity_provider=_identity,
        executable_path=backend,
        frozen=True,
    )

    assert shares == [b"service-owned-share"]
    assert result.listener_socket is listener
    assert result.live_session.state == "issued"
    assert listener.bind_calls == 0
    assert result.ack_bytes.startswith(b"VRCBAK01")
    result.close()
    assert listener.closed is True


def test_successful_adoption_erases_retained_bootstrap_share_and_session_key(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend.exe"
    backend.write_bytes(b"backend")
    frame = adoption.parse_backend_adoption_frame(_outer_frame())
    bootstrap_secret = bytes(frame.inner_live_bootstrap_bytes[:80])
    share_secret = bytes(frame.socket_share_bytes)
    listener = FakeSocket()
    received: list[bytes] = []

    result = adoption.adopt_backend_listener(
        frame,
        socket_fromshare=lambda value: received.append(bytes(value)) or listener,
        identity_provider=_identity,
        executable_path=backend,
        frozen=True,
    )

    assert bootstrap_secret != bytes(len(bootstrap_secret))
    assert share_secret == b"service-owned-share"
    assert received == [share_secret]
    assert frame.inner_live_bootstrap_bytes and not any(frame.inner_live_bootstrap_bytes)
    assert frame.socket_share_bytes and not any(frame.socket_share_bytes)
    assert not hasattr(result.live_session, "_bootstrap")
    assert any(result.live_session._key)

    result.close()
    assert result.live_session.state == "closed"
    assert not any(result.live_session._key)


@pytest.mark.parametrize(
    "listener",
    [
        FakeSocket(family=socket.AF_INET6),
        FakeSocket(socket_type=socket.SOCK_DGRAM),
        FakeSocket(protocol=socket.IPPROTO_UDP),
        FakeSocket(address=("127.0.0.2", 8757)),
        FakeSocket(address=("127.0.0.1", 8758)),
        FakeSocket(exclusive=0),
        FakeSocket(reuse=1),
        FakeSocket(accepting=0),
    ],
)
def test_adoption_rejects_every_listener_identity_shortcut(
    listener: FakeSocket, tmp_path: Path
) -> None:
    backend = tmp_path / "backend.exe"
    backend.write_bytes(b"backend")
    frame = adoption.parse_backend_adoption_frame(_outer_frame())

    with pytest.raises(
        adoption.BackendListenerAdoptionError, match="backend_adoption_socket_invalid"
    ):
        adoption.adopt_backend_listener(
            frame,
            socket_fromshare=lambda _value: listener,
            identity_provider=_identity,
            executable_path=backend,
            frozen=True,
        )
    assert listener.closed is True
    assert listener.bind_calls == 0


def test_ack_has_exact_rust_layout_flags_digest_and_partial_write() -> None:
    frame = adoption.parse_backend_adoption_frame(_outer_frame())
    identity = _identity()
    ack = adoption.encode_backend_adoption_ack(frame, identity)

    assert len(ack) == 275
    assert adoption.BACKEND_ADOPTION_ACK_DOMAIN == ACK_DOMAIN
    assert ack[:8] == b"VRCBAK01"
    assert struct.unpack(">H", ack[8:10])[0] == 1
    assert struct.unpack(">I", ack[10:14])[0] == 261
    assert ack[14:46] == frame.run_binding_digest
    assert ack[46:78] == frame.private_pipe_binding_digest
    assert ack[78:110] == frame.challenge
    assert ack[110] == 1
    assert struct.unpack(">H", ack[111:113])[0] == 2
    assert struct.unpack(">H", ack[113:115])[0] == 1
    assert struct.unpack(">H", ack[115:117])[0] == 6
    assert struct.unpack(">I", ack[117:121])[0] == 0x7F000001
    assert struct.unpack(">H", ack[121:123])[0] == 8757
    assert struct.unpack(">Q", ack[123:131])[0] == frame.listener_socket_object_id
    assert struct.unpack(">H", ack[131:133])[0] == 4
    assert ack[133:165] == frame.inner_live_bootstrap_digest
    assert struct.unpack(">I", ack[165:169])[0] == identity.process_id
    assert struct.unpack(">Q", ack[169:177])[0] == identity.process_creation_time
    assert ack[177:209] == identity.executable_digest
    assert ack[209:241] == identity.image_identity_digest
    assert adoption.BACKEND_ADOPTION_ACK_REQUIRED_FLAGS == 0x007F
    assert struct.unpack(">H", ack[241:243])[0] == 0x007F
    assert ack[-32:] == hashlib.sha256(ACK_DOMAIN + ack[:-32]).digest()

    output = PartialWriter()
    adoption.write_backend_adoption_ack(output, ack)
    assert output.getvalue() == ack
    tampered = bytearray(ack)
    tampered[-1] ^= 1
    rejected_output = io.BytesIO()
    with pytest.raises(
        adoption.BackendListenerAdoptionError, match="backend_adoption_ack_invalid"
    ):
        adoption.write_backend_adoption_ack(rejected_output, bytes(tampered))
    assert rejected_output.getvalue() == b""
    with pytest.raises(
        adoption.BackendListenerAdoptionError, match="backend_adoption_ack_duplicate"
    ):
        adopted = adoption.BackendListenerAdoption(
            listener_socket=FakeSocket(),
            live_session=Mock(),
            ack_bytes=ack,
            ack_stream=io.BytesIO(),
        )
        adopted.acknowledge()
        adopted.acknowledge()


def test_partial_ack_failure_consumes_the_only_write_attempt() -> None:
    frame = adoption.parse_backend_adoption_frame(_outer_frame())
    ack = adoption.encode_backend_adoption_ack(frame, _identity())

    class PrefixThenFailure:
        def __init__(self) -> None:
            self.value = bytearray()
            self.calls = 0

        def write(self, value: bytes) -> int:
            self.calls += 1
            if self.calls == 1:
                written = min(17, len(value))
                self.value.extend(value[:written])
                return written
            raise OSError("private pipe failure")

        def flush(self) -> None:
            raise AssertionError("flush must not run after a failed write")

    output = PrefixThenFailure()
    adopted = adoption.BackendListenerAdoption(
        listener_socket=FakeSocket(),
        live_session=Mock(),
        ack_bytes=ack,
        ack_stream=output,
    )

    with pytest.raises(
        adoption.BackendListenerAdoptionError,
        match="backend_adoption_ack_write_failed",
    ):
        adopted.acknowledge()

    partial = bytes(output.value)
    assert partial == ack[:17]
    assert adopted.acknowledged is False
    with pytest.raises(
        adoption.BackendListenerAdoptionError,
        match="backend_adoption_ack_duplicate",
    ):
        adopted.acknowledge()
    assert bytes(output.value) == partial


def test_concurrent_acknowledgement_writes_exactly_one_frame() -> None:
    frame = adoption.parse_backend_adoption_frame(_outer_frame())
    ack = adoption.encode_backend_adoption_ack(frame, _identity())

    class BlockingWriter(io.BytesIO):
        def __init__(self) -> None:
            super().__init__()
            self.first_write_entered = threading.Event()
            self.release_first_write = threading.Event()
            self.write_calls = 0
            self._calls_lock = threading.Lock()

        def write(self, value: bytes) -> int:
            with self._calls_lock:
                self.write_calls += 1
                call_number = self.write_calls
            if call_number == 1:
                self.first_write_entered.set()
                assert self.release_first_write.wait(timeout=1.0)
            return super().write(value)

    output = BlockingWriter()
    adopted = adoption.BackendListenerAdoption(
        listener_socket=FakeSocket(),
        live_session=Mock(),
        ack_bytes=ack,
        ack_stream=output,
    )
    second_started = threading.Event()
    outcomes: list[str] = []

    def acknowledge(*, signal_start: bool = False) -> None:
        if signal_start:
            second_started.set()
        try:
            adopted.acknowledge()
            outcomes.append("acknowledged")
        except adoption.BackendListenerAdoptionError as exc:
            outcomes.append(exc.code)

    first = threading.Thread(target=acknowledge)
    second = threading.Thread(target=acknowledge, kwargs={"signal_start": True})
    first.start()
    assert output.first_write_entered.wait(timeout=1.0)
    second.start()
    assert second_started.wait(timeout=1.0)
    output.release_first_write.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(outcomes) == ["acknowledged", "backend_adoption_ack_duplicate"]
    assert output.write_calls == 1
    assert output.getvalue() == ack
    assert adopted.acknowledged is True


def test_mode_is_exact_and_absence_performs_no_io() -> None:
    assert adoption.backend_listener_adoption_requested({}) is False
    assert (
        adoption.load_backend_listener_adoption(
            environ={},
            input_stream=Mock(side_effect=AssertionError("must not read")),
            output_stream=Mock(side_effect=AssertionError("must not write")),
        )
        is None
    )
    with pytest.raises(
        adoption.BackendListenerAdoptionError, match="backend_adoption_mode_invalid"
    ):
        adoption.backend_listener_adoption_requested(
            {adoption.BACKEND_ADOPTION_ENV: "true"}
        )


def test_adoption_and_legacy_stdin_modes_conflict_before_any_io() -> None:
    input_stream = Mock()
    output_stream = Mock()
    environment = {
        adoption.BACKEND_ADOPTION_ENV: "1",
        live.LIVE_STDIN_ENV: "1",
    }

    with pytest.raises(
        adoption.BackendListenerAdoptionError,
        match="backend_adoption_mode_conflict",
    ):
        adoption.load_backend_listener_adoption(
            environ=environment,
            input_stream=input_stream,
            output_stream=output_stream,
        )

    input_stream.read.assert_not_called()
    output_stream.write.assert_not_called()


def test_successful_adoption_removes_both_one_use_mode_markers(tmp_path: Path) -> None:
    backend = tmp_path / "backend.exe"
    backend.write_bytes(b"backend")
    listener = FakeSocket()
    environment = {adoption.BACKEND_ADOPTION_ENV: "1"}

    with patch.dict(os.environ, environment, clear=True):
        result = adoption.load_backend_listener_adoption(
            input_stream=io.BytesIO(_outer_frame()),
            output_stream=io.BytesIO(),
            socket_fromshare=lambda _value: listener,
            identity_provider=_identity,
            executable_path=backend,
            frozen=True,
        )
        assert result is not None
        assert adoption.BACKEND_ADOPTION_ENV not in os.environ
        assert live.LIVE_STDIN_ENV not in os.environ
        result.close()


def test_socket_share_failure_is_generic_and_never_falls_back_to_bind(
    tmp_path: Path,
) -> None:
    frame = adoption.parse_backend_adoption_frame(_outer_frame())
    backend = tmp_path / "backend.exe"
    backend.write_bytes(b"backend")
    with pytest.raises(
        adoption.BackendListenerAdoptionError,
        match="backend_adoption_socket_share_invalid",
    ):
        adoption.adopt_backend_listener(
            frame,
            socket_fromshare=lambda _value: (_ for _ in ()).throw(OSError("private")),
            identity_provider=_identity,
            executable_path=backend,
            frozen=True,
        )
    assert frame.inner_live_bootstrap_bytes and not any(frame.inner_live_bootstrap_bytes)
    assert frame.socket_share_bytes and not any(frame.socket_share_bytes)


def test_identity_failure_does_not_consume_the_one_use_socket_share(tmp_path: Path) -> None:
    backend = tmp_path / "backend.exe"
    backend.write_bytes(b"backend")
    frame = adoption.parse_backend_adoption_frame(_outer_frame())
    listener = FakeSocket()
    fromshare = Mock(return_value=listener)

    with pytest.raises(adoption.BackendListenerAdoptionError):
        adoption.adopt_backend_listener(
            frame,
            socket_fromshare=fromshare,
            identity_provider=lambda: adoption.BackendProcessIdentity(
                process_id=0,
                process_creation_time=1,
                executable_digest=_digest(b"backend"),
                image_identity_digest=_digest(b"identity"),
            ),
            executable_path=backend,
            frozen=True,
        )
    fromshare.assert_not_called()
    assert listener.closed is False
    assert any(frame.inner_live_bootstrap_bytes)
    assert bytes(frame.socket_share_bytes) == b"service-owned-share"

    mismatch = FakeSocket()
    mismatch_fromshare = Mock(return_value=mismatch)
    with pytest.raises(
        adoption.BackendListenerAdoptionError,
        match="backend_adoption_identity_mismatch",
    ):
        adoption.adopt_backend_listener(
            frame,
            socket_fromshare=mismatch_fromshare,
            identity_provider=lambda: adoption.BackendProcessIdentity(
                process_id=1,
                process_creation_time=1,
                executable_digest=_digest(b"another-backend"),
                image_identity_digest=_digest(b"identity"),
            ),
            executable_path=backend,
            frozen=True,
        )
    mismatch_fromshare.assert_not_called()
    assert mismatch.closed is False
    assert any(frame.inner_live_bootstrap_bytes)
    assert bytes(frame.socket_share_bytes) == b"service-owned-share"


@pytest.mark.skipif(os.name != "nt", reason="Windows socket sharing probe")
def test_windows_real_fromshare_probe_is_loopback_only_and_nonpersistent(tmp_path: Path) -> None:
    source = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    adopted = None
    try:
        source.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            source.bind(("127.0.0.1", 8757))
        except OSError:
            pytest.skip("fixed loopback port is occupied")
        source.listen(1)
        backend = tmp_path / "backend.exe"
        backend.write_bytes(b"backend")
        frame = adoption.parse_backend_adoption_frame(
            _outer_frame(socket_share=source.share(os.getpid()))
        )
        adopted = adoption.adopt_backend_listener(
            frame,
            identity_provider=_identity,
            executable_path=backend,
            frozen=True,
        )
        assert adopted.listener_socket.getsockname() == ("127.0.0.1", 8757)
    finally:
        if adopted is not None:
            adopted.close()
        source.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity probe")
def test_windows_identity_probe_is_read_only_and_current_process_bound() -> None:
    identity = adoption.current_backend_process_identity()

    assert identity.process_id == os.getpid()
    assert identity.process_creation_time > 0
    assert identity.executable_digest == hashlib.sha256(Path(os.sys.executable).read_bytes()).digest()
    assert any(identity.image_identity_digest)


def test_dashboard_adoption_skips_bind_probe_acks_then_runs_with_exact_socket() -> None:
    import dashboard_server

    args = dashboard_server.parse_args([])
    listener = object()
    events: list[str] = []
    live_observer = Mock(return_value={"ok": True, "observed": True})
    live_runtime = SimpleNamespace(
        status=lambda: {"ok": True, "state": "adopted"},
        observe_apply_lifecycle=live_observer,
    )
    live_connection = SimpleNamespace(
        bind=Mock(),
        validate=Mock(),
        inspect_fixture=Mock(),
        reload_fixture=Mock(),
        inspect_component=Mock(),
        preview_component=Mock(),
        read_compile_status=Mock(),
    )
    adopted = SimpleNamespace(
        listener_socket=listener,
        live_session=object(),
        acknowledge=lambda: events.append("ack"),
        close=lambda: events.append("close"),
    )
    lease = Mock()
    lease.acquire.return_value = True
    original_session = dashboard_server.PRIMITIVE_BASIS_LIVE_SESSION
    original_connection = dashboard_server.PRIMITIVE_BASIS_LIVE_CONNECTION
    original_runtime = dashboard_server.PRIMITIVE_BASIS_LIVE_RUNTIME
    original_observer = dashboard_server.AGENT_GATEWAY.approval_transactions.apply_lifecycle_observer

    def create_connection() -> object:
        events.append("connection")
        return live_connection

    def create_runtime(session: object, callbacks: object) -> object:
        assert session is adopted.live_session
        assert callbacks.bind_connection == live_connection.bind
        events.append("runtime")
        return live_runtime

    def run_server(*_args, **_kwargs) -> None:
        events.append("run")
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_SESSION is adopted.live_session
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_CONNECTION is live_connection
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_RUNTIME is live_runtime
        assert dashboard_server.app_primitive_basis_model_part_live_status() == {
            "ok": True,
            "state": "adopted",
        }
        assert dashboard_server.AGENT_GATEWAY.approval_transactions.apply_lifecycle_observer is live_observer
        assert dashboard_server.AGENT_GATEWAY.approval_transactions.apply_lifecycle_observer(
            "applied", {"requestId": "request-1"}
        ) == {"ok": True, "observed": True}

    try:
        with (
            patch("dashboard_server.parse_args", return_value=args),
            patch("dashboard_server.backend_listener_adoption_requested", return_value=True),
            patch("dashboard_server.load_backend_listener_adoption", return_value=adopted),
            patch(
                "dashboard_server.backend_bind_target_occupied",
                side_effect=AssertionError("adoption must not probe or bind"),
            ),
            patch("dashboard_server.BACKEND_OWNER_LEASE", lease),
            patch(
                "dashboard_server.PrimitiveBasisLiveUnityConnection",
                side_effect=create_connection,
            ),
            patch(
                "dashboard_server.ModelPartCompositionLiveRuntime",
                side_effect=create_runtime,
            ),
            patch(
                "dashboard_server.run_owned_uvicorn_server",
                side_effect=run_server,
            ) as run_server,
        ):
            assert dashboard_server.main() == 0
        assert events == ["connection", "runtime", "ack", "run", "close"]
        run_server.assert_called_once_with(args.host, args.port, sockets=[listener])
        live_observer.assert_called_once_with("applied", {"requestId": "request-1"})
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_SESSION is None
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_CONNECTION is None
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_RUNTIME is None
        assert dashboard_server.AGENT_GATEWAY.approval_transactions.apply_lifecycle_observer is None
    finally:
        dashboard_server.PRIMITIVE_BASIS_LIVE_SESSION = original_session
        dashboard_server.PRIMITIVE_BASIS_LIVE_CONNECTION = original_connection
        dashboard_server.PRIMITIVE_BASIS_LIVE_RUNTIME = original_runtime
        dashboard_server.AGENT_GATEWAY.approval_transactions.apply_lifecycle_observer = original_observer


def test_dashboard_adoption_ack_failure_closes_socket_releases_owner_and_never_runs() -> None:
    import dashboard_server

    args = dashboard_server.parse_args([])
    events: list[str] = []
    adopted = SimpleNamespace(
        listener_socket=object(),
        live_session=object(),
        acknowledge=lambda: (_ for _ in ()).throw(
            adoption.BackendListenerAdoptionError("backend_adoption_ack_write_failed")
        ),
        close=lambda: events.append("close"),
    )
    lease = Mock()
    lease.acquire.return_value = True
    with (
        patch("dashboard_server.parse_args", return_value=args),
        patch("dashboard_server.backend_listener_adoption_requested", return_value=True),
        patch("dashboard_server.load_backend_listener_adoption", return_value=adopted),
        patch(
            "dashboard_server.backend_bind_target_occupied",
            side_effect=AssertionError("adoption must not probe or bind"),
        ),
        patch("dashboard_server.BACKEND_OWNER_LEASE", lease),
        patch("dashboard_server.run_owned_uvicorn_server") as run_server,
        patch("builtins.print"),
    ):
        assert dashboard_server.main() == 1
    assert events == ["close"]
    lease.release.assert_called_once_with()
    run_server.assert_not_called()


def test_dashboard_runtime_install_failure_is_generic_and_releases_adoption() -> None:
    import dashboard_server

    args = dashboard_server.parse_args([])
    events: list[str] = []
    adopted = SimpleNamespace(
        listener_socket=object(),
        live_session=object(),
        acknowledge=lambda: events.append("ack"),
        close=lambda: events.append("close"),
    )
    connection = SimpleNamespace(
        bind=Mock(),
        validate=Mock(),
        inspect_fixture=Mock(),
        reload_fixture=Mock(),
        inspect_component=Mock(),
        preview_component=Mock(),
        read_compile_status=Mock(),
    )
    lease = Mock()
    lease.acquire.return_value = True
    original_session = dashboard_server.PRIMITIVE_BASIS_LIVE_SESSION
    original_connection = dashboard_server.PRIMITIVE_BASIS_LIVE_CONNECTION
    original_runtime = dashboard_server.PRIMITIVE_BASIS_LIVE_RUNTIME
    original_observer = dashboard_server.AGENT_GATEWAY.approval_transactions.apply_lifecycle_observer
    printed = Mock()
    try:
        with (
            patch("dashboard_server.parse_args", return_value=args),
            patch("dashboard_server.backend_listener_adoption_requested", return_value=True),
            patch("dashboard_server.load_backend_listener_adoption", return_value=adopted),
            patch("dashboard_server.BACKEND_OWNER_LEASE", lease),
            patch(
                "dashboard_server.PrimitiveBasisLiveUnityConnection",
                return_value=connection,
            ),
            patch(
                "dashboard_server.ModelPartCompositionLiveRuntime",
                side_effect=RuntimeError("private-runtime-detail"),
            ),
            patch("dashboard_server.run_owned_uvicorn_server") as run_server,
            patch("builtins.print", printed),
        ):
            assert dashboard_server.main() == 1

        assert events == ["close"]
        lease.release.assert_called_once_with()
        run_server.assert_not_called()
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_SESSION is None
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_CONNECTION is None
        assert dashboard_server.PRIMITIVE_BASIS_LIVE_RUNTIME is None
        assert dashboard_server.AGENT_GATEWAY.approval_transactions.apply_lifecycle_observer is None
        printed_text = " ".join(str(arg) for call in printed.call_args_list for arg in call.args)
        assert "protected listener adoption" in printed_text
        assert "private-runtime-detail" not in printed_text
    finally:
        dashboard_server.PRIMITIVE_BASIS_LIVE_SESSION = original_session
        dashboard_server.PRIMITIVE_BASIS_LIVE_CONNECTION = original_connection
        dashboard_server.PRIMITIVE_BASIS_LIVE_RUNTIME = original_runtime
        dashboard_server.AGENT_GATEWAY.approval_transactions.apply_lifecycle_observer = original_observer


def test_owned_uvicorn_server_passes_only_the_adopted_socket() -> None:
    import dashboard_server

    listener = object()
    server = Mock()
    with (
        patch.object(dashboard_server.uvicorn, "Config", return_value=object()),
        patch.object(dashboard_server.uvicorn, "Server", return_value=server),
    ):
        dashboard_server.run_owned_uvicorn_server(
            "127.0.0.1", 8757, sockets=[listener]
        )
    server.run.assert_called_once_with(sockets=[listener])
