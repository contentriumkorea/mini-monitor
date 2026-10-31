# SPDX-License-Identifier: GPL-3.0-or-later
"""Official organization costs, not a prepaid-credit ledger. No browser access."""
from __future__ import annotations

import copy
import calendar
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import threading
import time
import urllib.parse
import urllib.request

from ..config import OpenAIBudgetConfig
from ..models import AIData, AIProviderKind, SyncStatus
from ..security.dpapi import DPAPISecretStore
from .openai_usage import APIErrorKind, COSTS_URL, OpenAIUsageClient, OpenAIUsageError


def validate_budget(config: OpenAIBudgetConfig) -> None:
    if not isinstance(config.enabled, bool):
        raise ValueError("OpenAI API 표시 여부가 올바르지 않습니다")
    if not isinstance(config.total_usd, str) or not isinstance(config.start_date, str):
        raise ValueError("총액과 시작일을 입력해 주세요")
    if not config.enabled and not config.total_usd and not config.start_date:
        return
    if not re.fullmatch(r"\d{1,6}(?:\.\d{1,2})?", config.total_usd) or Decimal(config.total_usd) <= 0:
        raise ValueError("총액은 0보다 크고 999,999.99 이하인 USD 금액이어야 합니다")
    try:
        start = date.fromisoformat(config.start_date)
    except (ValueError, TypeError):
        raise ValueError("시작일을 YYYY-MM-DD 형식으로 입력해 주세요") from None
    if start.isoformat() != config.start_date or start > datetime.now(timezone.utc).date():
        raise ValueError("시작일은 미래가 아닌 YYYY-MM-DD 날짜여야 합니다")


def monthly_period(start_date: str, now: datetime) -> tuple[date, date]:
    """UTC calendar month, always anchored to the original day (end exclusive)."""
    anchor = date.fromisoformat(start_date)
    today = now.astimezone(timezone.utc).date()
    month = today.year * 12 + today.month - 1

    def boundary(index: int) -> date:
        year, zero_month = divmod(index, 12)
        number = zero_month + 1
        return date(year, number, min(anchor.day, calendar.monthrange(year, number)[1]))

    if boundary(month) > today:
        month -= 1
    return boundary(month), boundary(month + 1)


def _bad_response() -> OpenAIUsageError:
    return OpenAIUsageError(APIErrorKind.INVALID_RESPONSE, "Invalid USD costs response")


