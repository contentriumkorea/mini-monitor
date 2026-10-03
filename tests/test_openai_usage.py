# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from ai_mini_monitor.ai.openai_usage import (
    APIErrorKind,
    CostTotals,
    OpenAIUsageClient,
    OpenAIUsageError,
    OpenAIUsageProvider,
    MAX_RESPONSE_BYTES,
    RejectRedirects,
    UsageTotals,
    parse_costs_response,
    parse_usage_response,
)
from ai_mini_monitor.models import SyncStatus


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        data = (
            self.payload
            if isinstance(self.payload, bytes)
            else json.dumps(self.payload).encode("utf-8")
        )
        return data if size < 0 else data[:size]


class FakeOpener:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Any, float]] = []

    def __call__(self, request, *, timeout: float):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(response)


def usage_result(requests: int, input_tokens: int, output_tokens: int, cached: int) -> dict[str, Any]:
    return {
        "object": "organization.usage.completions.result",
        "num_model_requests": requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_cached_tokens": cached,
    }


def test_usage_and_cost_parsers_aggregate_only_expected_objects() -> None:
    usage = parse_usage_response(
        {
            "data": [
                {"results": [usage_result(2, 100, 20, 30), {"object": "ignored"}]},
                {"results": [usage_result(3, 200, 40, 50), "invalid"]},
            ]
        }
    )
    assert usage == UsageTotals(5, 300, 60, 80)

    costs = parse_costs_response(
        {
            "data": [
                {
                    "start_time": 100,
                    "results": [
                        {"object": "organization.costs.result", "amount": {"value": "1.25", "currency": "USD"}}
                    ],
                },
                {
                    "start_time": 200,
                    "results": [
                        {"object": "organization.costs.result", "amount": {"value": 2.5, "currency": "USD"}}
                    ],
                },
            ]
        },
        today_start=200,
    )
    assert costs == CostTotals(today=2.5, month=3.75, currency="usd")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_cost_parser_rejects_non_finite_amounts(value: float) -> None:
    payload = {
        "data": [
            {
                "start_time": 200,
                "results": [
                    {
                        "object": "organization.costs.result",
                        "amount": {"value": value, "currency": "USD"},
                    }
                ],
            }
        ]
    }
    with pytest.raises(OpenAIUsageError) as raised:
        parse_costs_response(payload, today_start=200)
    assert raised.value.kind is APIErrorKind.INVALID_RESPONSE


def test_fake_opener_pagination_and_headers_without_network() -> None:
    opener = FakeOpener(
        [
            {"data": [{"results": [usage_result(1, 10, 2, 3)]}], "has_more": True, "next_page": "cursor-2"},
            {"data": [{"results": [usage_result(2, 20, 4, 6)]}], "has_more": False},
        ]
    )
    client = OpenAIUsageClient("test-admin-key", timeout=4.5, opener=opener)
    assert client.fetch_usage(100, 200) == UsageTotals(3, 30, 6, 9)
    assert len(opener.calls) == 2
    first_request, timeout = opener.calls[0]
    second_request, _ = opener.calls[1]
    assert timeout == 4.5
    assert first_request.get_header("Authorization") == "Bearer test-admin-key"
    assert "page" not in urllib.parse.parse_qs(urllib.parse.urlparse(first_request.full_url).query)
    assert urllib.parse.parse_qs(urllib.parse.urlparse(second_request.full_url).query)["page"] == ["cursor-2"]


class CountingClient:
    def __init__(self) -> None:
        self.usage_calls = 0
        self.cost_calls = 0

    def fetch_usage(self, start_time: int, end_time: int) -> UsageTotals:
        self.usage_calls += 1
        return UsageTotals(self.usage_calls, 1_000, 200, 300)

    def fetch_costs(self, month_start: int, end_time: int, today_start: int) -> CostTotals:
        self.cost_calls += 1
        return CostTotals(today=1.25, month=4.5)


