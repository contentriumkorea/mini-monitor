from __future__ import annotations

import json
import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from ..models import AIData, AIProviderKind, SyncStatus


USAGE_URL = "https://api.openai.com/v1/organization/usage/completions"
COSTS_URL = "https://api.openai.com/v1/organization/costs"
MAX_RESPONSE_BYTES = 2_000_000


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward an Admin Key to a redirected URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if fp is not None:
            fp.close()
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "OpenAI API redirect refused",
            headers,
            None,
        )


class APIErrorKind(str, Enum):
    UNAUTHORIZED = "401"
    FORBIDDEN = "403"
    RATE_LIMITED = "429"
    NETWORK = "network"
    INVALID_RESPONSE = "invalid_response"


class OpenAIUsageError(RuntimeError):
    def __init__(self, kind: APIErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class UsageTotals:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True, slots=True)
class CostTotals:
    today: float = 0.0
    month: float = 0.0
    currency: str = "usd"


@dataclass(slots=True)
class OpenAICache:
    usage: UsageTotals | None = None
    costs: CostTotals | None = None
    usage_fetched_at: datetime | None = None
    costs_fetched_at: datetime | None = None
    last_success: datetime | None = None
    last_error: OpenAIUsageError | None = None
    usage_error: OpenAIUsageError | None = None
    costs_error: OpenAIUsageError | None = None
    usage_day: str | None = None
    costs_day: str | None = None
    costs_month: str | None = None
    costs_deferred_until: datetime | None = None


def parse_usage_response(payload: dict[str, Any]) -> UsageTotals:
    requests = input_tokens = output_tokens = cached_tokens = 0
    for bucket in _buckets(payload):
        for result in bucket.get("results", []):
            if not isinstance(result, dict):
                continue
            if result.get("object") != "organization.usage.completions.result":
                continue
            requests += _integer(result.get("num_model_requests"))
            input_tokens += _integer(result.get("input_tokens"))
            output_tokens += _integer(result.get("output_tokens"))
            cached_tokens += _integer(result.get("input_cached_tokens"))
    return UsageTotals(requests, input_tokens, output_tokens, cached_tokens)


def parse_costs_response(payload: dict[str, Any], today_start: int) -> CostTotals:
    today = month = 0.0
    currency = "usd"
    for bucket in _buckets(payload):
        bucket_value = 0.0
        for result in bucket.get("results", []):
            if not isinstance(result, dict) or result.get("object") != "organization.costs.result":
                continue
            amount = result.get("amount") or {}
            if isinstance(amount, dict):
                bucket_value += _number(amount.get("value"))
                if isinstance(amount.get("currency"), str):
                    currency = amount["currency"].lower()
        month += bucket_value
        if _integer(bucket.get("start_time")) >= today_start:
            today += bucket_value
    return CostTotals(today=today, month=month, currency=currency)


