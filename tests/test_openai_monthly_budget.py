from datetime import date, datetime, timezone
from decimal import Decimal
import threading
import time

import pytest
from ai_mini_monitor.ai import openai_budget as budget
from ai_mini_monitor.config import OpenAIBudgetConfig


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize('anchor,now,start,end', [
    ('2024-11-05', '2024-12-04T23:59:59', '2024-11-05', '2024-12-05'),
    ('2024-11-05', '2024-12-05T00:00:00', '2024-12-05', '2025-01-05'),
    ('2024-01-31', '2024-02-29T00:00:00', '2024-02-29', '2024-03-31'),
    ('2024-01-31', '2024-03-30T23:59:59', '2024-02-29', '2024-03-31'),
    ('2024-01-31', '2025-02-28T12:00:00', '2025-02-28', '2025-03-31'),
    ('2024-02-29', '2025-03-29T00:00:00', '2025-03-29', '2025-04-29'),
])
def test_monthly_period_keeps_original_anchor_and_clamps_only_short_month(anchor, now, start, end):
    assert hasattr(budget, 'monthly_period'), 'monthly period calculation missing'
    actual = budget.monthly_period(anchor, utc(now))
    assert tuple(day.isoformat() for day in actual) == (start, end)


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.01)
    assert predicate()


def test_period_uses_utc_not_local_calendar_date():
    start, end = budget.monthly_period('2024-11-05', datetime.fromisoformat('2024-12-05T08:59:59+09:00'))
    assert (start.isoformat(), end.isoformat()) == ('2024-11-05', '2024-12-05')


def test_settings_summary_identifies_the_measured_period_and_next_reset():
    from types import SimpleNamespace
    from ai_mini_monitor.ui.openai_budget import OpenAIBudgetDialog
    messages = []
    dialog = SimpleNamespace(summary=SimpleNamespace(set=messages.append))
    snapshot = budget.BudgetSnapshot(OpenAIBudgetConfig(True, '100', '2024-11-05'),
        Decimal('23.50'), 'ok', period_start=date(2025, 1, 5), period_end=date(2025, 2, 5))
    OpenAIBudgetDialog.update(dialog, snapshot)
    assert '2025-01-05' in messages[-1] and '2025-02-05' in messages[-1]
    assert '23.5%' in messages[-1] and '$100.00' in messages[-1]


def test_configuring_same_month_does_not_bypass_minimum_request_interval():
    calls = []
    class Store:
        def get(self): return 'test-only'
    class Client:
        def __init__(self, key): pass
        def fetch_spend(self, start_date, now, **kwargs):
            calls.append(start_date)
            return Decimal('2')
    service = budget.OpenAIBudgetService(OpenAIBudgetConfig(True, '100', '2024-11-05'), secret_store=Store(), client_factory=Client)
    try:
        eventually(lambda: service.snapshot().state == 'ok')
        service.configure(OpenAIBudgetConfig(True, '200', '2024-11-05'))
        for _ in range(15):
            assert service.snapshot().ai.primary_value == '--'
            time.sleep(.01)
        assert len(calls) == 1
    finally:
        service.close()


def test_worker_queries_current_month_not_original_start_and_discards_old_month_reply(monkeypatch):
    clock = [utc('2025-01-04T23:59:59')]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return clock[0]
    monkeypatch.setattr(budget, 'datetime', Clock)
    entered, release = threading.Event(), threading.Event()
    seen = []
    class Store:
        def get(self): return 'test-only'
    class Client:
        def __init__(self, key): pass
        def fetch_spend(self, start_date, now, **kwargs):
            seen.append(start_date)
            if len(seen) == 1:
                entered.set()
                release.wait(3)
                return Decimal('99')
            return Decimal('2')
    service = budget.OpenAIBudgetService(OpenAIBudgetConfig(True, '100', '2024-11-05'), secret_store=Store(), client_factory=Client)
    try:
        assert entered.wait(2)
        assert seen == ['2024-12-05']
        clock[0] = utc('2025-01-05T00:00:01')
        assert service.snapshot().ai.primary_value == '--'
        release.set()
        eventually(lambda: service.snapshot().state == 'ok')
        snapshot = service.snapshot()
        assert seen == ['2024-12-05', '2025-01-05']
        assert snapshot.ai.primary_value == '2%'
        assert snapshot.config.start_date == '2024-11-05'
        assert snapshot.config.total_usd == '100'
        assert snapshot.period_start.isoformat() == '2025-01-05'
        assert snapshot.period_end.isoformat() == '2025-02-05'
    finally:
        release.set()
        service.close()


def test_expired_success_is_hidden_until_new_month_query_finishes(monkeypatch):
    clock = [utc('2025-01-04T23:59:59')]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return clock[0]
    monkeypatch.setattr(budget, 'datetime', Clock)
    release = threading.Event()
    class Store:
        def get(self): return 'test-only'
    class Client:
        def __init__(self, key): pass
        def fetch_spend(self, start_date, now, **kwargs):
            if start_date == '2025-01-05':
                release.wait(3)
                return Decimal('0')
            return Decimal('99')
    service = budget.OpenAIBudgetService(OpenAIBudgetConfig(True, '100', '2024-11-05'), secret_store=Store(), client_factory=Client)
    try:
        eventually(lambda: service.snapshot().state == 'ok')
        assert service.snapshot().ai.primary_value == '99%'
        clock[0] = utc('2025-01-05T00:00:01')
        snapshot = service.snapshot()
        assert snapshot.ai.primary_value == '--'
        assert snapshot.spent is None and snapshot.last_success is None
        release.set()
        eventually(lambda: service.snapshot().state == 'ok')
        assert service.snapshot().ai.primary_value == '0%'
    finally:
        release.set()
        service.close()