@pytest.mark.parametrize(
    ("usage_interval", "cost_interval"),
    [
        (float("nan"), 600),
        (float("inf"), 600),
        (True, 600),
        ("60", 600),
        (60, float("nan")),
        (60, float("inf")),
        (60, False),
        (60, "600"),
    ],
)
def test_provider_rejects_non_finite_or_non_numeric_polling_intervals(
    usage_interval: object,
    cost_interval: object,
) -> None:
    with pytest.raises(ValueError, match="polling intervals"):
        OpenAIUsageProvider(
            CountingClient(),
            usage_interval=usage_interval,  # type: ignore[arg-type]
            cost_interval=cost_interval,  # type: ignore[arg-type]
        )


def test_provider_cache_intervals_and_stale_status() -> None:
    client = CountingClient()
    provider = OpenAIUsageProvider(
        client,
        usage_interval=60,
        cost_interval=600,
        daily_budget=5.0,
        monthly_budget=30.0,
    )
    start = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)

    first = provider.refresh(start)
    assert (client.usage_calls, client.cost_calls) == (1, 1)
    assert first.status is SyncStatus.OK
    assert first.primary_value == "$1.25"
    assert first.budget_ratio == pytest.approx(0.25)
    assert first.budget_label == "DAY 25% | MONTH 15%"

    provider.refresh(start + timedelta(seconds=59))
    assert (client.usage_calls, client.cost_calls) == (1, 1)
    provider.refresh(start + timedelta(seconds=60))
    assert (client.usage_calls, client.cost_calls) == (2, 1)
    provider.refresh(start + timedelta(seconds=600))
    assert (client.usage_calls, client.cost_calls) == (3, 2)

    provider.cache.last_success = start
    assert provider.to_display(start + timedelta(seconds=181)).status is SyncStatus.DELAYED


class AlwaysFailingClient:
    def __init__(self) -> None:
        self.usage_calls = 0
        self.cost_calls = 0

    def fetch_usage(self, start_time: int, end_time: int) -> UsageTotals:
        self.usage_calls += 1
        raise OpenAIUsageError(APIErrorKind.NETWORK, "offline")

    def fetch_costs(self, month_start: int, end_time: int, today_start: int) -> CostTotals:
        self.cost_calls += 1
        raise OpenAIUsageError(APIErrorKind.NETWORK, "offline")


class CostsFailingClient:
    def __init__(self) -> None:
        self.usage_calls = 0
        self.cost_calls = 0

    def fetch_usage(self, start_time: int, end_time: int) -> UsageTotals:
        self.usage_calls += 1
        return UsageTotals(requests=1)

    def fetch_costs(self, month_start: int, end_time: int, today_start: int) -> CostTotals:
        self.cost_calls += 1
        raise OpenAIUsageError(APIErrorKind.NETWORK, "costs offline")


def test_failed_endpoints_respect_minimum_retry_intervals() -> None:
    client = AlwaysFailingClient()
    provider = OpenAIUsageProvider(client, usage_interval=60, cost_interval=600)
    start = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)

    first = provider.refresh(start)
    assert first.status is SyncStatus.NETWORK_ERROR
    assert (client.usage_calls, client.cost_calls) == (1, 0)

    provider.refresh(start + timedelta(seconds=1))
    provider.refresh(start + timedelta(seconds=59))
    assert (client.usage_calls, client.cost_calls) == (1, 0)

    provider.refresh(start + timedelta(seconds=60))
    assert (client.usage_calls, client.cost_calls) == (2, 0)
    provider.refresh(start + timedelta(seconds=600))
    assert (client.usage_calls, client.cost_calls) == (3, 0)

    costs_client = CostsFailingClient()
    costs_provider = OpenAIUsageProvider(costs_client, usage_interval=60, cost_interval=600)
    costs_provider.refresh(start)
    costs_provider.refresh(start + timedelta(seconds=60))
    costs_provider.refresh(start + timedelta(seconds=599))
    assert (costs_client.usage_calls, costs_client.cost_calls) == (3, 1)
    costs_provider.refresh(start + timedelta(seconds=600))
    assert (costs_client.usage_calls, costs_client.cost_calls) == (3, 2)


