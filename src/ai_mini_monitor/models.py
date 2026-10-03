from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Sequence


class ConnectionStatus(str, Enum):
    ONLINE = "ONLINE"
    RECONNECTING = "RECONNECTING"
    DISCONNECTED = "DISCONNECTED"


class AIProviderKind(str, Enum):
    CODEX_ACCOUNT = "codex_account"
    CODEX_LOCAL = "codex_local"
    OPENAI_API = "openai_api"
    CHATGPT_ACTIVITY = "chatgpt_activity"
    NOT_CONFIGURED = "not_configured"


class SyncStatus(str, Enum):
    OK = "OK"
    DELAYED = "DELAYED"
    AUTH_ERROR = "AUTH ERROR"
    RATE_LIMITED = "RATE LIMITED"
    NETWORK_ERROR = "NETWORK ERROR"
    SETUP_REQUIRED = "SETUP REQUIRED"


@dataclass(frozen=True, slots=True)
class Metric:
    value: float | None
    unit: str = ""
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class ConnectionData:
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    port: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AIData:
    provider: AIProviderKind = AIProviderKind.NOT_CONFIGURED
    title: str = "AI PROVIDER"
    status: SyncStatus = SyncStatus.SETUP_REQUIRED
    primary_value: str = "SETUP"
    primary_label: str = "REQUIRED"
    fields: tuple[tuple[str, str], ...] = ()
    last_sync: datetime | None = None
    budget_ratio: float | None = None
    budget_label: str | None = None
    demo: bool = False
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class DisplaySnapshot:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cpu_model: str | None = None
    gpu_model: str | None = None
    cpu_percent: Metric = field(default_factory=lambda: Metric(None, "%", "not sampled"))
    cpu_temperature: Metric = field(default_factory=lambda: Metric(None, "°C", "sensor unavailable"))
    gpu_percent: Metric = field(default_factory=lambda: Metric(None, "%", "sensor unavailable"))
    gpu_temperature: Metric = field(default_factory=lambda: Metric(None, "°C", "sensor unavailable"))
    memory_percent: Metric = field(default_factory=lambda: Metric(None, "%", "not sampled"))
    memory_used_gib: Metric = field(default_factory=lambda: Metric(None, "GiB", "not sampled"))
    memory_total_gib: Metric = field(default_factory=lambda: Metric(None, "GiB", "not sampled"))
    memory_available_gib: Metric = field(default_factory=lambda: Metric(None, "GiB", "not sampled"))
    cpu_history: tuple[float | None, ...] = ()
    gpu_history: tuple[float | None, ...] = ()
    memory_history: tuple[float | None, ...] = ()
    ai: AIData = field(default_factory=AIData)
    connection: ConnectionData = field(default_factory=ConnectionData)
    gpu_vram_used_gib: Metric = field(default_factory=lambda: Metric(None, "GiB", "sensor unavailable"))
    gpu_vram_total_gib: Metric = field(default_factory=lambda: Metric(None, "GiB", "sensor unavailable"))
    gpu_power_w: Metric = field(default_factory=lambda: Metric(None, "W", "sensor unavailable"))


@dataclass(frozen=True, slots=True)
class SensorReading:
    identifier: str
    hardware: str
    name: str
    kind: str
    value: float | None
    unit: str
    reason: str | None = None
    hardware_identifier: str | None = None


@dataclass(frozen=True, slots=True)
class SensorSnapshot:
    captured_at: datetime
    cpu_percent: Metric
    cpu_temperature: Metric
    gpu_percent: Metric
    gpu_temperature: Metric
    memory_percent: Metric
    memory_used_gib: Metric
    memory_total_gib: Metric
    memory_available_gib: Metric
    cpu_model: str | None = None
    gpu_model: str | None = None
    discovered: tuple[SensorReading, ...] = ()
    gpu_vram_used_gib: Metric = field(default_factory=lambda: Metric(None, "GiB", "sensor unavailable"))
    gpu_vram_total_gib: Metric = field(default_factory=lambda: Metric(None, "GiB", "sensor unavailable"))
    gpu_power_w: Metric = field(default_factory=lambda: Metric(None, "W", "sensor unavailable"))


@dataclass(frozen=True, slots=True)
class FrameRegion:
    x: int
    y: int
    width: int
    height: int
    rgb: bytes


@dataclass(frozen=True, slots=True)
class FrameUpdate:
    sequence: int
    width: int
    height: int
    full_refresh: bool
    regions: tuple[FrameRegion, ...]
    submitted_at: float
