from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Callable

from PIL import Image

from .ai.activity import ChatGPTActivityProvider
from .ai.codex_account import CodexAccountSnapshot
from .ai.codex_usage import (
    CodexUsageProvider,
    CodexUsageSnapshot,
    CodexUsageStatus,
    to_ai_data as codex_to_ai_data,
)
from .ai.openai_usage import OpenAIUsageClient, OpenAIUsageProvider
from .config import AppConfig, validate_brightness
from .models import (
    AIData,
    AIProviderKind,
    ConnectionData,
    ConnectionStatus,
    FrameRegion,
    FrameUpdate,
    SensorSnapshot,
    SyncStatus,
)
from .orientation import orientation_spec
from .rendering.dirty import calculate_dirty_rectangles
from .rendering.layout import Rect, layout_for_dimensions
from .rendering.renderer import DashboardRenderer
from .security.dpapi import DPAPISecretStore
from .sensors.collector import SystemSensorCollector
from .state import DisplayComposer, RuntimeStateStore, not_configured_ai
from .transport.serial_writer import (
    InitialRestoreStatus,
    SerialWriter,
    SerialWriterEvent,
    SerialWriterState,
    is_port_in_use_error,
)


LOGGER = logging.getLogger(__name__)
INITIAL_RESTORE_TIMEOUT_SECONDS = 15.0


class MonitorStartError(RuntimeError):
    """A sanitized failure from the bounded first-frame startup handshake."""

    _SAFE_REASONS = frozenset(
        {
            InitialRestoreStatus.PORT_IN_USE.value,
            InitialRestoreStatus.DEVICE_ABSENT.value,
            InitialRestoreStatus.LINK_ERROR.value,
            InitialRestoreStatus.TIMEOUT.value,
            InitialRestoreStatus.STOPPED.value,
        }
    )

    def __init__(self, reason: str) -> None:
        safe_reason = reason if reason in self._SAFE_REASONS else "link_error"
        self.reason = safe_reason
        super().__init__(safe_reason)


@dataclass(slots=True)
class ControllerStats:
    started_at: float = field(default_factory=time.monotonic)
    render_frames: int = 0
    submitted_updates: int = 0
    submitted_regions: int = 0
    skipped_unchanged: int = 0
    full_refresh_submissions: int = 0
    max_pending: int = 0
    serial_error_events: int = 0
    unhandled_worker_errors: int = 0
    sent_updates: int = 0
    sent_regions: int = 0
    sent_full_refreshes: int = 0
    queue_latencies_ms: list[float] = field(default_factory=list)
    sensor_to_serial_latencies_ms: list[float] = field(default_factory=list)

    def snapshot(self) -> dict[str, float | int | None]:
        latencies = sorted(self.queue_latencies_ms)
        sensor_latencies = sorted(self.sensor_to_serial_latencies_ms)
        return {
            "uptime_seconds": max(0.0, time.monotonic() - self.started_at),
            "render_frames": self.render_frames,
            "submitted_updates": self.submitted_updates,
            "submitted_regions": self.submitted_regions,
            "skipped_unchanged": self.skipped_unchanged,
            "full_refresh_submissions": self.full_refresh_submissions,
            "max_pending": self.max_pending,
            "serial_error_events": self.serial_error_events,
            "unhandled_worker_errors": self.unhandled_worker_errors,
            "sent_updates": self.sent_updates,
            "sent_regions": self.sent_regions,
            "sent_full_refreshes": self.sent_full_refreshes,
            "queue_latency_mean_ms": mean(latencies) if latencies else None,
            "queue_latency_p95_ms": _p95(latencies),
            "sensor_to_serial_mean_ms": mean(sensor_latencies) if sensor_latencies else None,
            "sensor_to_serial_p95_ms": _p95(sensor_latencies),
        }