def test_local_day_rollover_forces_fresh_today_and_month_queries() -> None:
    client = CountingClient()
    provider = OpenAIUsageProvider(client, usage_interval=60, cost_interval=600)
    before_midnight = datetime(
        2026,
        8,
        10,
        23,
        59,
        50,
        tzinfo=timezone(timedelta(hours=9)),
    )
    provider.refresh(before_midnight)
    provider.refresh(before_midnight + timedelta(seconds=20))
    assert (client.usage_calls, client.cost_calls) == (2, 2)


class FailUsageOnceClient(CountingClient):
    def fetch_usage(self, start_time: int, end_time: int) -> UsageTotals:
        self.usage_calls += 1
        if self.usage_calls == 1:
            raise OpenAIUsageError(APIErrorKind.NETWORK, "temporary")
        return UsageTotals(requests=1)


def test_skipped_costs_retry_after_the_short_usage_recovery_interval() -> None:
    client = FailUsageOnceClient()
    provider = OpenAIUsageProvider(client, usage_interval=60, cost_interval=600)
    start = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)
    assert provider.refresh(start).status is SyncStatus.NETWORK_ERROR
    assert (client.usage_calls, client.cost_calls) == (1, 0)
    recovered = provider.refresh(start + timedelta(seconds=60))
    assert (client.usage_calls, client.cost_calls) == (2, 1)
    assert recovered.status is SyncStatus.OK
    assert recovered.primary_value == "$1.25"


@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
def test_admin_key_redirects_are_refused_for_every_redirect_code(status_code: int) -> None:
    request = urllib.request.Request(
        "https://api.openai.com/v1/organization/usage/completions",
        headers={"Authorization": "Bearer test-admin-key"},
    )
    with pytest.raises(urllib.error.HTTPError, match="redirect refused"):
        RejectRedirects().redirect_request(
            request,
            None,
            status_code,
            "redirect",
            {},
            "https://attacker.invalid/collect",
        )


def test_oversized_api_response_is_rejected_without_unbounded_read() -> None:
    opener = FakeOpener([b"x" * (MAX_RESPONSE_BYTES + 1)])
    client = OpenAIUsageClient("test-admin-key", opener=opener)
    with pytest.raises(OpenAIUsageError) as raised:
        client.fetch_usage(100, 200)
    assert raised.value.kind is APIErrorKind.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("status_code", "kind", "display_status"),
    [
        (401, APIErrorKind.UNAUTHORIZED, SyncStatus.AUTH_ERROR),
        (403, APIErrorKind.FORBIDDEN, SyncStatus.AUTH_ERROR),
        (429, APIErrorKind.RATE_LIMITED, SyncStatus.RATE_LIMITED),
    ],
)
def test_http_error_mapping_with_fake_opener(
    status_code: int, kind: APIErrorKind, display_status: SyncStatus
) -> None:
    error = urllib.error.HTTPError("https://example.invalid", status_code, "error", {}, None)
    opener = FakeOpener([error])
    client = OpenAIUsageClient("test-admin-key", opener=opener)
    provider = OpenAIUsageProvider(client)
    display = provider.refresh(datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc))
    assert provider.cache.last_error is not None
    assert provider.cache.last_error.kind is kind
    assert display.status is display_status
    assert display.error_detail == kind.value


def test_invalid_payload_is_not_silently_treated_as_zero() -> None:
    with pytest.raises(OpenAIUsageError) as error:
        parse_usage_response({"unexpected": []})
    assert error.value.kind is APIErrorKind.INVALID_RESPONSE