def _buckets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise OpenAIUsageError(APIErrorKind.INVALID_RESPONSE, "response has no data array")
    return [bucket for bucket in payload["data"] if isinstance(bucket, dict)]


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _number(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        raise OpenAIUsageError(
            APIErrorKind.INVALID_RESPONSE,
            "OpenAI API returned a non-finite numeric value",
        )
    return number


class OpenAIUsageClient:
    def __init__(self, admin_key: str, timeout: float = 15.0, opener: Callable[..., Any] | None = None) -> None:
        if not admin_key:
            raise ValueError("admin key is required")
        self._admin_key = admin_key
        self._timeout = timeout
        self._opener = opener or urllib.request.build_opener(RejectRedirects()).open

    def fetch_usage(self, start_time: int, end_time: int) -> UsageTotals:
        payloads = self._fetch_pages(
            USAGE_URL,
            {"start_time": start_time, "end_time": end_time, "bucket_width": "1d", "limit": 31},
        )
        totals = UsageTotals()
        for payload in payloads:
            page = parse_usage_response(payload)
            totals = UsageTotals(
                totals.requests + page.requests,
                totals.input_tokens + page.input_tokens,
                totals.output_tokens + page.output_tokens,
                totals.cached_tokens + page.cached_tokens,
            )
        return totals

    def fetch_costs(self, month_start: int, end_time: int, today_start: int) -> CostTotals:
        payloads = self._fetch_pages(
            COSTS_URL,
            {"start_time": month_start, "end_time": end_time, "bucket_width": "1d", "limit": 31},
        )
        aggregate = CostTotals()
        for payload in payloads:
            page = parse_costs_response(payload, today_start)
            aggregate = CostTotals(aggregate.today + page.today, aggregate.month + page.month, page.currency)
        return aggregate

    def _fetch_pages(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        page: str | None = None
        for _ in range(20):
            query = dict(params)
            if page:
                query["page"] = page
            target = url + "?" + urllib.parse.urlencode(query)
            request = urllib.request.Request(
                target,
                headers={
                    "Authorization": f"Bearer {self._admin_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "AI-Mini-Monitor/0.1",
                },
                method="GET",
            )
            payload = self._request_json(request)
            output.append(payload)
            if not payload.get("has_more"):
                return output
            next_page = payload.get("next_page")
            if not isinstance(next_page, str) or not next_page:
                raise OpenAIUsageError(APIErrorKind.INVALID_RESPONSE, "has_more without next_page")
            page = next_page
        raise OpenAIUsageError(APIErrorKind.INVALID_RESPONSE, "pagination limit exceeded")

    def _request_json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise OpenAIUsageError(
                        APIErrorKind.INVALID_RESPONSE,
                        "OpenAI API response exceeded the size limit",
                    )
                payload = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as error:
            kind = {
                401: APIErrorKind.UNAUTHORIZED,
                403: APIErrorKind.FORBIDDEN,
                429: APIErrorKind.RATE_LIMITED,
            }.get(error.code, APIErrorKind.NETWORK)
            raise OpenAIUsageError(kind, f"OpenAI API HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            raise OpenAIUsageError(APIErrorKind.NETWORK, f"OpenAI API network error: {type(error).__name__}") from None
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenAIUsageError(APIErrorKind.INVALID_RESPONSE, "OpenAI API returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise OpenAIUsageError(APIErrorKind.INVALID_RESPONSE, "OpenAI API response is not an object")
        return payload


class OpenAIUsageProvider:
    def __init__(
        self,
        client: OpenAIUsageClient,
        *,
        usage_interval: int = 60,
        cost_interval: int = 600,
        daily_budget: float | None = None,
        monthly_budget: float | None = None,
    ) -> None:
        if (
            isinstance(usage_interval, bool)
            or not isinstance(usage_interval, (int, float))
            or not math.isfinite(float(usage_interval))
            or isinstance(cost_interval, bool)
            or not isinstance(cost_interval, (int, float))
            or not math.isfinite(float(cost_interval))
            or usage_interval < 60
            or cost_interval < 600
        ):
            raise ValueError("OpenAI polling intervals are below the documented application minimum")
        self.client = client
        self.usage_interval = usage_interval
        self.cost_interval = cost_interval
        self.daily_budget = daily_budget
        self.monthly_budget = monthly_budget
        self.cache = OpenAICache()

    def refresh(self, now: datetime | None = None) -> AIData:
        current = (now or datetime.now().astimezone()).astimezone()
        today = current.replace(hour=0, minute=0, second=0, microsecond=0)
        month = today.replace(day=1)
        today_key = today.date().isoformat()
        month_key = month.date().isoformat()
        if self.cache.usage_day != today_key:
            self.cache.usage = None
            self.cache.usage_fetched_at = None
            self.cache.usage_error = None
            self.cache.usage_day = today_key
        if (
            self.cache.costs_day != today_key
            or self.cache.costs_month != month_key
        ):
            self.cache.costs = None
            self.cache.costs_fetched_at = None
            self.cache.costs_error = None
            self.cache.costs_day = today_key
            self.cache.costs_month = month_key
            self.cache.costs_deferred_until = None
        end_time = int(current.timestamp()) + 1
        attempted = False
        usage_failed = False
        if self._due(self.cache.usage_fetched_at, self.usage_interval, current):
            attempted = True
            # This is an attempt timestamp, not only a success timestamp. It
            # prevents a failed endpoint from being retried by the 1 Hz UI loop.
            self.cache.usage_fetched_at = current
            try:
                self.cache.usage = self.client.fetch_usage(int(today.timestamp()), end_time)
                self.cache.last_success = current
                self.cache.usage_error = None
            except OpenAIUsageError as error:
                self.cache.usage_error = error
                usage_failed = True
                self.cache.costs_deferred_until = current + timedelta(
                    seconds=self.usage_interval
                )
        costs_deferred = (
            self.cache.costs_deferred_until is not None
            and current < self.cache.costs_deferred_until
        )
        if (
            not usage_failed
            and not costs_deferred
            and self._due(self.cache.costs_fetched_at, self.cost_interval, current)
        ):
            attempted = True
            self.cache.costs_fetched_at = current
            try:
                self.cache.costs = self.client.fetch_costs(int(month.timestamp()), end_time, int(today.timestamp()))
                self.cache.last_success = current
                self.cache.costs_error = None
                self.cache.costs_deferred_until = None
            except OpenAIUsageError as error:
                self.cache.costs_error = error
        if attempted:
            self.cache.last_error = self.cache.usage_error or self.cache.costs_error
        return self.to_display(current)

    @staticmethod
    def _due(previous: datetime | None, seconds: int, now: datetime) -> bool:
        return previous is None or (now - previous).total_seconds() >= seconds

    def to_display(self, now: datetime | None = None) -> AIData:
        current = now or datetime.now().astimezone()
        usage = self.cache.usage
        costs = self.cache.costs
        status = self._status(current)
        today_cost = costs.today if costs else None
        primary = "--" if today_cost is None else f"${today_cost:.2f}"
        fields = (
            ("REQUESTS", "--" if usage is None else _short(usage.requests)),
            ("INPUT", "--" if usage is None else _short(usage.input_tokens)),
            ("OUTPUT", "--" if usage is None else _short(usage.output_tokens)),
            ("CACHED", "--" if usage is None else _short(usage.cached_tokens)),
        )
        ratio = None
        budget_label = None
        budget_parts: list[str] = []
        ratios: list[float] = []
        if today_cost is not None and self.daily_budget:
            daily_ratio = today_cost / self.daily_budget
            ratios.append(daily_ratio)
            budget_parts.append(f"DAY {daily_ratio:.0%}")
        if costs is not None and self.monthly_budget:
            monthly_ratio = costs.month / self.monthly_budget
            ratios.append(monthly_ratio)
            budget_parts.append(f"MONTH {monthly_ratio:.0%}")
        if ratios:
            ratio = max(ratios)
            budget_label = " | ".join(budget_parts)
        return AIData(
            provider=AIProviderKind.OPENAI_API,
            title="OPENAI API",
            status=status,
            primary_value=primary,
            primary_label="TODAY COST",
            fields=fields,
            last_sync=self.cache.last_success,
            budget_ratio=ratio,
            budget_label=budget_label,
            error_detail=self.cache.last_error.kind.value if self.cache.last_error else None,
        )

    def _status(self, now: datetime) -> SyncStatus:
        if self.cache.last_error:
            return {
                APIErrorKind.UNAUTHORIZED: SyncStatus.AUTH_ERROR,
                APIErrorKind.FORBIDDEN: SyncStatus.AUTH_ERROR,
                APIErrorKind.RATE_LIMITED: SyncStatus.RATE_LIMITED,
                APIErrorKind.NETWORK: SyncStatus.NETWORK_ERROR,
                APIErrorKind.INVALID_RESPONSE: SyncStatus.NETWORK_ERROR,
            }[self.cache.last_error.kind]
        if self.cache.last_success is None:
            return SyncStatus.DELAYED
        if (now - self.cache.last_success).total_seconds() > max(self.usage_interval * 2, 180):
            return SyncStatus.DELAYED
        return SyncStatus.OK


def _short(value: int) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)
