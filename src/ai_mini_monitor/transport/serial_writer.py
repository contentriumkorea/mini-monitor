# SPDX-License-Identifier: GPL-3.0-or-later
#
# Protocol behavior was audited against turing-smart-screen-python commit
# 262a28a3ab615f2047c0bf44afc482cc341c465c.
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine) and contributors.
# Modified 2026-08-10 by AI Mini Monitor contributors: the upstream unbounded
# FIFO/retry lifecycle was replaced by a single owner, latest-wins queue, strict
# revalidation, finite write timeout, and full-frame recovery after interruption.

"""Single-owner serial writer with bounded latest-wins scheduling."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import serial
from PIL import Image

from ..models import FrameRegion, FrameUpdate
from ..rendering.dirty import calculate_dirty_rectangles
from ..rendering.layout import DashboardLayout, layout_for_dimensions
from .device import DeviceDetector, DeviceInfo, DeviceSelectionError
from .latest_wins import LatestWinsQueue, QueueClosed
from .protocol_rev_a import (
    DEFAULT_BRIGHTNESS_PERCENT,
    Orientation,
    ProtocolValidationError,
    build_brightness_command,
    build_orientation_command,
    build_screen_on_command,
    encode_rgb888_transfer,
    oriented_dimensions,
)


SERIAL_BAUDRATE = 115_200
SERIAL_READ_TIMEOUT_SECONDS = 1.0
SERIAL_WRITE_TIMEOUT_SECONDS = 1.0
SCREEN_ON_SETTLE_SECONDS = 0.1
ORIENTATION_SETTLE_SECONDS = 0.5


class SerialWriterState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    ONLINE = "ONLINE"
    SUSPENDED = "SUSPENDED"
    STOPPED = "STOPPED"


class InitialRestoreStatus(str, Enum):
    """Safe, UI-facing outcome of the first complete framebuffer transfer."""

    READY = "ready"
    PORT_IN_USE = "port_in_use"
    DEVICE_ABSENT = "device_absent"
    LINK_ERROR = "link_error"
    TIMEOUT = "timeout"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class InitialRestoreResult:
    status: InitialRestoreStatus
    port: str | None = None

    @property
    def ready(self) -> bool:
        return self.status is InitialRestoreStatus.READY


@dataclass(frozen=True, slots=True)
class SerialWriterEvent:
    state: SerialWriterState
    port: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class _QueuedFrame:
    update: FrameUpdate
    framebuffer: bytes


@dataclass(frozen=True, slots=True)
class _BrightnessRequest:
    revision: int
    percent: int


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    initial_delay: float = 0.25
    maximum_delay: float = 8.0
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.initial_delay < 0:
            raise ValueError("initial reconnect delay must be non-negative")
        if self.maximum_delay < self.initial_delay:
            raise ValueError("maximum reconnect delay must be >= initial delay")
        if self.multiplier < 1:
            raise ValueError("reconnect multiplier must be >= 1")

    def delay_for_failure(self, failure_count: int) -> float:
        if failure_count <= 0:
            return 0.0
        return min(
            self.maximum_delay,
            self.initial_delay * (self.multiplier ** (failure_count - 1)),
        )


class SerialPort(Protocol):
    dtr: bool
    rts: bool

    def write(self, data: bytes) -> int | None: ...

    def close(self) -> None: ...


SerialFactory = Callable[..., SerialPort]
StatusCallback = Callable[[SerialWriterEvent], None]


class SerialTransferError(OSError):
    """The current command/payload is no longer safe to continue."""


class _ReconnectRequested(Exception):
    pass


class _SuspendRequested(Exception):
    pass


class _StopRequested(Exception):
    pass


def _default_serial_factory(**kwargs: Any) -> SerialPort:
    return serial.Serial(**kwargs)


class SerialWriter:
    """Own the COM handle on one thread and keep at most one pending update.

    ``FrameRegion.rgb`` is expected to contain tightly packed RGB888 pixels.
    A full refresh must cover the selected logical display dimensions. The complete RGB
    framebuffer is cached so a reconnect can restore current state without
    retrying the interrupted payload on the failed connection.
    """

    def __init__(
        self,
        *,
        detector: DeviceDetector | None = None,
        serial_factory: SerialFactory | None = None,
        manual_port: str | None = None,
        reconnect_policy: ReconnectPolicy | None = None,
        status_callback: StatusCallback | None = None,
        orientation: Orientation = Orientation.LANDSCAPE,
        brightness: int = DEFAULT_BRIGHTNESS_PERCENT,
        payload_chunk_size: int | None = None,
        poll_interval: float = 0.05,
        identity_check_interval: float = 1.0,
        screen_on_settle_seconds: float = SCREEN_ON_SETTLE_SECONDS,
        orientation_settle_seconds: float = ORIENTATION_SETTLE_SECONDS,
    ) -> None:
        if payload_chunk_size is not None and (
            isinstance(payload_chunk_size, bool)
            or not isinstance(payload_chunk_size, int)
            or payload_chunk_size <= 0
        ):
            raise ValueError("payload chunk size must be positive")
        if poll_interval <= 0:
            raise ValueError("poll interval must be positive")
        if identity_check_interval <= 0:
            raise ValueError("identity check interval must be positive")
        for name, value in (
            ("screen-on settle", screen_on_settle_seconds),
            ("orientation settle", orientation_settle_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} time must be finite and non-negative")
        if not isinstance(orientation, Orientation):
            raise ValueError("orientation must be an audited Orientation value")
        # Constructing the allowlisted command is the single protocol-level
        # validation path. It rejects bools, strings, and values outside 1..50
        # before the writer can start or a COM handle can be opened.
        build_brightness_command(brightness)

        self._detector = detector or DeviceDetector()
        self._serial_factory = serial_factory or _default_serial_factory
        self._manual_port = manual_port
        self._reconnect_policy = reconnect_policy or ReconnectPolicy()
        self._status_callback = status_callback
        self._orientation = orientation
        self._display_width, self._display_height = oriented_dimensions(orientation)
        self._layout: DashboardLayout = layout_for_dimensions(
            self._display_width, self._display_height
        )
        self._payload_chunk_size = (
            self._display_width * 8
            if payload_chunk_size is None
            else payload_chunk_size
        )
        self._poll_interval = poll_interval
        self._identity_check_interval = identity_check_interval
        self._screen_on_settle_seconds = float(screen_on_settle_seconds)
        self._orientation_settle_seconds = float(orientation_settle_seconds)

        self._queue: LatestWinsQueue[_QueuedFrame] = LatestWinsQueue()
        self._stop_event = threading.Event()
        self._reconnect_event = threading.Event()
        self._suspend_event = threading.Event()
        self._control_wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._power_control_lock = threading.Lock()
        self._submit_lock = threading.Lock()
        self._brightness_lock = threading.Lock()

        # Brightness uses a separate single-slot desired state, not the frame
        # queue. The serial owner consumes it only between complete bitmap
        # transfers, so a command can never split a header from its payload.
        self._brightness_revision = 0
        self._desired_brightness = brightness
        self._applied_brightness = brightness
        self._brightness_pending = False
        self._brightness_inflight: _BrightnessRequest | None = None

        framebuffer_size = self._display_width * self._display_height * 3
        self._framebuffer = bytearray(framebuffer_size)
        self._framebuffer_valid = False
        self._sent_framebuffer = bytes(framebuffer_size)
        self._sent_framebuffer_valid = False
        self._latest_sequence = -1
        self._cache_lock = threading.Lock()

        self._condition = threading.Condition()
        self._state = SerialWriterState.DISCONNECTED
        self._current_port: str | None = None
        self._last_attempted_port: str | None = None
        self._last_error: str | None = None
        self._last_sent_sequence = -1
        self._connection_count = 0
        self._sent_update_count = 0
        self._sent_region_count = 0
        self._sent_full_refresh_count = 0
        self._fatal_error: BaseException | None = None

    @property
    def state(self) -> SerialWriterState:
        with self._condition:
            return self._state

    @property
    def current_port(self) -> str | None:
        with self._condition:
            return self._current_port

    @property
    def last_error(self) -> str | None:
        with self._condition:
            return self._last_error

    @property
    def fatal_error(self) -> BaseException | None:
        with self._condition:
            return self._fatal_error

    @property
    def pending_count(self) -> int:
        return self._queue.pending_count

    @property
    def replaced_count(self) -> int:
        return self._queue.replaced_count

    @property
    def last_sent_sequence(self) -> int:
        with self._condition:
            return self._last_sent_sequence

    @property
    def connection_count(self) -> int:
        with self._condition:
            return self._connection_count

    @property
    def sent_update_count(self) -> int:
        with self._condition:
            return self._sent_update_count

    @property
    def sent_region_count(self) -> int:
        with self._condition:
            return self._sent_region_count

    @property
    def sent_full_refresh_count(self) -> int:
        with self._condition:
            return self._sent_full_refresh_count

    @property
    def brightness_percent(self) -> int:
        """Return the latest validated brightness desired for this writer."""

        with self._brightness_lock:
            return self._desired_brightness

    def start(self) -> None:
        """Start the writer; this is the first operation that may open a COM port."""

        with self._lifecycle_lock:
            if self._stop_event.is_set():
                raise RuntimeError("serial writer has been stopped")
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="mini-monitor-serial-writer",
                daemon=True,
            )
            self._thread.start()

    def submit(self, update: FrameUpdate) -> None:
        """Validate/cache an update, replacing the one pending update if needed."""

        if not isinstance(update, FrameUpdate):
            raise TypeError("update must be a FrameUpdate")
        with self._submit_lock:
            if self._stop_event.is_set():
                raise RuntimeError("serial writer has been stopped")
            queued = self._validate_and_cache(update)
            self._queue.put(queued)

    def request_brightness(self, percent: int) -> bool:
        """Coalesce one live brightness request for the serial owner thread.

        The request never opens a port and never writes from the caller thread.
        ``False`` means the same value was already desired; ``True`` means the
        single pending value changed.
        """

        build_brightness_command(percent)
        with self._brightness_lock:
            if self._stop_event.is_set():
                return False
            if percent == self._desired_brightness:
                return False
            self._brightness_revision += 1
            self._desired_brightness = percent
            inflight = self._brightness_inflight
            self._brightness_pending = (
                percent != self._applied_brightness
                or (inflight is not None and inflight.percent != percent)
            )
        return True

    def cancel_pending_brightness(self) -> bool:
        """Cancel uncommitted brightness work and retain the applied value.

        If the six-byte command is already inside the OS write call, the owner
        schedules the previously applied value once more as compensation. No
        caller-thread I/O and no additional COM handle are used.
        """

        with self._brightness_lock:
            if self._stop_event.is_set():
                return False
            inflight = self._brightness_inflight
            changed = self._brightness_pending or (
                inflight is not None
                and inflight.percent != self._applied_brightness
            )
            if not changed:
                return False
            self._brightness_revision += 1
            self._desired_brightness = self._applied_brightness
            self._brightness_pending = (
                inflight is not None
                and inflight.percent != self._applied_brightness
            )
        return True

    def request_reconnect(self) -> None:
        """Ask the owner thread to abandon the current session and re-enumerate."""

        if not self._stop_event.is_set() and not self._suspend_event.is_set():
            self._reconnect_event.set()
            self._control_wake_event.set()

    def suspend(self, timeout: float = 2.0) -> bool:
        """Pause I/O and wait boundedly for the owner thread to close COM.

        The latest desired framebuffer and the one-slot pending update are kept.
        Only the serial owner thread closes the handle.  ``False`` means the
        bounded wait expired; callers must not assume the handle is closed in
        that case.
        """

        if isinstance(timeout, bool):
            raise ValueError("suspend timeout must be positive")
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as error:
            raise ValueError("suspend timeout must be positive") from error
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError("suspend timeout must be positive")
        deadline = time.monotonic() + timeout_value
        # Signal first so the owner can close COM even if an OS open currently
        # holds the lifecycle barrier. Lock acquisition is part of the same
        # caller-supplied timeout budget.
        if self._stop_event.is_set():
            return False
        self._suspend_event.set()
        self._reconnect_event.clear()
        self._control_wake_event.set()
        if not self._lifecycle_lock.acquire(timeout=timeout_value):
            return False
        try:
            if self._stop_event.is_set():
                return False
            thread = self._thread
            if thread is None:
                self._set_state(
                    SerialWriterState.SUSPENDED,
                    port=None,
                    error=None,
                )
                return True
        finally:
            self._lifecycle_lock.release()
        remaining = max(0.0, deadline - time.monotonic())
        return self.wait_for_state(SerialWriterState.SUSPENDED, timeout=remaining)

    def resume(self) -> bool:
        """Resume after a power pause and request one strict full restore.

        Returns ``True`` only for the transition from paused to running, making
        duplicate Windows resume notifications harmless.
        """

        # Resume only changes lock-free control events; it must not wait behind
        # a synchronous Windows COM open that was already admitted. A separate
        # short lock preserves duplicate-notification idempotence.
        with self._power_control_lock:
            if self._stop_event.is_set() or not self._suspend_event.is_set():
                return False
            self._suspend_event.clear()
            self._control_wake_event.set()
            if self._thread is None:
                self._set_state(
                    SerialWriterState.DISCONNECTED,
                    port=None,
                    error=None,
                )
            return True

    def stop(self, timeout: float = 3.0) -> bool:
        """Stop the owner thread and close its COM handle on that same thread."""

        if isinstance(timeout, bool):
            raise ValueError("stop timeout must be positive")
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as error:
            raise ValueError("stop timeout must be positive") from error
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError("stop timeout must be positive")
        deadline = time.monotonic() + timeout_value
        self.signal_stop()
        if not self._lifecycle_lock.acquire(timeout=timeout_value):
            return False
        try:
            thread = self._thread
            if thread is None:
                self._set_state(SerialWriterState.STOPPED, port=None, error=None)
                return True
        finally:
            self._lifecycle_lock.release()
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(remaining)
        return not thread.is_alive()

    def cancel_pending_start(self) -> None:
        """Permanently revoke connect admission without waiting on the GUI thread."""

        self.signal_stop()
        with self._lifecycle_lock:
            if self._thread is None:
                self._set_state(SerialWriterState.STOPPED, port=None, error=None)

    def signal_stop(self) -> None:
        """Latch stop immediately without waiting for an in-flight OS open.

        The owner thread still performs handle cleanup. ``stop()`` or
        ``cancel_pending_start()`` can subsequently provide a bounded join or
        strict admission barrier, while GUI power/Exit callbacks stay prompt.
        """

        self._stop_event.set()
        self._control_wake_event.set()
        self._queue.close()
        with self._condition:
            self._condition.notify_all()

    def wait_for_sequence(self, sequence: int, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._last_sent_sequence < sequence and self._fatal_error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._state is SerialWriterState.STOPPED:
                    return False
                self._condition.wait(remaining)
            return self._last_sent_sequence >= sequence

    def wait_for_initial_restore(
        self,
        sequence: int,
        timeout: float = 15.0,
    ) -> InitialRestoreResult:
        """Wait until the first full frame is written and the writer is ONLINE.

        This confirms only that the audited brightness, screen-on, orientation,
        and complete framebuffer writes were accepted by the OS serial path.
        The display has no acknowledgement protocol, so this must not be
        described as physical LCD confirmation.
        """

        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("initial restore sequence must be a non-negative integer")
        if isinstance(timeout, bool):
            raise ValueError("initial restore timeout must be a positive finite number")
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "initial restore timeout must be a positive finite number"
            ) from error
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError("initial restore timeout must be a positive finite number")

        deadline = time.monotonic() + timeout_value
        with self._condition:
            while True:
                port = self._current_port or self._last_attempted_port
                if (
                    self._state is SerialWriterState.ONLINE
                    and self._last_sent_sequence >= sequence
                ):
                    return InitialRestoreResult(InitialRestoreStatus.READY, port)
                if is_port_in_use_error(self._last_error):
                    return InitialRestoreResult(
                        InitialRestoreStatus.PORT_IN_USE,
                        port,
                    )
                if self._fatal_error is not None:
                    return InitialRestoreResult(InitialRestoreStatus.LINK_ERROR, port)
                if self._stop_event.is_set() or self._state is SerialWriterState.STOPPED:
                    return InitialRestoreResult(InitialRestoreStatus.STOPPED, port)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if _is_device_absent_error(self._last_error):
                        status = InitialRestoreStatus.DEVICE_ABSENT
                    elif self._last_error:
                        status = InitialRestoreStatus.LINK_ERROR
                    else:
                        status = InitialRestoreStatus.TIMEOUT
                    return InitialRestoreResult(status, port)
                self._condition.wait(remaining)

    def wait_for_state(self, state: SerialWriterState, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._state is not state:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def wait_for_connections(self, count: int, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._connection_count < count and self._fatal_error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._state is SerialWriterState.STOPPED:
                    return False
                self._condition.wait(remaining)
            return self._connection_count >= count

    def _validate_and_cache(self, update: FrameUpdate) -> _QueuedFrame:
        if update.width != self._display_width or update.height != self._display_height:
            raise ProtocolValidationError(
                f"frame must be {self._display_width}x{self._display_height}"
            )
        if isinstance(update.sequence, bool) or not isinstance(update.sequence, int):
            raise ProtocolValidationError("frame sequence must be an integer")
        if update.sequence < 0:
            raise ProtocolValidationError("frame sequence must be non-negative")
        if not update.regions:
            raise ProtocolValidationError("frame update must contain at least one region")

        normalized_regions = tuple(self._normalize_region(region) for region in update.regions)
        if update.full_refresh:
            if len(normalized_regions) != 1:
                raise ProtocolValidationError(
                    "full refresh must contain exactly one full-screen region"
                )
            only = normalized_regions[0]
            if (
                only.x != 0
                or only.y != 0
                or only.width != self._display_width
                or only.height != self._display_height
            ):
                raise ProtocolValidationError(
                    "full refresh region must be exactly "
                    f"0,0,{self._display_width},{self._display_height}"
                )
        else:
            for region in normalized_regions:
                if not any(
                    region.x >= card.x
                    and region.y >= card.y
                    and region.x + region.width <= card.right
                    and region.y + region.height <= card.bottom
                    for card in self._layout.card_rects.values()
                ):
                    raise ProtocolValidationError(
                        "partial updates must stay inside one dashboard card"
                    )

        with self._cache_lock:
            if update.sequence <= self._latest_sequence:
                raise ProtocolValidationError(
                    f"frame sequence {update.sequence} is not newer than "
                    f"{self._latest_sequence}"
                )
            if not update.full_refresh and not self._framebuffer_valid:
                raise ProtocolValidationError(
                    "partial update requires an earlier full framebuffer"
                )

            if update.full_refresh:
                self._framebuffer[:] = normalized_regions[0].rgb
                self._framebuffer_valid = True
            else:
                for region in normalized_regions:
                    self._apply_region(region)
            self._latest_sequence = update.sequence
            framebuffer = bytes(self._framebuffer)

        return _QueuedFrame(
            update=FrameUpdate(
                sequence=update.sequence,
                width=update.width,
                height=update.height,
                full_refresh=update.full_refresh,
                regions=normalized_regions,
                submitted_at=update.submitted_at,
            ),
            framebuffer=framebuffer,
        )

    def _normalize_region(self, region: FrameRegion) -> FrameRegion:
        if not isinstance(region, FrameRegion):
            raise ProtocolValidationError("all frame regions must be FrameRegion objects")
        values = (region.x, region.y, region.width, region.height)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ProtocolValidationError("region coordinates and dimensions must be integers")
        if region.x < 0 or region.y < 0 or region.width <= 0 or region.height <= 0:
            raise ProtocolValidationError("region coordinates/dimensions are invalid")
        if (
            region.x + region.width > self._display_width
            or region.y + region.height > self._display_height
        ):
            raise ProtocolValidationError(
                "region exceeds the "
                f"{self._display_width}x{self._display_height} framebuffer"
            )
        if not isinstance(region.rgb, (bytes, bytearray, memoryview)):
            raise ProtocolValidationError("region RGB data must be bytes-like")
        rgb = bytes(region.rgb)
        expected = region.width * region.height * 3
        if len(rgb) != expected:
            raise ProtocolValidationError(
                f"region RGB length must be {expected}, got {len(rgb)}"
            )
        return FrameRegion(
            x=region.x,
            y=region.y,
            width=region.width,
            height=region.height,
            rgb=rgb,
        )

    def _apply_region(self, region: FrameRegion) -> None:
        source_row_bytes = region.width * 3
        for row in range(region.height):
            source_start = row * source_row_bytes
            destination_start = (
                ((region.y + row) * self._display_width + region.x) * 3
            )
            self._framebuffer[
                destination_start : destination_start + source_row_bytes
            ] = region.rgb[source_start : source_start + source_row_bytes]

    def _run(self) -> None:
        port: SerialPort | None = None
        identity: DeviceInfo | None = None
        failure_count = 0
        last_identity_check = 0.0
        self._set_state(
            SerialWriterState.SUSPENDED
            if self._suspend_event.is_set()
            else SerialWriterState.RECONNECTING,
            port=None,
            error=None,
        )
        try:
            while not self._stop_event.is_set():
                if self._suspend_event.is_set():
                    self._reconnect_event.clear()
                    if port is not None:
                        self._close_port(port)
                        port = None
                        identity = None
                    with self._cache_lock:
                        self._sent_framebuffer_valid = False
                    self._set_state(
                        SerialWriterState.SUSPENDED,
                        port=None,
                        error=None,
                    )
                    self._control_wake_event.clear()
                    while (
                        self._suspend_event.is_set()
                        and not self._stop_event.is_set()
                    ):
                        self._control_wake_event.wait(self._poll_interval)
                        self._control_wake_event.clear()
                    if self._stop_event.is_set():
                        break
                    failure_count = 0
                    self._set_state(
                        SerialWriterState.RECONNECTING,
                        port=None,
                        error=None,
                    )
                    continue

                if port is None:
                    try:
                        port, identity = self._connect_and_restore()
                        last_identity_check = time.monotonic()
                        failure_count = 0
                    except _StopRequested:
                        break
                    except _SuspendRequested:
                        continue
                    except _ReconnectRequested:
                        self._reconnect_event.clear()
                        continue
                    except (DeviceSelectionError, serial.SerialException, OSError) as exc:
                        failure_count += 1
                        error_text = str(exc)
                        self._set_state(
                            SerialWriterState.RECONNECTING,
                            port=self._last_attempted_port,
                            error=error_text,
                        )
                        if is_port_in_use_error(error_text):
                            # Another process owns the COM handle. Reopening in
                            # a loop cannot fix that condition and used to create
                            # a warning storm. Wait for one explicit UI retry.
                            if self._wait_for_explicit_reconnect():
                                break
                            failure_count = 0
                            continue
                        if self._wait_for_control(
                            self._reconnect_policy.delay_for_failure(failure_count)
                        ):
                            break
                        continue

                if self._reconnect_event.is_set():
                    self._reconnect_event.clear()
                    self._close_port(port)
                    port = None
                    identity = None
                    self._set_state(
                        SerialWriterState.RECONNECTING, port=None, error=None
                    )
                    continue

                brightness_written = False
                try:
                    brightness_written = self._write_desired_brightness(port)
                except _StopRequested:
                    break
                except _SuspendRequested:
                    self._close_port(port)
                    port = None
                    identity = None
                    with self._cache_lock:
                        self._sent_framebuffer_valid = False
                    continue
                except _ReconnectRequested:
                    self._reconnect_event.clear()
                    self._close_port(port)
                    port = None
                    identity = None
                    self._set_state(
                        SerialWriterState.RECONNECTING, port=None, error=None
                    )
                    continue
                except (serial.SerialException, OSError) as exc:
                    self._close_port(port)
                    port = None
                    identity = None
                    failure_count += 1
                    error_text = str(exc)
                    self._set_state(
                        SerialWriterState.RECONNECTING,
                        port=self._last_attempted_port,
                        error=error_text,
                    )
                    if is_port_in_use_error(error_text):
                        if self._wait_for_explicit_reconnect():
                            break
                        failure_count = 0
                        continue
                    if self._wait_for_control(
                        self._reconnect_policy.delay_for_failure(failure_count)
                    ):
                        break
                    continue

                try:
                    # After a six-byte control write, take a frame immediately
                    # if one is waiting. This prevents a continuously moving
                    # slider from starving the independent latest-wins frame.
                    queued = self._queue.get(
                        timeout=0.0 if brightness_written else self._poll_interval
                    )
                except QueueClosed:
                    break
                if queued is None:
                    now = time.monotonic()
                    if (
                        identity is not None
                        and now - last_identity_check >= self._identity_check_interval
                    ):
                        last_identity_check = now
                        try:
                            self._detector.revalidate(identity)
                        except DeviceSelectionError as exc:
                            self._close_port(port)
                            port = None
                            identity = None
                            failure_count += 1
                            self._set_state(
                                SerialWriterState.RECONNECTING,
                                port=None,
                                error=str(exc),
                            )
                    continue
                update = queued.update
                with self._condition:
                    if update.sequence <= self._last_sent_sequence:
                        continue
                try:
                    region_count, was_full_refresh = self._send_queued_frame(port, queued)
                except _StopRequested:
                    break
                except _SuspendRequested:
                    self._close_port(port)
                    port = None
                    identity = None
                    with self._cache_lock:
                        self._sent_framebuffer_valid = False
                    continue
                except _ReconnectRequested:
                    self._reconnect_event.clear()
                    self._close_port(port)
                    port = None
                    identity = None
                    self._set_state(
                        SerialWriterState.RECONNECTING, port=None, error=None
                    )
                    continue
                except (serial.SerialException, OSError) as exc:
                    # Never retry an interrupted header or payload on this
                    # connection.  Reconnect and restore a fresh full frame.
                    self._close_port(port)
                    port = None
                    identity = None
                    failure_count += 1
                    error_text = str(exc)
                    self._set_state(
                        SerialWriterState.RECONNECTING,
                        port=self._last_attempted_port,
                        error=error_text,
                    )
                    if is_port_in_use_error(error_text):
                        if self._wait_for_explicit_reconnect():
                            break
                        failure_count = 0
                        continue
                    if self._wait_for_control(
                        self._reconnect_policy.delay_for_failure(failure_count)
                    ):
                        break
                    continue

                failure_count = 0
                self._mark_sequence_sent(
                    update.sequence,
                    region_count=region_count,
                    full_refresh=was_full_refresh,
                )
        except BaseException as exc:
            with self._condition:
                self._fatal_error = exc
                self._last_error = str(exc)
                self._condition.notify_all()
        finally:
            if port is not None:
                self._close_port(port)
            self._set_state(
                SerialWriterState.STOPPED,
                port=None,
                error=self.last_error if self._fatal_error is not None else None,
            )

    def _begin_brightness_write(
        self,
        *,
        force: bool,
    ) -> _BrightnessRequest | None:
        with self._brightness_lock:
            if not force and not self._brightness_pending:
                return None
            if self._brightness_inflight is not None:
                raise RuntimeError("brightness write already in flight")
            request = _BrightnessRequest(
                self._brightness_revision,
                self._desired_brightness,
            )
            self._brightness_inflight = request
            self._brightness_pending = False
            return request

    def _finish_brightness_write(self, request: _BrightnessRequest) -> None:
        with self._brightness_lock:
            if self._brightness_inflight != request:
                raise RuntimeError("brightness write completion is out of order")
            self._applied_brightness = request.percent
            self._brightness_inflight = None
            self._brightness_pending = (
                self._desired_brightness != self._applied_brightness
            )

    def _abort_brightness_write(self, request: _BrightnessRequest) -> None:
        with self._brightness_lock:
            if self._brightness_inflight == request:
                self._brightness_inflight = None
                self._brightness_pending = (
                    self._desired_brightness != self._applied_brightness
                )

    def _write_desired_brightness(
        self,
        port: SerialPort,
        *,
        force: bool = False,
    ) -> bool:
        """Write one owner-thread brightness command between frame transfers."""

        request = self._begin_brightness_write(force=force)
        if request is None:
            return False
        try:
            self._write_exact(port, build_brightness_command(request.percent))
        except BaseException:
            self._abort_brightness_write(request)
            raise
        self._finish_brightness_write(request)
        return True

    def _connect_and_restore(self) -> tuple[SerialPort, DeviceInfo]:
        self._check_control_events()
        with self._condition:
            self._last_attempted_port = None
        selected = self._detector.select(self._manual_port)
        with self._condition:
            self._last_attempted_port = selected.device
        # Enumeration may block in the OS. Re-check start/stop admission
        # immediately before the first operation that can open COM.
        self._check_control_events()
        # Linearize the final cancellation check with COM-handle creation.
        # cancel_pending_start()/stop() use the same lock, so if either call
        # returns before this block is admitted, the serial factory is never
        # invoked. If creation was admitted first, cancellation waits until the
        # finite-timeout factory call returns and then revokes the connection.
        with self._lifecycle_lock:
            self._check_control_events()
            port = self._serial_factory(
                port=selected.device,
                baudrate=SERIAL_BAUDRATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=SERIAL_READ_TIMEOUT_SECONDS,
                write_timeout=SERIAL_WRITE_TIMEOUT_SECONDS,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            try:
                self._check_control_events()
                # Vendor Run uses Handshake.None with DTR/RTS asserted.
                # pyserial defaults both line states to True before opening a
                # named port; reassert them so custom factories are equivalent
                # and the requirement remains observable in fake-only tests.
                port.dtr = True
                port.rts = True
            except BaseException:
                self._close_port(port)
                raise
        try:
            self._check_control_events()
            # A manual COM selection is still revalidated by exact identity.
            self._detector.revalidate(selected)
            self._write_desired_brightness(port, force=True)
            self._write_exact(port, build_screen_on_command())
            self._settle_with_control(self._screen_on_settle_seconds)
            self._write_exact(port, build_orientation_command(self._orientation))
            self._settle_with_control(self._orientation_settle_seconds)
            # The vendor render worker reapplies the configured brightness
            # after its orientation settle. Snapshot the latest coalesced value
            # again so requests made during startup settle are not lost.
            self._write_desired_brightness(port, force=True)

            with self._cache_lock:
                framebuffer_valid = self._framebuffer_valid
                framebuffer = bytes(self._framebuffer) if framebuffer_valid else b""
                restored_sequence = self._latest_sequence
            if framebuffer_valid:
                transfer = encode_rgb888_transfer(
                    0,
                    0,
                    self._display_width,
                    self._display_height,
                    framebuffer,
                    display_width=self._display_width,
                    display_height=self._display_height,
                )
                self._write_transfer(port, transfer)
                with self._cache_lock:
                    self._sent_framebuffer = framebuffer
                    self._sent_framebuffer_valid = True
                self._mark_sequence_sent(
                    restored_sequence,
                    region_count=1,
                    full_refresh=True,
                )
        except BaseException:
            self._close_port(port)
            raise

        with self._condition:
            self._connection_count += 1
            self._condition.notify_all()
        self._set_state(
            SerialWriterState.ONLINE, port=selected.device, error=None
        )
        return port, selected

    def _send_queued_frame(
        self,
        port: SerialPort,
        queued: _QueuedFrame,
    ) -> tuple[int, bool]:
        regions, full_refresh = self._materialize_regions(queued)
        for region in regions:
            transfer = encode_rgb888_transfer(
                region.x,
                region.y,
                region.width,
                region.height,
                region.rgb,
                display_width=self._display_width,
                display_height=self._display_height,
            )
            self._write_transfer(port, transfer)
        with self._cache_lock:
            self._sent_framebuffer = queued.framebuffer
            self._sent_framebuffer_valid = True
        return len(regions), full_refresh

    def _materialize_regions(
        self,
        queued: _QueuedFrame,
    ) -> tuple[tuple[FrameRegion, ...], bool]:
        if queued.update.full_refresh:
            return (
                FrameRegion(
                    0,
                    0,
                    self._display_width,
                    self._display_height,
                    queued.framebuffer,
                ),
            ), True
        with self._cache_lock:
            sent_valid = self._sent_framebuffer_valid
            sent = self._sent_framebuffer
        if not sent_valid:
            return (
                FrameRegion(
                    0,
                    0,
                    self._display_width,
                    self._display_height,
                    queued.framebuffer,
                ),
            ), True
        before = Image.frombytes(
            "RGB", (self._display_width, self._display_height), sent
        )
        target = Image.frombytes(
            "RGB",
            (self._display_width, self._display_height),
            queued.framebuffer,
        )
        rectangles = calculate_dirty_rectangles(
            before, target, layout=self._layout
        )
        return tuple(
            FrameRegion(
                rect.x,
                rect.y,
                rect.width,
                rect.height,
                target.crop(rect.as_box()).tobytes(),
            )
            for rect in rectangles
        ), False

    def _write_transfer(self, port: SerialPort, transfer: Any) -> None:
        for block in transfer.iter_writes(self._payload_chunk_size):
            self._write_exact(port, block)

    def _write_exact(self, port: SerialPort, data: bytes) -> None:
        self._check_control_events()
        written = port.write(data)
        if written != len(data):
            raise SerialTransferError(
                f"short serial write: expected {len(data)} bytes, wrote {written!r}"
            )

    def _settle_with_control(self, seconds: float) -> None:
        """Honor the audited panel settle interval without delaying controls."""

        deadline = time.monotonic() + seconds
        while True:
            self._check_control_events()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._control_wake_event.wait(remaining)
            self._control_wake_event.clear()

    def _check_control_events(self) -> None:
        if self._stop_event.is_set():
            raise _StopRequested
        if self._suspend_event.is_set():
            raise _SuspendRequested
        if self._reconnect_event.is_set():
            raise _ReconnectRequested

    def _wait_for_control(self, timeout: float) -> bool:
        """Wait for retry time while allowing power/stop controls to wake it."""

        if timeout > 0:
            self._control_wake_event.wait(timeout)
        self._control_wake_event.clear()
        return self._stop_event.is_set()

    def _wait_for_explicit_reconnect(self) -> bool:
        """Pause a non-actionable access-denied retry until the user acts."""

        while not (
            self._stop_event.is_set()
            or self._suspend_event.is_set()
            or self._reconnect_event.is_set()
        ):
            self._control_wake_event.wait()
            self._control_wake_event.clear()
        if self._reconnect_event.is_set():
            self._reconnect_event.clear()
            self._set_state(
                SerialWriterState.RECONNECTING,
                port=self._last_attempted_port,
                error=None,
            )
        return self._stop_event.is_set()

    @staticmethod
    def _close_port(port: SerialPort) -> None:
        try:
            port.close()
        except Exception:
            # The handle is already unusable.  Reconnection must proceed and
            # no write is attempted from this cleanup path.
            pass

    def _mark_sequence_sent(
        self,
        sequence: int,
        *,
        region_count: int,
        full_refresh: bool,
    ) -> None:
        with self._condition:
            if sequence > self._last_sent_sequence:
                self._last_sent_sequence = sequence
            self._sent_update_count += 1
            self._sent_region_count += region_count
            self._sent_full_refresh_count += int(full_refresh)
            self._condition.notify_all()

    def _set_state(
        self,
        state: SerialWriterState,
        *,
        port: str | None,
        error: str | None,
    ) -> None:
        with self._condition:
            changed = (
                state is not self._state
                or port != self._current_port
                or error != self._last_error
            )
            self._state = state
            self._current_port = port
            self._last_error = error
            self._condition.notify_all()
        if changed and self._status_callback is not None:
            try:
                self._status_callback(SerialWriterEvent(state, port, error))
            except Exception:
                # Observability must never take down the serial owner thread.
                pass


def is_port_in_use_error(error: str | None) -> bool:
    """Recognize Windows access-denied variants without exposing raw errors."""

    if not error:
        return False
    lowered = error.casefold()
    return any(
        marker in lowered
        for marker in (
            "permissionerror",
            "permission denied",
            "access is denied",
            "access denied",
            "액세스가 거부",
            "winerror 5",
        )
    )


def _is_device_absent_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.casefold()
    return any(
        marker in lowered
        for marker in (
            "not present",
            "not found",
            "cannot find",
            "찾을 수 없",
        )
    )
