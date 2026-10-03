# SPDX-License-Identifier: GPL-3.0-or-later

"""Confirmed, display-only endurance benchmark for the verified mini display.

This module deliberately stays above the transport protocol.  It submits only
480x320 dashboard frames and dashboard-derived regions through
``SerialWriter``; device discovery and every pre-write identity check remain in
``DeviceDetector`` and ``SerialWriter``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import psutil
from PIL import Image

from .demo import demo_snapshot
from .models import FrameRegion, FrameUpdate
from .rendering.renderer import DashboardRenderer
from .transport.device import DeviceDetector, DeviceIdentityMismatch, DeviceInfo
from .transport.serial_writer import SerialWriter, SerialWriterEvent


DEFAULT_DURATION_SECONDS = 1_800.0
DEFAULT_OUTPUT_PATH = Path("diagnostics/device-benchmark.json")
DEFAULT_UPDATE_INTERVAL_SECONDS = 0.2
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 60.0
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0
BENCHMARK_DEMO_TIME = datetime(2026, 8, 10, 12, 44, 33, tzinfo=timezone.utc)

PATCH_SIZES: tuple[tuple[int, int], ...] = (
    (32, 32),
    (80, 40),
    (160, 40),
    (228, 89),
)


class DeviceOutputConfirmationRequired(PermissionError):
    """Raised before discovery when physical display output was not confirmed."""


class BenchmarkTimeout(TimeoutError):
    """A submitted dashboard update did not complete in the bounded wait."""


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    name: str
    x: int
    y: int
    width: int
    height: int
    full_refresh: bool = False


# All patch rectangles sit within the CPU card.  Their pixels come from a
# freshly rendered dashboard variant, never from arbitrary test patterns.
BENCHMARK_WORKLOADS: tuple[WorkloadSpec, ...] = (
    WorkloadSpec("full_frame_480x320", 0, 0, 480, 320, True),
    WorkloadSpec("patch_32x32", 76, 83, 32, 32),
    WorkloadSpec("patch_80x40", 28, 72, 80, 40),
    WorkloadSpec("patch_160x40", 40, 72, 160, 40),
    WorkloadSpec("patch_228x89", 8, 38, 228, 89),
)
PARTIAL_WORKLOADS: tuple[WorkloadSpec, ...] = tuple(
    spec for spec in BENCHMARK_WORKLOADS if not spec.full_refresh
)


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    duration_seconds: float = DEFAULT_DURATION_SECONDS
    output_path: Path = DEFAULT_OUTPUT_PATH
    manual_port: str | None = None
    confirm_device_output: bool = False
    update_interval_seconds: float = DEFAULT_UPDATE_INTERVAL_SECONDS
    completion_timeout_seconds: float = DEFAULT_COMPLETION_TIMEOUT_SECONDS
    stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("benchmark duration must be a finite positive number")
        if (
            not math.isfinite(self.update_interval_seconds)
            or self.update_interval_seconds < 0
        ):
            raise ValueError("update interval must be a finite non-negative number")
        if (
            not math.isfinite(self.completion_timeout_seconds)
            or self.completion_timeout_seconds <= 0
        ):
            raise ValueError("completion timeout must be a finite positive number")
        if (
            not math.isfinite(self.stop_timeout_seconds)
            or self.stop_timeout_seconds <= 0
        ):
            raise ValueError("stop timeout must be a finite positive number")
        if self.manual_port is not None and not self.manual_port.strip():
            raise ValueError("manual port cannot be empty")
        if not isinstance(self.output_path, Path):
            object.__setattr__(self, "output_path", Path(self.output_path))


class BenchmarkWriter(Protocol):
    pending_count: int
    replaced_count: int
    last_sent_sequence: int
    connection_count: int
    fatal_error: BaseException | None

    def submit(self, update: FrameUpdate) -> None: ...

    def start(self) -> None: ...

    def wait_for_sequence(self, sequence: int, timeout: float = ...) -> bool: ...

    def stop(self, timeout: float = ...) -> bool: ...


WriterFactory = Callable[..., BenchmarkWriter]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
WallClock = Callable[[], datetime]


class _RenderedDashboardFrames:
    """Produce only native-size dashboard frames and changed dashboard crops."""

    _STATES = (
        "zero",
        "hundred",
        "temperature_warning",
        "memory_99",
        "ai_delayed",
        "normal",
    )

    def __init__(self) -> None:
        renderer = DashboardRenderer()
        self.final_dashboard = renderer.render(
            demo_snapshot("normal", now=BENCHMARK_DEMO_TIME)
        ).convert("RGB")
        self._variants = tuple(
            renderer.render(
                demo_snapshot(state, now=BENCHMARK_DEMO_TIME)
            ).convert("RGB")
            for state in self._STATES
        )
        for frame in (self.final_dashboard, *self._variants):
            if frame.mode != "RGB" or frame.size != (480, 320):
                raise ValueError("benchmark renderer must produce RGB 480x320 frames")
        self._current = self.final_dashboard.copy()
        self._variant_cursor = 0

    def final_update(self, sequence: int, submitted_at: float) -> FrameUpdate:
        self._current = self.final_dashboard.copy()
        return _full_frame_update(self._current, sequence, submitted_at)

    def workload_update(
        self,
        spec: WorkloadSpec,
        sequence: int,
        submitted_at: float,
    ) -> FrameUpdate:
        box = (spec.x, spec.y, spec.x + spec.width, spec.y + spec.height)
        before = self._current.crop(box).tobytes()
        selected: Image.Image | None = None
        for offset in range(len(self._variants)):
            index = (self._variant_cursor + offset) % len(self._variants)
            candidate = self._variants[index]
            if candidate.crop(box).tobytes() != before:
                selected = candidate
                self._variant_cursor = (index + 1) % len(self._variants)
                break
        if selected is None:
            raise RuntimeError(f"rendered UI did not change workload {spec.name}")

        if spec.full_refresh:
            self._current = selected.copy()
            return _full_frame_update(self._current, sequence, submitted_at)

        patch = selected.crop(box).convert("RGB")
        self._current.paste(patch, (spec.x, spec.y))
        return FrameUpdate(
            sequence=sequence,
            width=480,
            height=320,
            full_refresh=False,
            regions=(
                FrameRegion(
                    spec.x,
                    spec.y,
                    spec.width,
                    spec.height,
                    patch.tobytes(),
                ),
            ),
            submitted_at=submitted_at,
        )


class _StatusCounter:
    def __init__(self) -> None:
        self.serial_error_count = 0
        self.error_messages: list[str] = []

    def __call__(self, event: SerialWriterEvent) -> None:
        if event.error:
            self.serial_error_count += 1
            self.error_messages.append(event.error)


def _full_frame_update(
    image: Image.Image,
    sequence: int,
    submitted_at: float,
) -> FrameUpdate:
    if image.mode != "RGB" or image.size != (480, 320):
        raise ValueError("full benchmark frame must be RGB 480x320")
    return FrameUpdate(
        sequence=sequence,
        width=480,
        height=320,
        full_refresh=True,
        regions=(FrameRegion(0, 0, 480, 320, image.tobytes()),),
        submitted_at=submitted_at,
    )


def _exception(stage: str, error: BaseException) -> dict[str, str]:
    return {
        "stage": stage,
        "type": type(error).__name__,
        "message": str(error),
    }


def _percentile(values: list[float], proportion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * proportion) - 1))
    return ordered[index]


def _safe_number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("process metric is not finite")
    return number


def _process_sample(process: Any, elapsed_seconds: float) -> dict[str, float | int]:
    memory = process.memory_info()
    rss = int(memory.rss)
    if rss < 0:
        raise ValueError("process RSS cannot be negative")
    return {
        "elapsed_seconds": max(0.0, float(elapsed_seconds)),
        "cpu_percent": _safe_number(process.cpu_percent(interval=None)),
        "rss_bytes": rss,
    }


def _verified_identity(detector: DeviceDetector, manual_port: str | None) -> DeviceInfo:
    selected = detector.select(manual_port)
    if not selected.is_exact_target:
        raise DeviceIdentityMismatch("detector returned a non-target device")
    verified = detector.revalidate(selected)
    if not verified.is_exact_target or not selected.same_identity(verified):
        raise DeviceIdentityMismatch("device identity changed during benchmark preflight")
    return verified


def _workload_results(
    specs: tuple[WorkloadSpec, ...],
) -> dict[str, dict[str, Any]]:
    return {
        spec.name: {
            "width": spec.width,
            "height": spec.height,
            "full_refresh": spec.full_refresh,
            "submitted_count": 0,
            "completed_count": 0,
            "elapsed_times_ms": [],
        }
        for spec in specs
    }


def run_device_benchmark(
    options: BenchmarkOptions,
    *,
    detector: DeviceDetector | None = None,
    writer_factory: WriterFactory | None = None,
    process: Any | None = None,
    monotonic: Clock = time.monotonic,
    sleep: Sleeper = time.sleep,
    wall_now: WallClock = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Run and atomically write a confirmed physical-display benchmark.

    Supplying ``confirm_device_output=True`` is mandatory even for direct API
    callers.  Tests should inject a fake detector, writer factory, and process;
    the production defaults are the only path that can enumerate or open COM.
    """

    if not options.confirm_device_output:
        raise DeviceOutputConfirmationRequired(
            "physical display output requires --confirm-device-output"
        )

    total_started = monotonic()
    started_utc = wall_now().astimezone(timezone.utc)
    detector = detector or DeviceDetector()
    writer_factory = writer_factory or SerialWriter
    process = process or psutil.Process(os.getpid())
    frames = _RenderedDashboardFrames()
    status = _StatusCounter()
    calibration_workloads = _workload_results(BENCHMARK_WORKLOADS)
    endurance_workloads = _workload_results(PARTIAL_WORKLOADS)
    exceptions: list[dict[str, str]] = []
    process_samples: list[dict[str, float | int]] = []
    calibration_samples: list[dict[str, Any]] = []
    endurance_samples: list[dict[str, Any]] = []

    writer: BenchmarkWriter | None = None
    writer_started = False
    display_initialized = False
    final_dashboard_restored = False
    writer_closed = False
    identity: DeviceInfo | None = None
    sequence = 0
    max_pending = 0
    calibration_full_frame_submissions = 0
    endurance_full_frame_submissions = 0
    cleanup_full_frame_submissions = 0
    final_restore_latency_ms: float | None = None
    calibration_started: float | None = None
    calibration_finished: float | None = None
    endurance_started: float | None = None
    endurance_finished: float | None = None
    interrupted = False
    stage = "process_baseline"

    try:
        # Prime psutil's non-blocking CPU percentage and capture initial RSS.
        process.cpu_percent(interval=None)
        process_samples.append(_process_sample(process, monotonic() - total_started))

        stage = "device_identity_preflight"
        identity = _verified_identity(detector, options.manual_port)

        stage = "writer_initialization"
        writer = writer_factory(
            detector=detector,
            manual_port=options.manual_port,
            status_callback=status,
        )

        # The calibration's single full-frame sample is also the framebuffer
        # required before SerialWriter may open the port.  This avoids a hidden
        # extra warmup transfer.
        stage = "calibration"
        calibration_started = monotonic()
        next_submission = calibration_started
        for calibration_index, spec in enumerate(BENCHMARK_WORKLOADS):
            now = monotonic()
            if options.update_interval_seconds > 0 and now < next_submission:
                sleep(next_submission - now)
            sequence += 1
            update = frames.workload_update(spec, sequence, 0.0)
            submitted = monotonic()
            update = replace(update, submitted_at=submitted)
            if update.full_refresh != (calibration_index == 0):
                raise RuntimeError(
                    "calibration must contain one leading full frame and four patches"
                )
            workload = calibration_workloads[spec.name]
            workload["submitted_count"] += 1
            writer.submit(update)
            if update.full_refresh:
                calibration_full_frame_submissions += 1
            max_pending = max(max_pending, int(writer.pending_count))
            if calibration_index == 0:
                writer.start()
                writer_started = True
            completed = writer.wait_for_sequence(
                sequence, timeout=options.completion_timeout_seconds
            )
            completed_at = monotonic()
            elapsed_ms = (completed_at - submitted) * 1_000.0
            sample = {
                "phase": "calibration",
                "sequence": sequence,
                "workload": spec.name,
                "width": spec.width,
                "height": spec.height,
                "full_refresh": update.full_refresh,
                "elapsed_ms": elapsed_ms,
                "completed": bool(completed),
            }
            calibration_samples.append(sample)
            if not completed:
                raise BenchmarkTimeout(
                    f"calibration workload {spec.name} sequence {sequence} timed out"
                )
            if calibration_index == 0:
                display_initialized = True
            workload["completed_count"] += 1
            workload["elapsed_times_ms"].append(elapsed_ms)
            process_samples.append(
                _process_sample(process, completed_at - total_started)
            )
            next_submission = submitted + options.update_interval_seconds
        calibration_finished = monotonic()

        # Endurance timing starts only after calibration.  Its scheduling set
        # excludes the full-frame workload by construction and is checked again
        # before every submit.
        stage = "endurance"
        endurance_started = monotonic()
        deadline = endurance_started + options.duration_seconds
        endurance_index = 0
        next_submission = endurance_started
        while endurance_index < len(PARTIAL_WORKLOADS) or monotonic() < deadline:
            now = monotonic()
            if options.update_interval_seconds > 0 and now < next_submission:
                sleep(next_submission - now)
            if endurance_index >= len(PARTIAL_WORKLOADS) and monotonic() >= deadline:
                break

            spec = PARTIAL_WORKLOADS[endurance_index % len(PARTIAL_WORKLOADS)]
            sequence += 1
            update = frames.workload_update(spec, sequence, 0.0)
            submitted = monotonic()
            update = replace(update, submitted_at=submitted)
            if update.full_refresh:
                raise RuntimeError("full-frame updates are forbidden during endurance")
            workload = endurance_workloads[spec.name]
            workload["submitted_count"] += 1
            writer.submit(update)
            if update.full_refresh:
                endurance_full_frame_submissions += 1
            max_pending = max(max_pending, int(writer.pending_count))
            completed = writer.wait_for_sequence(
                sequence, timeout=options.completion_timeout_seconds
            )
            completed_at = monotonic()
            elapsed_ms = (completed_at - submitted) * 1_000.0
            sample = {
                "phase": "endurance",
                "sequence": sequence,
                "workload": spec.name,
                "width": spec.width,
                "height": spec.height,
                "full_refresh": update.full_refresh,
                "elapsed_ms": elapsed_ms,
                "completed": bool(completed),
            }
            endurance_samples.append(sample)
            if not completed:
                raise BenchmarkTimeout(
                    f"endurance workload {spec.name} sequence {sequence} timed out"
                )
            workload["completed_count"] += 1
            workload["elapsed_times_ms"].append(elapsed_ms)
            process_samples.append(
                _process_sample(process, completed_at - total_started)
            )
            endurance_index += 1
            next_submission = submitted + options.update_interval_seconds
        endurance_finished = monotonic()
    except KeyboardInterrupt as error:
        interrupted = True
        exceptions.append(_exception(stage, error))
        if stage == "calibration":
            calibration_finished = monotonic()
        elif stage == "endurance":
            endurance_finished = monotonic()
    except Exception as error:
        exceptions.append(_exception(stage, error))
        if stage == "calibration":
            calibration_finished = monotonic()
        elif stage == "endurance":
            endurance_finished = monotonic()
    finally:
        if writer is not None and writer_started and display_initialized:
            stage = "final_dashboard_restore"
            try:
                sequence += 1
                final_update = frames.final_update(sequence, 0.0)
                submitted = monotonic()
                final_update = replace(final_update, submitted_at=submitted)
                if not final_update.full_refresh:
                    raise RuntimeError("final dashboard restore must be a full frame")
                writer.submit(final_update)
                cleanup_full_frame_submissions += 1
                max_pending = max(max_pending, int(writer.pending_count))
                if not writer.wait_for_sequence(
                    sequence, timeout=options.completion_timeout_seconds
                ):
                    raise BenchmarkTimeout("final dashboard restore did not complete")
                final_restore_latency_ms = (monotonic() - submitted) * 1_000.0
                final_dashboard_restored = True
            except Exception as error:
                exceptions.append(_exception(stage, error))

        if writer is not None:
            stage = "writer_close"
            try:
                writer_closed = bool(writer.stop(timeout=options.stop_timeout_seconds))
                if not writer_closed:
                    raise BenchmarkTimeout("serial writer did not stop before timeout")
            except Exception as error:
                exceptions.append(_exception(stage, error))

        try:
            process_samples.append(
                _process_sample(process, monotonic() - total_started)
            )
        except Exception as error:
            exceptions.append(_exception("process_final", error))

    def phase_elapsed(start: float | None, finish: float | None) -> float:
        if start is None:
            return 0.0
        return max(0.0, (finish if finish is not None else monotonic()) - start)

    calibration_elapsed = phase_elapsed(calibration_started, calibration_finished)
    endurance_elapsed = phase_elapsed(endurance_started, endurance_finished)
    total_elapsed = max(0.0, monotonic() - total_started)
    calibration_latencies = [
        float(sample["elapsed_ms"])
        for sample in calibration_samples
        if sample["completed"]
    ]
    endurance_latencies = [
        float(sample["elapsed_ms"])
        for sample in endurance_samples
        if sample["completed"]
    ]
    completed_latencies = calibration_latencies + endurance_latencies
    calibration_submitted = sum(
        int(workload["submitted_count"])
        for workload in calibration_workloads.values()
    )
    calibration_completed = sum(
        int(workload["completed_count"])
        for workload in calibration_workloads.values()
    )
    endurance_submitted = sum(
        int(workload["submitted_count"])
        for workload in endurance_workloads.values()
    )
    endurance_completed = sum(
        int(workload["completed_count"])
        for workload in endurance_workloads.values()
    )
    updates_submitted = calibration_submitted + endurance_submitted
    updates_completed = calibration_completed + endurance_completed
    calibration_complete = all(
        int(workload["submitted_count"]) == 1
        and int(workload["completed_count"]) == 1
        for workload in calibration_workloads.values()
    )
    endurance_partial_only = bool(endurance_samples) and all(
        not bool(sample["full_refresh"]) for sample in endurance_samples
    )
    partial_update_rate_hz = (
        endurance_completed / endurance_elapsed if endurance_elapsed > 0 else 0.0
    )
    rss_values = [int(sample["rss_bytes"]) for sample in process_samples]
    cpu_values = [float(sample["cpu_percent"]) for sample in process_samples]
    rss_start = rss_values[0] if rss_values else None
    rss_end = rss_values[-1] if rss_values else None

    connection_count = int(getattr(writer, "connection_count", 0)) if writer else 0
    reconnect_count = max(0, connection_count - 1)
    fatal_error = getattr(writer, "fatal_error", None) if writer else None
    if fatal_error is not None:
        exceptions.append(_exception("writer_fatal", fatal_error))

    calibration_summary = {
        "elapsed_seconds": calibration_elapsed,
        "submitted_count": calibration_submitted,
        "completed_count": calibration_completed,
        "full_frame_submissions": calibration_full_frame_submissions,
        "workloads": calibration_workloads,
        "samples": calibration_samples,
        "queue_latency_samples_ms": calibration_latencies,
        "queue_latency_mean_ms": (
            statistics.fmean(calibration_latencies)
            if calibration_latencies
            else None
        ),
        "queue_latency_p95_ms": _percentile(calibration_latencies, 0.95),
        "complete": calibration_complete,
    }
    endurance_summary = {
        "requested_duration_seconds": options.duration_seconds,
        "completion_semantics": (
            "host submit-to-write-return latency; the display protocol has no "
            "LCD acknowledgement"
        ),
        "elapsed_seconds": endurance_elapsed,
        "submitted_count": endurance_submitted,
        "completed_count": endurance_completed,
        "full_frame_submissions": endurance_full_frame_submissions,
        "partial_update_rate_hz": partial_update_rate_hz,
        "workloads": endurance_workloads,
        "samples": endurance_samples,
        "queue_latency_samples_ms": endurance_latencies,
        "queue_latency_mean_ms": (
            statistics.fmean(endurance_latencies) if endurance_latencies else None
        ),
        "queue_latency_p95_ms": _percentile(endurance_latencies, 0.95),
        "partial_updates_only": endurance_partial_only,
    }

    finished_utc = wall_now().astimezone(timezone.utc)
    result: dict[str, Any] = {
        "schema_version": 2,
        "success": False,
        "interrupted": interrupted,
        "started_at_utc": started_utc.isoformat(),
        "finished_at_utc": finished_utc.isoformat(),
        "requested_duration_seconds": options.duration_seconds,
        "calibration_elapsed_seconds": calibration_elapsed,
        "endurance_elapsed_seconds": endurance_elapsed,
        "benchmark_elapsed_seconds": calibration_elapsed + endurance_elapsed,
        "total_elapsed_seconds": total_elapsed,
        "output_path": str(options.output_path.resolve()),
        "identity_verified": identity is not None,
        "device": None
        if identity is None
        else {
            "port": identity.device,
            "vid": identity.vid,
            "pid": identity.pid,
            "serial_number": identity.serial_number,
        },
        "calibration": calibration_summary,
        "endurance": endurance_summary,
        "update_samples": calibration_samples + endurance_samples,
        "queue_latency_samples_ms": completed_latencies,
        "queue_latency_mean_ms": (
            statistics.fmean(completed_latencies) if completed_latencies else None
        ),
        "queue_latency_p95_ms": _percentile(completed_latencies, 0.95),
        "updates_submitted": updates_submitted,
        "updates_completed": updates_completed,
        "update_rate_hz": (
            updates_completed / (calibration_elapsed + endurance_elapsed)
            if calibration_elapsed + endurance_elapsed > 0
            else 0.0
        ),
        "partial_update_rate_hz": partial_update_rate_hz,
        "full_frame_submissions": (
            calibration_full_frame_submissions
            + endurance_full_frame_submissions
            + cleanup_full_frame_submissions
        ),
        "measurement_full_frame_submissions": (
            calibration_full_frame_submissions + endurance_full_frame_submissions
        ),
        "calibration_full_frame_submissions": calibration_full_frame_submissions,
        "endurance_full_frame_submissions": endurance_full_frame_submissions,
        "cleanup_full_frame_submissions": cleanup_full_frame_submissions,
        "final_restore_latency_ms": final_restore_latency_ms,
        "process_samples": process_samples,
        "process_cpu_percent_mean": (
            statistics.fmean(cpu_values) if cpu_values else None
        ),
        "process_cpu_percent_peak": max(cpu_values) if cpu_values else None,
        "rss_start_bytes": rss_start,
        "rss_end_bytes": rss_end,
        "rss_peak_bytes": max(rss_values) if rss_values else None,
        "memory_growth_bytes": (
            rss_end - rss_start
            if rss_start is not None and rss_end is not None
            else None
        ),
        "serial_error_count": status.serial_error_count,
        "serial_error_messages": status.error_messages,
        "max_pending": max_pending,
        "queue_replaced_count": (
            int(getattr(writer, "replaced_count", 0)) if writer else 0
        ),
        "connection_count": connection_count,
        "reconnect_count": reconnect_count,
        "required_workloads_completed": calibration_complete,
        "endurance_partial_updates_only": endurance_partial_only,
        "final_dashboard_restored": final_dashboard_restored,
        "writer_closed": writer_closed,
        "exceptions": exceptions,
        "exception_count": len(exceptions),
    }
    result["success"] = bool(
        identity is not None
        and calibration_complete
        and endurance_partial_only
        and final_dashboard_restored
        and writer_closed
        and endurance_elapsed >= options.duration_seconds * 0.99
        and max_pending <= 1
        and calibration_full_frame_submissions == 1
        and endurance_full_frame_submissions == 0
        and cleanup_full_frame_submissions == 1
        and status.serial_error_count == 0
        and reconnect_count == 0
        and not exceptions
        and not interrupted
    )
    _write_json_atomic(options.output_path, result)
    return result


