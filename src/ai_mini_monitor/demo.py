from __future__ import annotations

from datetime import datetime, timezone
from math import sin

from .models import (
    AIData,
    AIProviderKind,
    ConnectionData,
    ConnectionStatus,
    DisplaySnapshot,
    Metric,
    SyncStatus,
)


def _history(length: int, center: float, amplitude: float, phase: float = 0.0) -> tuple[float, ...]:
    return tuple(min(100.0, max(0.0, center + amplitude * sin(index / 4.0 + phase))) for index in range(length))


def demo_snapshot(
    state: str = "normal",
    *,
    now: datetime | None = None,
) -> DisplaySnapshot:
    captured = now or datetime.now(timezone.utc)
    values = {
        "normal": (38.0, 51.0, 63.0, 67.0, 74.0, ConnectionStatus.ONLINE, SyncStatus.OK),
        "zero": (0.0, 0.0, 36.0, 42.0, 48.0, ConnectionStatus.ONLINE, SyncStatus.OK),
        "hundred": (100.0, 100.0, 91.0, 92.0, 86.0, ConnectionStatus.ONLINE, SyncStatus.OK),
        "temperature_warning": (78.0, 84.0, 94.0, 96.0, 71.0, ConnectionStatus.ONLINE, SyncStatus.OK),
        "memory_99": (34.0, 44.0, 62.0, 68.0, 99.0, ConnectionStatus.ONLINE, SyncStatus.OK),
        "ai_delayed": (41.0, 56.0, 64.0, 70.0, 76.0, ConnectionStatus.ONLINE, SyncStatus.DELAYED),
        "ai_error": (41.0, 56.0, 64.0, 70.0, 76.0, ConnectionStatus.ONLINE, SyncStatus.AUTH_ERROR),
        "unknown": (41.0, 56.0, 64.0, 70.0, 76.0, ConnectionStatus.DISCONNECTED, SyncStatus.SETUP_REQUIRED),
        "vram_max": (99.0, 100.0, 89.0, 91.0, 99.0, ConnectionStatus.ONLINE, SyncStatus.OK),
        "reconnecting": (40.0, 49.0, 61.0, 66.0, 73.0, ConnectionStatus.RECONNECTING, SyncStatus.OK),
        "disconnected": (39.0, 48.0, 60.0, 65.0, 72.0, ConnectionStatus.DISCONNECTED, SyncStatus.OK),
    }
    if state == "ai_not_configured":
        cpu, gpu, cpu_temp, gpu_temp, memory, connection, sync = values["normal"]
        ai = AIData(
            provider=AIProviderKind.NOT_CONFIGURED,
            title="AI PROVIDER",
            status=SyncStatus.SETUP_REQUIRED,
            primary_value="SETUP",
            primary_label="REQUIRED",
            fields=(("STATUS", "NOT LINKED"),),
            demo=False,
        )
    else:
        cpu, gpu, cpu_temp, gpu_temp, memory, connection, sync = values.get(state, values["normal"])
        ai = AIData(
            provider=AIProviderKind.CODEX_ACCOUNT,
            title="CODEX",
            status=sync,
            primary_value="--" if state in {"unknown", "ai_error"} else "79%",
            primary_label="7D LEFT",
            fields=(("5H LEFT", "62%"),),
            last_sync=captured,
            budget_ratio=None if state in {"unknown", "ai_error"} else 0.79,
            budget_label="7D LEFT 79%",
            demo=True,
        )
    total = 31.3
    used = total * memory / 100.0
    return DisplaySnapshot(
        timestamp=captured,
        cpu_model="Intel Core i7-14700K",
        gpu_model="NVIDIA GeForce RTX 5070 Ti",
        cpu_percent=Metric(cpu, "%"),
        cpu_temperature=Metric(cpu_temp, "°C"),
        gpu_percent=Metric(gpu, "%"),
        gpu_temperature=Metric(gpu_temp, "°C"),
        memory_percent=Metric(memory, "%"),
        memory_used_gib=Metric(used, "GiB"),
        memory_total_gib=Metric(total, "GiB"),
        memory_available_gib=Metric(total - used, "GiB"),
        gpu_vram_used_gib=Metric(None if state == "unknown" else 15.9 if state == "vram_max" else 7.5, "GiB"),
        gpu_vram_total_gib=Metric(None if state == "unknown" else 16.0, "GiB"),
        gpu_power_w=Metric(None if state == "unknown" else 235.0, "W"),
        cpu_history=_history(60, cpu, min(18.0, max(3.0, cpu / 3.0))),
        gpu_history=_history(60, gpu, min(22.0, max(3.0, gpu / 3.0)), 1.2),
        memory_history=_history(60, memory, 3.5, 0.7),
        ai=ai,
        connection=ConnectionData(status=connection, detail="PREVIEW DEMO"),
    )