def _amount(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 40:
        raise _bad_response()
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise _bad_response() from None
    if not number.is_finite() or number < 0 or number > Decimal("1000000000000"):
        raise _bad_response()
    return number


class BudgetCostsClient(OpenAIUsageClient):
    def fetch_spend(self, start_date: str, now: datetime, *, cancelled=lambda: False) -> Decimal:
        start = int(datetime.combine(date.fromisoformat(start_date), datetime.min.time(), tzinfo=timezone.utc).timestamp())
        end = int(now.timestamp())
        if end <= start:
            return Decimal(0)
        total = Decimal(0)
        cursor = None
        seen: set[str] = set()
        for _ in range(100):
            if cancelled():
                raise OpenAIUsageError(APIErrorKind.NETWORK, "Cancelled")
            params = {"start_time": start, "end_time": end, "bucket_width": "1d", "limit": 180}
            if cursor:
                params["page"] = cursor
            request = urllib.request.Request(COSTS_URL + "?" + urllib.parse.urlencode(params),
                headers={"Authorization": f"Bearer {self._admin_key}", "Accept": "application/json"}, method="GET")
            payload = self._request_json(request)
            if payload.get("object") != "page" or not isinstance(payload.get("data"), list) or not isinstance(payload.get("has_more"), bool):
                raise _bad_response()
            for bucket in payload["data"]:
                if not isinstance(bucket, dict) or bucket.get("object") != "bucket" or not isinstance(bucket.get("results"), list):
                    raise _bad_response()
                bucket_start, bucket_end = bucket.get("start_time"), bucket.get("end_time")
                if (type(bucket_start) is not int or type(bucket_end) is not int
                        or not start <= bucket_start < end or not bucket_start < bucket_end <= bucket_start + 86400):
                    raise _bad_response()
                for result in bucket["results"]:
                    if not isinstance(result, dict) or result.get("object") != "organization.costs.result":
                        raise _bad_response()
                    amount = result.get("amount")
                    if not isinstance(amount, dict) or str(amount.get("currency", "")).lower() != "usd":
                        raise _bad_response()
                    total += _amount(amount.get("value"))
                    if total > Decimal("1000000000000"):
                        raise _bad_response()
            if not payload["has_more"]:
                return total
            cursor = payload.get("next_page")
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen:
                raise _bad_response()
            seen.add(cursor)
        raise _bad_response()


def usd(value: Decimal) -> str:
    return f"${value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP):,.2f}"


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    config: OpenAIBudgetConfig = field(default_factory=OpenAIBudgetConfig)
    spent: Decimal | None = None
    state: str = "disabled"
    last_success: datetime | None = None
    period_start: date | None = None
    period_end: date | None = None

    @property
    def ai(self) -> AIData | None:
        if not self.config.enabled:
            return None
        total = Decimal(self.config.total_usd)
        valid = self.state == "ok" and self.spent is not None
        ratio = self.spent / total if valid else None
        percent = ((ratio * 100).quantize(Decimal('.1'), rounding=ROUND_HALF_UP) if ratio is not None else None)
        value = (format(percent, 'f').rstrip('0').rstrip('.') + "%") if percent is not None else "--"
        # Only remove fractional zeros, never the integer zero.
        if value == "%":
            value = "0%"
        status = SyncStatus.OK if valid else (SyncStatus.AUTH_ERROR if self.state == "auth" else SyncStatus.DELAYED)
        return AIData(provider=AIProviderKind.OPENAI_API, title="OPENAI API", status=status,
            primary_value=value, primary_label="BUDGET USED", fields=(("USED", usd(self.spent) if valid else "--"), ("TOTAL", usd(total))),
            last_sync=self.last_success, budget_ratio=float(ratio) if ratio is not None else None,
            budget_label="CONFIGURED TOTAL", error_detail=None if valid else self.state)


class OpenAIBudgetService:
    """One worker, bounded polling, immutable snapshots, no Tk or secret logging."""
    def __init__(self, config: OpenAIBudgetConfig, *, secret_store=None, client_factory=BudgetCostsClient):
        validate_budget(config)
        self._store = secret_store if secret_store is not None else DPAPISecretStore()
        self._client_factory = client_factory
        self._cv = threading.Condition()
        self._closed = False
        self._generation = 0
        self._last_attempt = float('-inf')
        self._due = time.monotonic()
        self._snapshot = BudgetSnapshot(copy.deepcopy(config), state="pending" if config.enabled else "disabled")
        self._thread = threading.Thread(target=self._run, name="openai-costs", daemon=True)
        self._thread.start()

    def snapshot(self) -> BudgetSnapshot:
        with self._cv:
            self._roll_period_locked()
            return copy.deepcopy(self._snapshot)

    def _roll_period_locked(self) -> None:
        if self._closed or not self._snapshot.config.enabled:
            return
        start, end = monthly_period(self._snapshot.config.start_date, datetime.now(timezone.utc))
        if self._snapshot.period_start != start:
            previous_period = self._snapshot.period_start
            self._generation += 1
            self._snapshot = BudgetSnapshot(self._snapshot.config, state="pending",
                                            period_start=start, period_end=end)
            if previous_period is not None:
                self._due = time.monotonic()
            self._cv.notify_all()

    def configure(self, config: OpenAIBudgetConfig) -> None:
        validate_budget(config)
        with self._cv:
            self._generation += 1
            self._snapshot = BudgetSnapshot(copy.deepcopy(config), state="pending" if config.enabled else "disabled")
            self._due = max(time.monotonic(), self._last_attempt + 60)
            self._cv.notify_all()

    def refresh(self) -> bool:
        with self._cv:
            self._roll_period_locked()
            if self._closed or not self._snapshot.config.enabled or time.monotonic() - self._last_attempt < 60:
                return False
            self._due = time.monotonic()
            self._cv.notify_all()
            return True

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._generation += 1
            self._cv.notify_all()
        self._thread.join(timeout=1)

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._closed:
                    self._roll_period_locked()
                    if self._snapshot.config.enabled:
                        wait = self._due - time.monotonic()
                        if wait <= 0:
                            break
                        self._cv.wait(min(wait, 60))
                    else:
                        self._cv.wait()
                if self._closed:
                    return
                generation = self._generation
                previous_success = self._snapshot.last_success
                self._snapshot = replace(self._snapshot, spent=None, state="pending")
                period_start = self._snapshot.period_start
                self._last_attempt = time.monotonic()
                self._due = self._last_attempt + 600
            spent = None
            state = "ok"
            success = previous_success
            try:
                key = self._store.get()
                if not key:
                    state = "key_required"
                else:
                    client = self._client_factory(key)
                    key = None
                    spent = client.fetch_spend(period_start.isoformat(), datetime.now(timezone.utc),
                                               cancelled=lambda: self._cancelled(generation))
                    success = datetime.now(timezone.utc)
            except OpenAIUsageError as error:
                state = {APIErrorKind.UNAUTHORIZED: "auth", APIErrorKind.FORBIDDEN: "auth", APIErrorKind.RATE_LIMITED: "rate_limited",
                    APIErrorKind.INVALID_RESPONSE: "invalid_response"}.get(error.kind, "network")
            except Exception:
                # No exception text: a dependency may include credentials or paths.
                state = "unavailable"
            with self._cv:
                self._roll_period_locked()
                if not self._closed and self._generation == generation:
                    self._snapshot = replace(self._snapshot, spent=spent, state=state, last_success=success)

    def _cancelled(self, generation: int) -> bool:
        with self._cv:
            self._roll_period_locked()
            return self._closed or generation != self._generation