def _write_json_atomic(path: Path, result: dict[str, Any]) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle: Any | None = None
    temporary_name: str | None = None
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        )
        temporary_name = handle.name
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if handle is not None:
            handle.close()
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ai_mini_monitor.benchmark",
        description=(
            "Benchmark rendered dashboard updates on one exactly verified "
            "USB35INCHIPSV2 display."
        ),
    )
    parser.add_argument(
        "--duration",
        "--duration-seconds",
        dest="duration_seconds",
        type=_positive_float,
        default=DEFAULT_DURATION_SECONDS,
        help="benchmark duration in seconds (default: 1800)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="JSON result path",
    )
    parser.add_argument(
        "--port",
        dest="manual_port",
        help="optional COM name; VID, PID, and serial are still mandatory",
    )
    parser.add_argument(
        "--confirm-device-output",
        action="store_true",
        required=True,
        help="explicitly allow changing the verified physical display",
    )
    return parser


def _positive_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = BenchmarkOptions(
        duration_seconds=args.duration_seconds,
        output_path=args.output,
        manual_port=args.manual_port,
        confirm_device_output=args.confirm_device_output,
    )
    result = run_device_benchmark(options)
    print(json.dumps({
        "success": result["success"],
        "output": result["output_path"],
        "calibration_complete": result["calibration"]["complete"],
        "calibration_elapsed_seconds": result["calibration_elapsed_seconds"],
        "endurance_elapsed_seconds": result["endurance_elapsed_seconds"],
        "endurance_updates_completed": result["endurance"]["completed_count"],
        "partial_update_rate_hz": result["partial_update_rate_hz"],
        "full_frame_submissions": result["full_frame_submissions"],
        "serial_error_count": result["serial_error_count"],
        "reconnect_count": result["reconnect_count"],
    }, ensure_ascii=False, indent=2))
    if result["interrupted"]:
        return 130
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
