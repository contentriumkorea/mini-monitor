# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import threading
import time
import math
from types import SimpleNamespace
from typing import Any

import pytest

from ai_mini_monitor.models import FrameRegion, FrameUpdate
from ai_mini_monitor.transport.device import DeviceDetector
from ai_mini_monitor.transport.protocol_rev_a import (
    DISPLAY_BITMAP,
    SET_BRIGHTNESS,
    Orientation,
    ProtocolValidationError,
    build_bitmap_command,
    build_brightness_command,
    build_orientation_command,
    build_screen_on_command,
    oriented_dimensions,
)
from ai_mini_monitor.transport.serial_writer import (
    ReconnectPolicy,
    SERIAL_WRITE_TIMEOUT_SECONDS,
    SerialWriter,
    SerialWriterState,
    InitialRestoreStatus,
)


def target_port(device: str = "COM_FAKE") -> SimpleNamespace:
    return SimpleNamespace(
        device=device,
        vid=0x1A86,
        pid=0x5722,
        serial_number="USB35INCHIPSV2",
        description="fake target",
        hwid="USB VID:PID=1A86:5722",
        location="test",
    )


def region(
    x: int,
    y: int,
    width: int,
    height: int,
    color: tuple[int, int, int],
) -> FrameRegion:
    return FrameRegion(
        x=x,
        y=y,
        width=width,
        height=height,
        rgb=bytes(color) * (width * height),
    )


def full_update(sequence: int = 1) -> FrameUpdate:
    return FrameUpdate(
        sequence=sequence,
        width=480,
        height=320,
        full_refresh=True,
        regions=(region(0, 0, 480, 320, (5, 7, 13)),),
        submitted_at=time.monotonic(),
    )


def oriented_full_update(
    orientation: Orientation,
    sequence: int = 1,
) -> FrameUpdate:
    width, height = oriented_dimensions(orientation)
    rgb = bytearray((5, 7, 13) * (width * height))
    rgb[:3] = bytes((255, 0, 0))
    rgb[-3:] = bytes((0, 0, 255))
    return FrameUpdate(
        sequence=sequence,
        width=width,
        height=height,
        full_refresh=True,
        regions=(FrameRegion(0, 0, width, height, bytes(rgb)),),
        submitted_at=time.monotonic(),
    )


def oriented_partial_update(
    orientation: Orientation,
    sequence: int,
) -> FrameUpdate:
    width, height = oriented_dimensions(orientation)
    return FrameUpdate(
        sequence=sequence,
        width=width,
        height=height,
        full_refresh=False,
        regions=(region(10, 40, 2, 2, (0, 255, 0)),),
        submitted_at=time.monotonic(),
    )


def partial_update(
    sequence: int,
    *,
    x: int,
    color: tuple[int, int, int],
) -> FrameUpdate:
    return FrameUpdate(
        sequence=sequence,
        width=480,
        height=320,
        full_refresh=False,
        regions=(region(x, 10, 2, 2, color),),
        submitted_at=time.monotonic(),
    )


class RecordingSerial:
    def __init__(
        self,
        *,
        short_write_call: int | None = None,
        block_call: int | None = None,
        block_data: bytes | None = None,
        **kwargs: Any,
    ) -> None:
        self.kwargs = kwargs
        self.short_write_call = short_write_call
        self.block_call = block_call
        self.block_data = block_data
        self.writes: list[bytes] = []
        self.closed = False
        self.dtr = False
        self.rts = False
        self.entered_block = threading.Event()
        self.release_block = threading.Event()

    def write(self, data: bytes) -> int:
        call_number = len(self.writes) + 1
        immutable = bytes(data)
        self.writes.append(immutable)
        if self.block_call == call_number or self.block_data == immutable:
            self.entered_block.set()
            assert self.release_block.wait(2.0), "test did not release fake serial"
        if self.short_write_call == call_number:
            return max(0, len(immutable) - 1)
        return len(immutable)

    def close(self) -> None:
        self.closed = True


class RecordingFactory:
    def __init__(self, serials: list[RecordingSerial] | None = None) -> None:
        self.serials = serials or []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> RecordingSerial:
        self.calls.append(kwargs)
        if self.serials:
            instance = self.serials.pop(0)
            instance.kwargs = kwargs
        else:
            instance = RecordingSerial(**kwargs)
        created.append(instance)
        return instance