class MonitorController:
    def __init__(
        self,
        config: AppConfig,
        *,
        enable_serial: bool = True,
        secret_store: DPAPISecretStore | None = None,
        image_callback: Callable[[Image.Image], None] | None = None,
        sensor_collector_factory: Callable[[], SystemSensorCollector] | None = None,
        serial_writer: SerialWriter | None = None,
        codex_provider_factory: Callable[[], CodexUsageProvider] | None = None,
        codex_account_snapshot: Callable[[], CodexAccountSnapshot] | None = None,
        initial_restore_timeout: float = INITIAL_RESTORE_TIMEOUT_SECONDS,
    ) -> None:
        config.validate()
        if isinstance(initial_restore_timeout, bool):
            raise ValueError("initial restore timeout must be a positive finite number")
        try:
            restore_timeout = float(initial_restore_timeout)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "initial restore timeout must be a positive finite number"
            ) from error
        if not math.isfinite(restore_timeout) or restore_timeout <= 0:
            raise ValueError("initial restore timeout must be a positive finite number")
        self.config = config
        self.enable_serial = enable_serial
        self.secret_store = secret_store or DPAPISecretStore()
        self.image_callback = image_callback
        self.store = RuntimeStateStore()
        self.orientation = orientation_spec(config.device.rotation)
        self.layout = layout_for_dimensions(*self.orientation.dimensions)
        self.renderer = DashboardRenderer(layout=self.layout)
        self.composer = DisplayComposer(sensor_period_seconds=config.sensors.sample_interval_ms / 1000.0)
        self.stats = ControllerStats()
        self._stats_lock = threading.Lock()
        self._completion_lock = threading.Lock()
        self._latest_lock = threading.Lock()
        self._latest_image: Image.Image | None = None
        self._stop = threading.Event()
        self._ai_wake = threading.Event()
        self._start_cancelled = threading.Event()
        self._power_lock = threading.Lock()
        self._power_suspended = False
        self._power_resume_gap_guard = False
        self._threads: list[threading.Thread] = []
        self._started = False
        self._sequence = 0
        self._submitted_times: dict[int, float] = {}
        self._submitted_sensor_ages_ms: dict[int, float] = {}
        self._submitted_region_counts: dict[int, int] = {}
        self._submitted_full_refresh: dict[int, bool] = {}
        self._desired_frame: Image.Image | None = None
        self._last_observed_sent = -1
        self._sensor_collector_factory = sensor_collector_factory or (
            lambda: SystemSensorCollector(self.config.sensors)
        )
        self._codex_provider_factory = codex_provider_factory or (
            lambda: CodexUsageProvider(
                consent_granted=self.config.ai.codex_local_consent,
            )
        )
        self._codex_account_snapshot = codex_account_snapshot
        self._initial_restore_timeout = restore_timeout

        if enable_serial:
            self.serial = serial_writer or SerialWriter(
                manual_port=config.device.manual_port,
                status_callback=self._on_serial_event,
                orientation=self.orientation.protocol,
                brightness=config.device.brightness,
            )
        else:
            self.serial = None
            self.store.update_connection(
                ConnectionData(ConnectionStatus.DISCONNECTED, detail="PREVIEW ONLY")
            )

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self._started:
            return
        self._ensure_start_allowed()
        self._stop.clear()
        self._ai_wake.clear()
        self._ensure_start_allowed()
        self.stats.started_at = time.monotonic()

        # Establish the exact full framebuffer before the writer can open COM.
        initial = self.renderer.render(self.composer.compose(self.store.read(), monotonic_now=time.monotonic()))
        self._publish_image(initial)
        self._ensure_start_allowed()
        with self._completion_lock:
            self._desired_frame = initial.copy()
        if self.serial is not None:
            self._ensure_start_allowed()
            initial_update = self._frame_update(initial, full_refresh=True)
            self._register_submission(initial_update, sensor_age_ms=None)
            try:
                self.serial.submit(initial_update)
            except Exception:
                self._discard_submission(initial_update.sequence)
                raise
            with self._stats_lock:
                self.stats.submitted_updates += 1
                self.stats.submitted_regions += 1
                self.stats.full_refresh_submissions += 1
            self._ensure_start_allowed()
            self.serial.start()
            self._ensure_start_allowed()
            restored = self.serial.wait_for_initial_restore(
                initial_update.sequence,
                timeout=self._initial_restore_timeout,
            )
            if not restored.ready:
                # Revoke further open/write admission promptly. DesktopSession
                # performs the bounded join before allowing another Start.
                signal_stop = getattr(self.serial, "signal_stop", None)
                if callable(signal_stop):
                    signal_stop()
                raise MonitorStartError(restored.status.value)
            self._ensure_start_allowed()

        self._threads = [
            threading.Thread(target=self._sensor_loop, name="mini-monitor-sensors", daemon=True),
            threading.Thread(target=self._ai_loop, name="mini-monitor-ai", daemon=True),
            threading.Thread(target=self._render_loop, name="mini-monitor-render", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        self._started = True

    def cancel_start(self) -> None:
        """Revoke one-shot start admission without blocking the Tk thread."""

        self._start_cancelled.set()
        self._stop.set()
        self._ai_wake.set()
        if self.serial is not None:
            signal_stop = getattr(self.serial, "signal_stop", None)
            if callable(signal_stop):
                signal_stop()
            else:
                # Compatibility for injected test/third-party writers.
                self.serial.cancel_pending_start()

    def _ensure_start_allowed(self) -> None:
        if self._start_cancelled.is_set():
            raise RuntimeError("controller start was cancelled")

    def stop(self, timeout: float = 20.0) -> bool:
        if timeout <= 0:
            raise ValueError("stop timeout must be positive")
        self._stop.set()
        self._ai_wake.set()
        deadline = time.monotonic() + timeout
        serial_stopped = True
        # Close COM promptly before waiting on a potentially blocked OpenAI
        # request. The remaining budget is still available for worker joins.
        if self.serial is not None:
            serial_stopped = self.serial.stop(timeout=min(5.0, timeout))
        for thread in self._threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        live_threads = [thread for thread in self._threads if thread.is_alive()]
        self._threads = live_threads
        complete = serial_stopped and not live_threads
        self._started = not complete
        return complete

    def request_reconnect(self) -> None:
        if self.serial is not None:
            self.serial.request_reconnect()

    def request_brightness(self, brightness: int) -> bool:
        """Update the one active writer's desired brightness in place."""

        value = validate_brightness(brightness)
        self.config.device.brightness = value
        if self.serial is not None:
            # A False writer result means the same value was already desired;
            # that duplicate is accepted, while a stopped writer whose
            # desired value did not change is reported as unavailable.
            changed = self.serial.request_brightness(value)
            desired = getattr(self.serial, "brightness_percent", value)
            return bool(changed or desired == value)
        return True

    def request_ai_refresh(self) -> None:
        """Wake a configured provider without weakening its consent gate."""

        self._ai_wake.set()

    def suspend_for_power_event(self, timeout: float = 2.0) -> bool:
        """Synchronously pause serial I/O for a Windows suspend broadcast."""

        if timeout <= 0:
            raise ValueError("power suspend timeout must be positive")
        with self._power_lock:
            self._power_suspended = True
        if self.serial is None:
            return True
        return self.serial.suspend(timeout=timeout)

    def resume_from_power_event(self) -> bool:
        """Resume once after any of Windows' duplicate resume broadcasts."""

        with self._power_lock:
            if not self._power_suspended:
                return False
            self._power_suspended = False
            self._power_resume_gap_guard = True
        if self.serial is None:
            return True
        return self.serial.resume()

    def latest_image(self) -> Image.Image | None:
        with self._latest_lock:
            return None if self._latest_image is None else self._latest_image.copy()

    def save_latest(self, path: Path) -> bool:
        image = self.latest_image()
        if image is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG", optimize=True)
        return True

    def stats_snapshot(self) -> dict[str, float | int | None]:
        self._observe_serial_completion()
        with self._stats_lock:
            return self.stats.snapshot()

    def _sensor_loop(self) -> None:
        collector = self._sensor_collector_factory()
        period = self.config.sensors.sample_interval_ms / 1000.0
        try:
            collector.open()
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    self.store.update_sensor(collector.sample())
                except Exception:
                    LOGGER.exception("sensor snapshot failed")
                remaining = max(0.0, period - (time.monotonic() - started))
                if self._stop.wait(remaining):
                    break
        except Exception:
            with self._stats_lock:
                self.stats.unhandled_worker_errors += 1
            LOGGER.exception("sensor worker stopped unexpectedly")
        finally:
            try:
                collector.close()
            except Exception:
                LOGGER.exception("sensor collector close failed")

    def _ai_loop(self) -> None:
        try:
            provider_name = self.config.ai.provider
            if provider_name == AIProviderKind.CODEX_LOCAL.value:
                self._run_codex_provider()
            elif provider_name == AIProviderKind.CODEX_ACCOUNT.value:
                self._run_codex_account()
            elif provider_name == AIProviderKind.OPENAI_API.value:
                self._run_openai_provider()
            elif provider_name == AIProviderKind.CHATGPT_ACTIVITY.value:
                self._run_activity_provider()
            else:
                self.store.update_ai(not_configured_ai())
                self._stop.wait()
        except Exception as error:
            with self._stats_lock:
                self.stats.unhandled_worker_errors += 1
            # Provider exceptions may carry local paths. Record only the class
            # at this privacy boundary.
            LOGGER.error(
                "AI provider worker stopped unexpectedly (%s)",
                type(error).__name__,
            )

    def _run_codex_account(self) -> None:
        """Mirror the app-owned account service; never spawn a second client."""

        snapshot = self._codex_account_snapshot
        if snapshot is None:
            self.store.update_ai(AIData(
                provider=AIProviderKind.CODEX_ACCOUNT,
                title="CODEX",
                status=SyncStatus.SETUP_REQUIRED,
                primary_value="SETUP",
                primary_label="REQUIRED",
            ))
            self._stop.wait()
            return
        while not self._stop.is_set():
            self._ai_wake.clear()
            self.store.update_ai(snapshot().ai)
            self._ai_wake.wait(0.5)

    def _run_codex_provider(self) -> None:
        if not self.config.ai.codex_local_consent:
            self.store.update_ai(
                codex_to_ai_data(
                    CodexUsageSnapshot(CodexUsageStatus.CONSENT_REQUIRED)
                )
            )
            self._stop.wait()
            return

        provider = self._codex_provider_factory()
        interval = float(self.config.ai.usage_refresh_seconds)
        last_fresh_snapshot: CodexUsageSnapshot | None = None
        while not self._stop.is_set():
            self._ai_wake.clear()
            try:
                snapshot = provider.refresh()
            except Exception as error:
                LOGGER.error(
                    "Codex local limit refresh failed (%s)",
                    type(error).__name__,
                )
                snapshot = CodexUsageSnapshot(CodexUsageStatus.UNAVAILABLE)
            if snapshot.status is CodexUsageStatus.OK:
                if (
                    last_fresh_snapshot is not None
                    and last_fresh_snapshot.updated_at is not None
                    and (
                        snapshot.updated_at is None
                        or snapshot.updated_at < last_fresh_snapshot.updated_at
                    )
                ):
                    display = replace(
                        codex_to_ai_data(last_fresh_snapshot),
                        status=SyncStatus.DELAYED,
                        error_detail="older local rate-limit snapshot ignored",
                    )
                else:
                    display = codex_to_ai_data(snapshot)
                    if snapshot.updated_at is not None:
                        last_fresh_snapshot = snapshot
            elif (
                snapshot.status is CodexUsageStatus.STALE
                and last_fresh_snapshot is not None
            ):
                display = replace(
                    codex_to_ai_data(last_fresh_snapshot),
                    status=SyncStatus.DELAYED,
                    error_detail="stale local rate-limit snapshot ignored",
                )
            else:
                # Explicit current errors must remain visible.  Never hide a
                # revoked consent or missing session state behind cached quota.
                display = codex_to_ai_data(snapshot)
                if snapshot.status in {
                    CodexUsageStatus.CONSENT_REQUIRED,
                    CodexUsageStatus.SESSIONS_NOT_FOUND,
                    CodexUsageStatus.NO_RATE_LIMITS,
                }:
                    last_fresh_snapshot = None
            self.store.update_ai(display)
            deadline = time.monotonic() + interval
            while not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._ai_wake.wait(min(0.5, remaining)):
                    break

    def _run_openai_provider(self) -> None:
        try:
            key = self.secret_store.get()
        except Exception:
            LOGGER.exception("could not unlock the OpenAI Admin Key")
            self.store.update_ai(
                AIData(
                    provider=AIProviderKind.OPENAI_API,
                    title="OPENAI API",
                    status=SyncStatus.AUTH_ERROR,
                    primary_value="KEY",
                    primary_label="UNAVAILABLE",
                    fields=(("STATUS", "UNLOCK FAILED"),),
                )
            )
            self._stop.wait()
            return
        if not key:
            self.store.update_ai(
                AIData(
                    provider=AIProviderKind.OPENAI_API,
                    title="OPENAI API",
                    status=SyncStatus.SETUP_REQUIRED,
                    primary_value="SETUP",
                    primary_label="REQUIRED",
                    fields=(("STATUS", "NO ADMIN KEY"),),
                )
            )
            self._stop.wait()
            return
        provider = OpenAIUsageProvider(
            OpenAIUsageClient(key),
            usage_interval=self.config.ai.usage_refresh_seconds,
            cost_interval=self.config.ai.cost_refresh_seconds,
            daily_budget=self.config.ai.daily_budget_usd,
            monthly_budget=self.config.ai.monthly_budget_usd,
        )
        while not self._stop.is_set():
            self._ai_wake.clear()
            self.store.update_ai(provider.refresh())
            self._ai_wake.wait(1.0)

    def _run_activity_provider(self) -> None:
        provider = ChatGPTActivityProvider(self.config.ai.activity_processes)
        while not self._stop.is_set():
            self._ai_wake.clear()
            self.store.update_ai(provider.tick())
            self._ai_wake.wait(1.0)

    def _render_loop(self) -> None:
        frame_period = 1.0 / self.config.app.render_fps
        usb_period = 1.0 / self.config.device.usb_fps
        next_frame = time.monotonic()
        next_usb = next_frame
        last_iteration = next_frame
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now < next_frame:
                    if self._stop.wait(next_frame - now):
                        break
                    now = time.monotonic()
                if self.serial is not None and now - last_iteration > 5.0:
                    # Keep the monotonic fallback for non-desktop callers, but
                    # suppress it when WM_POWERBROADCAST already initiated the
                    # strict resume restore. This prevents two full restores.
                    with self._power_lock:
                        power_suspended = self._power_suspended
                        resume_already_handled = self._power_resume_gap_guard
                        self._power_resume_gap_guard = False
                    if not power_suspended and not resume_already_handled:
                        self.serial.request_reconnect()
                last_iteration = now
                next_frame = max(next_frame + frame_period, now)
                runtime_values = self.store.read()
                display = self.composer.compose(runtime_values, monotonic_now=now)
                image = self.renderer.render(display)
                self._publish_image(image)
                with self._stats_lock:
                    self.stats.render_frames += 1
                if self.serial is not None and now >= next_usb:
                    dirty = self._dirty_from_desired(image)
                    if dirty:
                        update = self._regions_update(image, dirty)
                        sensor_age_ms = _sensor_age_ms(runtime_values.sensor)
                        self._register_submission(update, sensor_age_ms=sensor_age_ms)
                        try:
                            self.serial.submit(update)
                        except RuntimeError:
                            self._discard_submission(update.sequence)
                            if self._stop.is_set():
                                break
                            raise
                        with self._completion_lock:
                            self._desired_frame = image.copy()
                        with self._stats_lock:
                            self.stats.submitted_updates += 1
                            self.stats.submitted_regions += len(update.regions)
                            self.stats.max_pending = max(self.stats.max_pending, self.serial.pending_count)
                    else:
                        with self._stats_lock:
                            self.stats.skipped_unchanged += 1
                    next_usb = now + usb_period
                self._observe_serial_completion()
        except Exception:
            with self._stats_lock:
                self.stats.unhandled_worker_errors += 1
            LOGGER.exception("render worker stopped unexpectedly")
            self._stop.set()

    def _publish_image(self, image: Image.Image) -> None:
        with self._latest_lock:
            self._latest_image = image.copy()
        if self.image_callback is not None:
            try:
                self.image_callback(image.copy())
            except Exception:
                LOGGER.exception("preview image callback failed")

    def _frame_update(self, image: Image.Image, *, full_refresh: bool) -> FrameUpdate:
        if image.size != self.layout.size:
            raise ValueError(
                f"frame must be {self.layout.width}x{self.layout.height}, got {image.size}"
            )
        self._sequence += 1
        width, height = image.size
        region = FrameRegion(0, 0, width, height, image.convert("RGB").tobytes())
        return FrameUpdate(
            sequence=self._sequence,
            width=width,
            height=height,
            full_refresh=full_refresh,
            regions=(region,),
            submitted_at=time.monotonic(),
        )

    def _dirty_from_desired(self, image: Image.Image) -> list[Rect]:
        with self._completion_lock:
            desired = None if self._desired_frame is None else self._desired_frame.copy()
        return calculate_dirty_rectangles(desired, image, layout=self.layout)

    def _regions_update(self, image: Image.Image, rectangles: list[Rect]) -> FrameUpdate:
        if image.size != self.layout.size:
            raise ValueError(
                f"frame must be {self.layout.width}x{self.layout.height}, got {image.size}"
            )
        self._sequence += 1
        regions = tuple(
            FrameRegion(rect.x, rect.y, rect.width, rect.height, image.crop(rect.as_box()).convert("RGB").tobytes())
            for rect in rectangles
        )
        return FrameUpdate(
            sequence=self._sequence,
            width=image.width,
            height=image.height,
            full_refresh=False,
            regions=regions,
            submitted_at=time.monotonic(),
        )

    def _observe_serial_completion(self) -> None:
        if self.serial is None:
            return
        with self._completion_lock:
            sent = self.serial.last_sent_sequence
            actual_update_count = getattr(self.serial, "sent_update_count", None)
            actual_region_count = getattr(self.serial, "sent_region_count", None)
            actual_full_count = getattr(self.serial, "sent_full_refresh_count", None)
            if (
                actual_update_count is not None
                and actual_region_count is not None
                and actual_full_count is not None
            ):
                with self._stats_lock:
                    self.stats.sent_updates = int(actual_update_count)
                    self.stats.sent_regions = int(actual_region_count)
                    self.stats.sent_full_refreshes = int(actual_full_count)
            if sent <= self._last_observed_sent:
                return
            completed = time.monotonic()
            submitted = self._submitted_times.get(sent)
            sensor_age_ms = self._submitted_sensor_ages_ms.get(sent)
            region_count = self._submitted_region_counts.get(sent)
            was_full_refresh = self._submitted_full_refresh.get(sent, False)
            with self._stats_lock:
                if submitted is not None:
                    queue_latency_ms = (completed - submitted) * 1000.0
                    self.stats.queue_latencies_ms.append(queue_latency_ms)
                    if sensor_age_ms is not None:
                        self.stats.sensor_to_serial_latencies_ms.append(
                            sensor_age_ms + queue_latency_ms
                        )
                    if actual_update_count is None:
                        self.stats.sent_updates += 1
                        self.stats.sent_regions += region_count or 0
                        self.stats.sent_full_refreshes += int(was_full_refresh)
            self._last_observed_sent = sent
            self._submitted_times = {
                sequence: timestamp
                for sequence, timestamp in self._submitted_times.items()
                if sequence > sent
            }
            self._submitted_sensor_ages_ms = {
                sequence: age
                for sequence, age in self._submitted_sensor_ages_ms.items()
                if sequence > sent
            }
            self._submitted_region_counts = {
                sequence: count
                for sequence, count in self._submitted_region_counts.items()
                if sequence > sent
            }
            self._submitted_full_refresh = {
                sequence: is_full
                for sequence, is_full in self._submitted_full_refresh.items()
                if sequence > sent
            }

    def _register_submission(
        self,
        update: FrameUpdate,
        *,
        sensor_age_ms: float | None,
    ) -> None:
        with self._completion_lock:
            self._submitted_times[update.sequence] = update.submitted_at
            if sensor_age_ms is not None:
                self._submitted_sensor_ages_ms[update.sequence] = sensor_age_ms
            self._submitted_region_counts[update.sequence] = len(update.regions)
            self._submitted_full_refresh[update.sequence] = update.full_refresh
            # Scalar timing metadata remains bounded even during a long device
            # disconnect. Missing an old completion sample is safe; the newest
            # cumulative frame is always retained for physical-state tracking.
            floor = update.sequence - 256
            self._submitted_times = {
                sequence: timestamp
                for sequence, timestamp in self._submitted_times.items()
                if sequence >= floor
            }
            self._submitted_sensor_ages_ms = {
                sequence: age
                for sequence, age in self._submitted_sensor_ages_ms.items()
                if sequence >= floor
            }
            self._submitted_region_counts = {
                sequence: count
                for sequence, count in self._submitted_region_counts.items()
                if sequence >= floor
            }
            self._submitted_full_refresh = {
                sequence: is_full
                for sequence, is_full in self._submitted_full_refresh.items()
                if sequence >= floor
            }

    def _discard_submission(self, sequence: int) -> None:
        with self._completion_lock:
            self._submitted_times.pop(sequence, None)
            self._submitted_sensor_ages_ms.pop(sequence, None)
            self._submitted_region_counts.pop(sequence, None)
            self._submitted_full_refresh.pop(sequence, None)

    def _on_serial_event(self, event: SerialWriterEvent) -> None:
        if event.error:
            with self._stats_lock:
                self.stats.serial_error_events += 1
            LOGGER.warning("display connection event: %s", event.error)
        if event.state is SerialWriterState.ONLINE:
            connection = ConnectionData(ConnectionStatus.ONLINE, port=event.port)
        elif event.state is SerialWriterState.RECONNECTING:
            detail = _connection_detail(event.error)
            status = (
                ConnectionStatus.DISCONNECTED
                if detail == "PORT IN USE"
                else ConnectionStatus.RECONNECTING
            )
            connection = ConnectionData(status, port=event.port, detail=detail)
        elif event.state is SerialWriterState.SUSPENDED:
            connection = ConnectionData(
                ConnectionStatus.DISCONNECTED,
                detail="SYSTEM SLEEP",
            )
        else:
            connection = ConnectionData(ConnectionStatus.DISCONNECTED, detail=_connection_detail(event.error))
        self.store.update_connection(connection)


def _connection_detail(error: str | None) -> str | None:
    if not error:
        return None
    lowered = error.casefold()
    if is_port_in_use_error(error):
        return "PORT IN USE"
    if "not present" in lowered or "not found" in lowered:
        return "DEVICE ABSENT"
    return "LINK ERROR"


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, math.ceil(len(values) * 0.95) - 1))
    return values[index]


def _sensor_age_ms(sensor: SensorSnapshot | None) -> float | None:
    if sensor is None:
        return None
    try:
        captured = sensor.captured_at
        if captured.tzinfo is None:
            captured = captured.astimezone()
        return max(
            0.0,
            (datetime.now().astimezone() - captured.astimezone()).total_seconds()
            * 1_000.0,
        )
    except (AttributeError, OverflowError, OSError, ValueError):
        return None
