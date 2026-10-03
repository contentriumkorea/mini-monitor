# SPDX-License-Identifier: GPL-3.0-or-later

"""Confirmed end-to-end endurance run using live sensors and the real controller."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil

from .config import AppConfig, load_config
from .controller import MonitorController
from .transport.device import DeviceDetector, DeviceIdentityMismatch, DeviceInfo
from .transport.serial_writer import SerialWriterState


DEFAULT_DURATION_SECONDS = 1_800.0
DEFAULT_OUTPUT_PATH = Path("diagnostics/endurance-30min.json")


class DeviceOutputConfirmationRequired(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class EnduranceOptions:
    duration_seconds: float = DEFAULT_DURATION_SECONDS
    output_path: Path = DEFAULT_OUTPUT_PATH
    config_path: Path | None = None
    manual_port: str | None = None
    confirm_device_output: bool = False
    connection_timeout_seconds: float = 60.0
    sample_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        for name in (
            "duration_seconds",
            "connection_timeout_seconds",
            "sample_interval_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if self.manual_port is not None and not self.manual_port.strip():
            raise ValueError("manual port cannot be empty")


def _verified_identity(detector: DeviceDetector, manual_port: str | None) -> DeviceInfo:
    selected = detector.select(manual_port)
    verified = detector.revalidate(selected)
    if not selected.is_exact_target or not verified.is_exact_target:
        raise DeviceIdentityMismatch("refusing a non-target display")
    if not selected.same_identity(verified):
        raise DeviceIdentityMismatch("display identity changed during preflight")
    return verified


def _process_sample(process: Any, elapsed: float, logical_cpus: int) -> dict[str, float | int]:
    raw_cpu = float(process.cpu_percent(interval=None))
    rss = int(process.memory_info().rss)
    if not math.isfinite(raw_cpu) or rss < 0:
        raise ValueError("invalid process metric")
    return {
        "elapsed_seconds": max(0.0, elapsed),
        "cpu_percent_raw": raw_cpu,
        "cpu_percent_normalized": raw_cpu / max(1, logical_cpus),
        "rss_bytes": rss,
    }


def _rss_slope_bytes_per_minute(samples: list[dict[str, float | int]]) -> float | None:
    if len(samples) < 2:
        return None
    xs = [float(sample["elapsed_seconds"]) / 60.0 for sample in samples]
    ys = [float(sample["rss_bytes"]) for sample in samples]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return None
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_endurance(
    options: EnduranceOptions,
    *,
    detector: DeviceDetector | None = None,
    controller_factory: Callable[[AppConfig], Any] = MonitorController,
    process: Any | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wall_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    if not options.confirm_device_output:
        raise DeviceOutputConfirmationRequired(
            "physical display output requires --confirm-device-output"
        )

    started_utc = wall_now().astimezone(timezone.utc)
    detector = detector or DeviceDetector()
    identity = _verified_identity(detector, options.manual_port)
    config = load_config(options.config_path)
    config.device.manual_port = options.manual_port
    config.validate()
    controller = controller_factory(config)
    process = process or psutil.Process(os.getpid())
    logical_cpus = psutil.cpu_count(logical=True) or 1
    process.cpu_percent(interval=None)

    samples: list[dict[str, float | int]] = []
    exceptions: list[dict[str, str]] = []
    runtime_started: float | None = None
    runtime_finished: float | None = None
    connected = False
    writer_closed = False
    controller_stop_complete = False
    final_frame_path = options.output_path.with_name("endurance-final.png")

    try:
        controller.start()
        connection_deadline = monotonic() + options.connection_timeout_seconds
        while monotonic() < connection_deadline:
            serial = controller.serial
            if serial.state is SerialWriterState.ONLINE:
                connected = True
                break
            if serial.fatal_error is not None:
                raise RuntimeError(f"serial writer failed: {type(serial.fatal_error).__name__}")
            sleep(min(0.1, options.sample_interval_seconds))
        if not connected:
            raise TimeoutError("verified display did not become ONLINE before timeout")

        runtime_started = monotonic()
        deadline = runtime_started + options.duration_seconds
        while monotonic() < deadline:
            now = monotonic()
            samples.append(_process_sample(process, now - runtime_started, logical_cpus))
            controller.stats_snapshot()
            if controller.serial.fatal_error is not None:
                raise RuntimeError(
                    f"serial writer failed: {type(controller.serial.fatal_error).__name__}"
                )
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(options.sample_interval_seconds, remaining))
        runtime_finished = monotonic()
    except KeyboardInterrupt as error:
        runtime_finished = monotonic()
        exceptions.append({"type": type(error).__name__, "message": "interrupted"})
    except Exception as error:
        runtime_finished = monotonic()
        exceptions.append({"type": type(error).__name__, "message": str(error)})
    finally:
        try:
            controller_stop_complete = bool(controller.stop(timeout=20.0))
        except Exception as error:
            exceptions.append({"type": type(error).__name__, "message": f"stop: {error}"})
        try:
            controller.save_latest(final_frame_path)
        except Exception as error:
            exceptions.append({"type": type(error).__name__, "message": f"save frame: {error}"})
        writer_closed = controller.serial.state is SerialWriterState.STOPPED

    stats = controller.stats_snapshot()
    elapsed = (
        max(0.0, (runtime_finished or monotonic()) - runtime_started)
        if runtime_started is not None
        else 0.0
    )
    rss_values = [int(sample["rss_bytes"]) for sample in samples]
    normalized_cpu = [float(sample["cpu_percent_normalized"]) for sample in samples]
    serial = controller.serial
    connection_count = int(serial.connection_count)
    completed_partial_updates = max(
        0, int(stats["sent_updates"]) - int(stats["sent_full_refreshes"])
    )
    completed_partial_regions = max(
        0, int(stats["sent_regions"]) - int(stats["sent_full_refreshes"])
    )
    # LHM/.NET and font initialization occurs just after the port becomes
    # ONLINE. Exclude a bounded warm-up window when evaluating continuous RSS
    # growth, while retaining every raw sample in the evidence file.
    warmup_excluded_seconds = min(60.0, options.duration_seconds * 0.1)
    steady_samples = [
        sample
        for sample in samples
        if float(sample["elapsed_seconds"]) >= warmup_excluded_seconds
    ]
    if len(steady_samples) < 2:
        steady_samples = samples
        warmup_excluded_seconds = 0.0
    steady_rss = [int(sample["rss_bytes"]) for sample in steady_samples]
    slope = _rss_slope_bytes_per_minute(steady_samples)
    memory_growth = (
        steady_rss[-1] - steady_rss[0] if len(steady_rss) >= 2 else None
    )
    memory_growth_suspected = bool(
        slope is not None
        and memory_growth is not None
        and slope > 1_048_576
        and memory_growth > 8 * 1_048_576
    )

    result: dict[str, Any] = {
        "schema_version": 1,
        "success": False,
        "started_at_utc": started_utc.isoformat(),
        "finished_at_utc": wall_now().astimezone(timezone.utc).isoformat(),
        "requested_duration_seconds": options.duration_seconds,
        "elapsed_seconds": elapsed,
        "identity_verified": identity.is_exact_target,
        "device": {
            "port": identity.device,
            "vid": identity.vid,
            "pid": identity.pid,
            "serial_number": identity.serial_number,
        },
        "controller_stats": stats,
        "completed_partial_update_rate_hz": (
            completed_partial_updates / elapsed if elapsed > 0 else 0.0
        ),
        "completed_partial_region_rate_hz": (
            completed_partial_regions / elapsed if elapsed > 0 else 0.0
        ),
        "connection_count": connection_count,
        "reconnect_count": max(0, connection_count - 1),
        "queue_replaced_count": int(serial.replaced_count),
        "writer_closed": writer_closed,
        "controller_stop_complete": controller_stop_complete,
        "process_samples": samples,
        "process_cpu_percent_normalized_mean": (
            statistics.fmean(normalized_cpu) if normalized_cpu else None
        ),
        "process_cpu_percent_normalized_peak": max(normalized_cpu) if normalized_cpu else None,
        "rss_start_bytes": rss_values[0] if rss_values else None,
        "rss_steady_start_bytes": steady_rss[0] if steady_rss else None,
        "rss_end_bytes": rss_values[-1] if rss_values else None,
        "rss_peak_bytes": max(rss_values) if rss_values else None,
        "memory_growth_bytes": memory_growth,
        "memory_warmup_excluded_seconds": warmup_excluded_seconds,
        "rss_slope_bytes_per_minute": slope,
        "memory_growth_suspected": memory_growth_suspected,
        "final_frame_path": str(final_frame_path.resolve()),
        "exceptions": exceptions,
        "exception_count": len(exceptions),
    }
    result["success"] = bool(
        connected
        and elapsed >= options.duration_seconds * 0.99
        and writer_closed
        and controller_stop_complete
        and connection_count == 1
        and int(stats["serial_error_events"]) == 0
        and int(stats["unhandled_worker_errors"]) == 0
        and int(stats["full_refresh_submissions"]) == 1
        and int(stats["max_pending"]) <= 1
        and float(stats["queue_latency_p95_ms"] or math.inf) <= 250.0
        and result["completed_partial_region_rate_hz"] >= 4.0
        and float(result["process_cpu_percent_normalized_mean"] or math.inf) <= 5.0
        and not memory_growth_suspected
        and not exceptions
    )
    _write_json_atomic(options.output_path, result)
    return result


def _positive_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ai_mini_monitor.endurance",
        description="Run the live-sensor USB display endurance test.",
    )
    parser.add_argument("--duration", type=_positive_float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--port")
    parser.add_argument("--confirm-device-output", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_endurance(
        EnduranceOptions(
            duration_seconds=args.duration,
            output_path=args.output,
            config_path=args.config,
            manual_port=args.port,
            confirm_device_output=args.confirm_device_output,
        )
    )
    print(
        json.dumps(
            {
                "success": result["success"],
                "output": str(args.output.resolve()),
                "elapsed_seconds": result["elapsed_seconds"],
                "partial_region_rate_hz": result["completed_partial_region_rate_hz"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