created: list[RecordingSerial] = []


@pytest.fixture(autouse=True)
def clear_created() -> None:
    created.clear()


def make_writer(
    factory: RecordingFactory,
    *,
    brightness: int = 25,
) -> SerialWriter:
    detector = DeviceDetector(lambda: [target_port()])
    return SerialWriter(
        detector=detector,
        serial_factory=factory,
        reconnect_policy=ReconnectPolicy(
            initial_delay=0.001, maximum_delay=0.005, multiplier=2
        ),
        poll_interval=0.005,
        orientation=Orientation.LANDSCAPE,
        brightness=brightness,
        identity_check_interval=0.02,
        screen_on_settle_seconds=0,
        orientation_settle_seconds=0,
    )


def wait_for_command_count(
    serial_port: RecordingSerial,
    command: bytes,
    count: int = 1,
    *,
    timeout: float = 2.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while serial_port.writes.count(command) < count and time.monotonic() < deadline:
        time.sleep(0.005)
    return serial_port.writes.count(command) >= count


def test_writer_uses_finite_timeout_single_owner_and_closes_normally() -> None:
    factory = RecordingFactory()
    writer = make_writer(factory)
    writer.submit(full_update())
    writer.start()
    restored = writer.wait_for_initial_restore(1, timeout=3.0)
    assert restored.status is InitialRestoreStatus.READY
    assert restored.port == "COM_FAKE"
    assert len(created) == 1
    serial_port = created[0]
    assert factory.calls[0]["port"] == "COM_FAKE"
    assert factory.calls[0]["bytesize"] == 8
    assert factory.calls[0]["parity"] == "N"
    assert factory.calls[0]["stopbits"] == 1
    assert factory.calls[0]["write_timeout"] == SERIAL_WRITE_TIMEOUT_SECONDS
    assert factory.calls[0]["xonxoff"] is False
    assert factory.calls[0]["rtscts"] is False
    assert factory.calls[0]["dsrdtr"] is False
    assert serial_port.dtr is True
    assert serial_port.rts is True
    assert serial_port.writes[0] == build_brightness_command(25)
    assert serial_port.writes[1] == build_screen_on_command()
    assert serial_port.writes[2] == build_orientation_command(Orientation.LANDSCAPE)
    assert serial_port.writes[3] == build_brightness_command(25)
    assert serial_port.writes[4] == build_bitmap_command(0, 0, 480, 320)
    assert all(
        block[-1] != DISPLAY_BITMAP
        for block in serial_port.writes[5:]
        if len(block) == 6
    )
    assert writer.stop()
    assert serial_port.closed
    assert writer.state is SerialWriterState.STOPPED


def test_configured_brightness_is_applied_in_verified_vendor_order() -> None:
    factory = RecordingFactory()
    writer = make_writer(factory, brightness=37)
    assert writer.brightness_percent == 37
    writer.submit(full_update())
    writer.start()
    assert writer.wait_for_initial_restore(1, timeout=3.0).ready
    assert created[0].writes[:5] == [
        build_brightness_command(37),
        build_screen_on_command(),
        build_orientation_command(Orientation.LANDSCAPE),
        build_brightness_command(37),
        build_bitmap_command(0, 0, 480, 320),
    ]
    assert writer.stop()


@pytest.mark.parametrize(
    "value",
    [True, False, None, 25.0, "25", b"25", 0, 51, -1],
)
def test_live_brightness_rejects_invalid_types_and_ranges_before_io(value) -> None:
    factory = RecordingFactory()
    with pytest.raises(ProtocolValidationError):
        SerialWriter(serial_factory=factory, brightness=value)
    writer = make_writer(factory)
    with pytest.raises(ProtocolValidationError):
        writer.request_brightness(value)
    assert writer.brightness_percent == 25
    assert factory.calls == []
    assert writer.stop()


def test_startup_honors_vendor_settle_points_before_first_frame() -> None:
    factory = RecordingFactory()
    writer = SerialWriter(
        detector=DeviceDetector(lambda: [target_port()]),
        serial_factory=factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
        screen_on_settle_seconds=0.02,
        orientation_settle_seconds=0.03,
    )
    writer.submit(full_update())
    started = time.monotonic()
    writer.start()
    assert writer.wait_for_initial_restore(1, timeout=1.0).ready
    assert time.monotonic() - started >= 0.04
    assert created[0].writes[:5] == [
        build_brightness_command(25),
        build_screen_on_command(),
        build_orientation_command(Orientation.LANDSCAPE),
        build_brightness_command(25),
        build_bitmap_command(0, 0, 480, 320),
    ]
    assert writer.stop()


def test_stop_interrupts_screen_on_settle_before_orientation_or_frame() -> None:
    factory = RecordingFactory()
    writer = SerialWriter(
        detector=DeviceDetector(lambda: [target_port()]),
        serial_factory=factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
        screen_on_settle_seconds=2.0,
        orientation_settle_seconds=0,
    )
    writer.submit(full_update())
    writer.start()
    deadline = time.monotonic() + 1.0
    while (not created or len(created[0].writes) < 2) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert created and created[0].writes == [
        build_brightness_command(25),
        build_screen_on_command(),
    ]
    started = time.monotonic()
    assert writer.stop(timeout=0.5)
    assert time.monotonic() - started < 0.5
    assert created[0].closed


@pytest.mark.parametrize("value", [True, "0.1", -0.1, math.nan, math.inf])
def test_settle_times_must_be_finite_non_negative_numbers(value) -> None:
    with pytest.raises(ValueError):
        SerialWriter(screen_on_settle_seconds=value)
    with pytest.raises(ValueError):
        SerialWriter(orientation_settle_seconds=value)


def test_access_denied_waits_for_explicit_retry_and_emits_one_error_event() -> None:
    calls: list[dict[str, Any]] = []
    events = []

    def denied_factory(**kwargs: Any):
        calls.append(kwargs)
        raise OSError(
            "could not open port 'COM3': "
            "PermissionError(13, '액세스가 거부되었습니다.', None, 5)"
        )

    writer = SerialWriter(
        detector=DeviceDetector(lambda: [target_port("COM3")]),
        serial_factory=denied_factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        status_callback=events.append,
        poll_interval=0.005,
    )
    writer.submit(full_update())
    writer.start()

    restored = writer.wait_for_initial_restore(1, timeout=0.5)
    assert restored.status is InitialRestoreStatus.PORT_IN_USE
    assert restored.port == "COM3"
    time.sleep(0.05)
    assert len(calls) == 1
    error_events = [event for event in events if event.error]
    assert len(error_events) == 1
    assert error_events[0].port == "COM3"

    writer.request_reconnect()
    deadline = time.monotonic() + 0.5
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    time.sleep(0.05)
    assert len(calls) == 2
    assert len([event for event in events if event.error]) == 2
    assert writer.stop()


def test_latest_wins_keeps_only_one_pending_update_during_transfer() -> None:
    first_serial = RecordingSerial(block_call=5)
    factory = RecordingFactory([first_serial])
    writer = make_writer(factory)
    writer.submit(full_update(1))
    writer.start()
    assert first_serial.entered_block.wait(2.0)

    writer.submit(partial_update(2, x=10, color=(255, 0, 0)))
    writer.submit(partial_update(3, x=20, color=(0, 255, 0)))
    assert writer.pending_count == 1
    assert writer.replaced_count >= 1
    first_serial.release_block.set()

    assert writer.wait_for_sequence(3, timeout=3.0)
    assert writer.pending_count == 0
    headers = [block for block in first_serial.writes if len(block) == 6]
    # Sequence 2 is replaced as a queue item, but its desired red pixels are
    # folded into sequence 3's target framebuffer. Distant changes remain two
    # safe patches, so no physical state is lost and no large overdraw box is
    # introduced when latest-wins discards the stale queue entry.
    red_header = build_bitmap_command(9, 9, 4, 4)
    green_header = build_bitmap_command(19, 9, 4, 4)
    assert red_header in headers
    assert green_header in headers
    assert build_bitmap_command(10, 10, 2, 2) not in headers
    assert build_bitmap_command(20, 10, 2, 2) not in headers
    red_payload = first_serial.writes[first_serial.writes.index(red_header) + 1]
    green_payload = first_serial.writes[first_serial.writes.index(green_header) + 1]
    assert b"\x00\xf8" in red_payload  # RGB565LE red from sequence 2
    assert b"\xe0\x07" in green_payload  # RGB565LE green from sequence 3
    assert writer.stop()


def test_live_brightness_is_latest_wins_and_never_splits_a_frame_transfer() -> None:
    serial_port = RecordingSerial(block_call=5)
    factory = RecordingFactory([serial_port])
    writer = make_writer(factory)
    writer.submit(full_update(1))
    writer.start()
    assert serial_port.entered_block.wait(2.0)

    assert writer.request_brightness(10) is True
    assert writer.request_brightness(20) is True
    assert writer.request_brightness(40) is True
    assert writer.request_brightness(40) is False
    assert writer.brightness_percent == 40
    serial_port.release_block.set()

    assert writer.wait_for_sequence(1, timeout=3.0)
    assert wait_for_command_count(serial_port, build_brightness_command(40))
    brightness_commands = [
        block
        for block in serial_port.writes
        if len(block) == 6 and block[-1] == SET_BRIGHTNESS
    ]
    assert brightness_commands == [
        build_brightness_command(25),
        build_brightness_command(25),
        build_brightness_command(40),
    ]
    assert build_brightness_command(10) not in serial_port.writes
    assert build_brightness_command(20) not in serial_port.writes
    frame_header = build_bitmap_command(0, 0, 480, 320)
    frame_header_index = serial_port.writes.index(frame_header)
    brightness_index = serial_port.writes.index(build_brightness_command(40))
    assert brightness_index > frame_header_index + 1
    assert writer.stop()


def test_pending_live_brightness_can_be_cancelled_without_a_serial_command() -> None:
    serial_port = RecordingSerial(block_call=5)
    factory = RecordingFactory([serial_port])
    writer = make_writer(factory)
    writer.submit(full_update(1))
    writer.start()
    assert serial_port.entered_block.wait(2.0)

    assert writer.request_brightness(45) is True
    assert writer.cancel_pending_brightness() is True
    assert writer.cancel_pending_brightness() is False
    assert writer.brightness_percent == 25
    serial_port.release_block.set()

    assert writer.wait_for_sequence(1, timeout=3.0)
    time.sleep(0.03)
    brightness_commands = [
        block
        for block in serial_port.writes
        if len(block) == 6 and block[-1] == SET_BRIGHTNESS
    ]
    assert brightness_commands == [
        build_brightness_command(25),
        build_brightness_command(25),
    ]
    assert build_brightness_command(45) not in serial_port.writes
    assert writer.stop()
    assert writer.request_brightness(30) is False


def test_inflight_brightness_cancellation_restores_the_applied_value() -> None:
    requested = build_brightness_command(45)
    serial_port = RecordingSerial(block_data=requested)
    factory = RecordingFactory([serial_port])
    writer = make_writer(factory)
    writer.submit(full_update(1))
    writer.start()
    assert writer.wait_for_initial_restore(1, timeout=3.0).ready

    assert writer.request_brightness(45) is True
    assert serial_port.entered_block.wait(2.0)
    assert writer.cancel_pending_brightness() is True
    assert writer.brightness_percent == 25
    serial_port.release_block.set()

    assert wait_for_command_count(
        serial_port,
        build_brightness_command(25),
        count=3,
    )
    brightness_commands = [
        block
        for block in serial_port.writes
        if len(block) == 6 and block[-1] == SET_BRIGHTNESS
    ]
    assert brightness_commands == [
        build_brightness_command(25),
        build_brightness_command(25),
        requested,
        build_brightness_command(25),
    ]
    assert writer.stop()


def test_short_partial_transfer_is_abandoned_then_full_frame_is_restored() -> None:
    # brightness, screen on, orientation, brightness, bitmap header, payload
    failed = RecordingSerial(short_write_call=6)
    recovered = RecordingSerial()
    factory = RecordingFactory([failed, recovered])
    writer = make_writer(factory)
    writer.submit(full_update(1))
    writer.start()

    assert writer.wait_for_sequence(1, timeout=4.0)
    assert writer.wait_for_connections(1, timeout=4.0)
    assert len(created) >= 2
    assert failed.closed
    # No continuation or same-connection retry follows the short write.
    assert len(failed.writes) == 6
    assert recovered.writes[0] == build_brightness_command(25)
    assert recovered.writes[1] == build_screen_on_command()
    assert recovered.writes[2] == build_orientation_command(Orientation.LANDSCAPE)
    assert recovered.writes[3] == build_brightness_command(25)
    assert recovered.writes[4] == build_bitmap_command(0, 0, 480, 320)
    assert writer.stop()
    assert recovered.closed


def test_invalid_updates_never_reach_serial_factory() -> None:
    factory = RecordingFactory()
    writer = make_writer(factory)
    with pytest.raises(ProtocolValidationError):
        writer.submit(partial_update(1, x=10, color=(1, 2, 3)))
    malformed = FrameUpdate(
        sequence=1,
        width=480,
        height=320,
        full_refresh=True,
        regions=(FrameRegion(0, 0, 480, 320, b"too short"),),
        submitted_at=time.monotonic(),
    )
    with pytest.raises(ProtocolValidationError):
        writer.submit(malformed)
    assert not factory.calls
    assert writer.stop()


def test_cancel_while_device_selection_is_blocked_never_opens_com() -> None:
    class BlockingDetector:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def select(self, manual_port: str | None):
            del manual_port
            self.entered.set()
            assert self.release.wait(2.0), "test did not release fake detection"
            return target_port()

        def revalidate(self, selected):
            return selected

    detector = BlockingDetector()
    factory = RecordingFactory()
    writer = SerialWriter(
        detector=detector,
        serial_factory=factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
    )
    writer.submit(full_update())
    writer.start()
    assert detector.entered.wait(1.0)

    writer.cancel_pending_start()
    detector.release.set()

    assert writer.wait_for_state(SerialWriterState.STOPPED, timeout=2.0)
    assert factory.calls == []
    assert created == []
    assert writer.stop()


def test_cancel_and_serial_factory_share_one_admission_boundary() -> None:
    class AdmissionDetector:
        def select(self, manual_port: str | None):
            del manual_port
            return target_port()

        def revalidate(self, selected):
            return selected

    factory_entered = threading.Event()
    factory_release = threading.Event()
    cancel_returned = threading.Event()
    factory_calls: list[dict[str, Any]] = []

    def blocking_factory(**kwargs: Any) -> RecordingSerial:
        factory_calls.append(kwargs)
        factory_entered.set()
        assert factory_release.wait(2.0), "test did not release fake factory"
        return RecordingSerial(**kwargs)

    writer = SerialWriter(
        detector=AdmissionDetector(),
        serial_factory=blocking_factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
    )
    writer.submit(full_update())
    writer.start()
    assert factory_entered.wait(1.0)

    cancel_thread = threading.Thread(
        target=lambda: (writer.cancel_pending_start(), cancel_returned.set())
    )
    cancel_thread.start()
    assert not cancel_returned.wait(0.05)
    assert len(factory_calls) == 1
    factory_release.set()
    cancel_thread.join(1.0)

    assert cancel_returned.is_set()
    assert writer.wait_for_state(SerialWriterState.STOPPED, timeout=2.0)
    assert len(factory_calls) == 1
    assert writer.stop()


def test_stop_timeout_includes_wait_for_inflight_factory_admission() -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_factory(**kwargs: Any) -> RecordingSerial:
        entered.set()
        assert release.wait(2.0), "test did not release fake factory"
        return RecordingSerial(**kwargs)

    writer = SerialWriter(
        detector=DeviceDetector(lambda: [target_port()]),
        serial_factory=blocking_factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
    )
    writer.submit(full_update())
    writer.start()
    assert entered.wait(1.0)

    began = time.monotonic()
    assert writer.stop(timeout=0.05) is False
    assert time.monotonic() - began < 0.25

    release.set()
    assert writer.stop(timeout=1.0)
    assert writer.state is SerialWriterState.STOPPED


def test_suspend_timeout_includes_wait_for_inflight_factory_admission() -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_factory(**kwargs: Any) -> RecordingSerial:
        entered.set()
        assert release.wait(2.0), "test did not release fake factory"
        return RecordingSerial(**kwargs)

    writer = SerialWriter(
        detector=DeviceDetector(lambda: [target_port()]),
        serial_factory=blocking_factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
    )
    writer.submit(full_update())
    writer.start()
    assert entered.wait(1.0)

    began = time.monotonic()
    assert writer.suspend(timeout=0.05) is False
    assert time.monotonic() - began < 0.25

    release.set()
    assert writer.wait_for_state(SerialWriterState.SUSPENDED, timeout=1.0)
    assert writer.stop(timeout=1.0)


def test_resume_is_prompt_while_an_inflight_factory_holds_lifecycle_lock() -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_factory(**kwargs: Any) -> RecordingSerial:
        entered.set()
        assert release.wait(2.0), "test did not release fake factory"
        return RecordingSerial(**kwargs)

    writer = SerialWriter(
        detector=DeviceDetector(lambda: [target_port()]),
        serial_factory=blocking_factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
    )
    writer.submit(full_update())
    writer.start()
    assert entered.wait(1.0)
    assert writer.suspend(timeout=0.05) is False

    began = time.monotonic()
    assert writer.resume() is True
    assert time.monotonic() - began < 0.25
    assert writer.resume() is False

    release.set()
    assert writer.wait_for_sequence(1, timeout=2.0)
    assert writer.stop(timeout=1.0)


def test_manual_port_is_revalidated_before_first_byte() -> None:
    calls = 0

    def provider() -> list[SimpleNamespace]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [target_port("COM9")]
        return [
            SimpleNamespace(
                device="COM9",
                vid=0x1A86,
                pid=0x5722,
                serial_number="OTHER",
                description="wrong device",
                hwid="wrong",
                location="test",
            )
        ]

    factory = RecordingFactory()
    writer = SerialWriter(
        detector=DeviceDetector(provider),
        serial_factory=factory,
        manual_port="COM9",
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
    )
    writer.start()
    assert writer.wait_for_state(SerialWriterState.RECONNECTING)
    deadline = time.monotonic() + 1.0
    while not created and time.monotonic() < deadline:
        time.sleep(0.005)
    assert created
    assert created[0].writes == []
    assert created[0].closed
    assert writer.stop()


def test_idle_detach_is_detected_without_waiting_for_a_dirty_frame() -> None:
    calls = 0

    def provider() -> list[SimpleNamespace]:
        nonlocal calls
        calls += 1
        # Initial select + pre-write revalidation succeed. The periodic idle
        # identity check then observes the physical detach.
        return [target_port()] if calls <= 2 else []

    factory = RecordingFactory()
    writer = SerialWriter(
        detector=DeviceDetector(provider),
        serial_factory=factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
        identity_check_interval=0.01,
    )
    writer.submit(full_update(1))
    writer.start()
    assert writer.wait_for_sequence(1, timeout=3.0)
    deadline = time.monotonic() + 1.0
    while writer.state is SerialWriterState.ONLINE and time.monotonic() < deadline:
        time.sleep(0.005)
    assert writer.state is SerialWriterState.RECONNECTING
    assert created[0].closed
    assert writer.stop()


def test_auto_detection_recovers_when_windows_assigns_a_new_com_number() -> None:
    calls = 0

    def provider() -> list[SimpleNamespace]:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return [target_port("COM3")]
        if calls == 3:
            return []
        return [target_port("COM7")]

    factory = RecordingFactory()
    writer = SerialWriter(
        detector=DeviceDetector(provider),
        serial_factory=factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
        identity_check_interval=0.01,
    )
    writer.submit(full_update(1))
    writer.start()
    assert writer.wait_for_connections(2, timeout=3.0)
    assert writer.current_port == "COM7"
    assert factory.calls[0]["port"] == "COM3"
    assert factory.calls[1]["port"] == "COM7"
    assert created[0].closed
    assert writer.stop()


def test_power_suspend_closes_on_owner_and_resume_restores_latest_full_frame() -> None:
    first = RecordingSerial()
    resumed = RecordingSerial()
    factory = RecordingFactory([first, resumed])
    writer = make_writer(factory)
    writer.submit(full_update(1))
    writer.start()
    assert writer.wait_for_sequence(1, timeout=3.0)
    assert writer.wait_for_state(SerialWriterState.ONLINE)

    # The call waits until the serial owner has closed the handle and entered
    # the paused state. No detector/factory activity is allowed while paused.
    assert writer.suspend(timeout=2.0)
    assert writer.state is SerialWriterState.SUSPENDED
    assert writer.current_port is None
    assert first.closed
    connections_while_paused = len(factory.calls)

    writer.request_reconnect()  # ignored while explicitly power-paused
    writer.submit(partial_update(2, x=20, color=(0, 255, 0)))
    assert writer.pending_count == 1
    time.sleep(0.05)
    assert len(factory.calls) == connections_while_paused == 1
    assert writer.state is SerialWriterState.SUSPENDED

    assert writer.resume() is True
    assert writer.resume() is False  # duplicate Windows resume broadcasts
    assert writer.wait_for_connections(2, timeout=3.0)
    assert writer.wait_for_sequence(2, timeout=3.0)
    assert writer.current_port == "COM_FAKE"
    assert resumed.writes[0] == build_brightness_command(25)
    assert resumed.writes[1] == build_screen_on_command()
    assert resumed.writes[2] == build_orientation_command(Orientation.LANDSCAPE)
    assert resumed.writes[3] == build_brightness_command(25)
    assert resumed.writes[4] == build_bitmap_command(0, 0, 480, 320)
    assert writer.sent_full_refresh_count == 2
    assert len(factory.calls) == 2
    assert writer.stop()
    assert resumed.closed


def test_latest_brightness_is_reapplied_on_reconnect_and_resume() -> None:
    initial = RecordingSerial()
    reconnected = RecordingSerial()
    resumed = RecordingSerial()
    factory = RecordingFactory([initial, reconnected, resumed])
    writer = make_writer(factory, brightness=31)
    writer.submit(full_update(1))
    writer.start()
    assert writer.wait_for_initial_restore(1, timeout=3.0).ready
    assert initial.writes[:4] == [
        build_brightness_command(31),
        build_screen_on_command(),
        build_orientation_command(Orientation.LANDSCAPE),
        build_brightness_command(31),
    ]

    assert writer.request_brightness(44) is True
    assert wait_for_command_count(initial, build_brightness_command(44))
    writer.request_reconnect()
    assert writer.wait_for_connections(2, timeout=3.0)
    assert reconnected.writes[:4] == [
        build_brightness_command(44),
        build_screen_on_command(),
        build_orientation_command(Orientation.LANDSCAPE),
        build_brightness_command(44),
    ]

    assert writer.suspend(timeout=2.0)
    assert writer.request_brightness(17) is True
    assert writer.resume() is True
    assert writer.wait_for_connections(3, timeout=3.0)
    assert resumed.writes[:4] == [
        build_brightness_command(17),
        build_screen_on_command(),
        build_orientation_command(Orientation.LANDSCAPE),
        build_brightness_command(17),
    ]
    assert len(factory.calls) == 3
    assert writer.stop()


@pytest.mark.parametrize("orientation", list(Orientation))
def test_all_orientations_start_and_resume_with_exact_dynamic_geometry(
    orientation: Orientation,
) -> None:
    first = RecordingSerial()
    resumed = RecordingSerial()
    factory = RecordingFactory([first, resumed])
    writer = SerialWriter(
        detector=DeviceDetector(lambda: [target_port()]),
        serial_factory=factory,
        reconnect_policy=ReconnectPolicy(0.001, 0.005, 2),
        poll_interval=0.005,
        identity_check_interval=0.02,
        screen_on_settle_seconds=0,
        orientation_settle_seconds=0,
        orientation=orientation,
    )
    width, height = oriented_dimensions(orientation)
    expected_header = build_bitmap_command(
        0,
        0,
        width,
        height,
        display_width=width,
        display_height=height,
    )

    writer.submit(oriented_full_update(orientation, 1))
    writer.start()
    assert writer.wait_for_initial_restore(1, timeout=3.0).ready
    assert first.writes[2] == build_orientation_command(orientation)
    assert first.writes[4] == expected_header
    assert len(first.writes[5]) == width * 8
    # Reverse modes are performed by the audited firmware orientation enum;
    # the host keeps its genuine logical raster in row-major order.
    assert first.writes[5][:2] == b"\x00\xf8"

    assert writer.suspend(timeout=2.0)
    writer.submit(oriented_partial_update(orientation, 2))
    assert writer.resume()
    assert writer.wait_for_connections(2, timeout=3.0)
    assert writer.wait_for_sequence(2, timeout=3.0)
    assert resumed.writes[2] == build_orientation_command(orientation)
    assert resumed.writes[4] == expected_header
    assert len(resumed.writes[5]) == width * 8
    assert writer.sent_full_refresh_count == 2
    assert writer.stop()
