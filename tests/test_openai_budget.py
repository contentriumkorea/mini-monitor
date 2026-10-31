from datetime import datetime, timezone
from decimal import Decimal
import threading
import time
import urllib.parse

import pytest

from ai_mini_monitor import config as cfg
from ai_mini_monitor.ai import openai_usage


def module():
    from ai_mini_monitor.ai import openai_budget
    return openai_budget


def result(value, currency="usd"):
    return {"object": "organization.costs.result", "amount": {"value": value, "currency": currency}}


def page(*values, more=False, cursor=None):
    return {"object": "page", "data": [{"object": "bucket", "start_time": 1790812800,
        "end_time": 1790899200, "results": [result(v) for v in values]}],
        "has_more": more, "next_page": cursor}


def test_feature_exposes_separate_config_without_replacing_codex():
    assert hasattr(cfg, "OpenAIBudgetConfig"), "separate API budget configuration is missing"
    c = cfg.AppConfig()
    assert not c.openai.enabled
    assert c.ai.provider == "codex_account"


def test_config_roundtrip_contains_no_key_and_rejects_invalid_total_date(tmp_path):
    m = module()
    c = cfg.AppConfig(openai=cfg.OpenAIBudgetConfig(True, "100.00", "2026-10-01"))
    path = cfg.save_config(c, tmp_path / "config.json")
    assert cfg.load_config(path).openai == c.openai
    assert "key" not in path.read_text()
    for total in ("0", "-1", "NaN", "Infinity", "1.001", "1000000", "1e10", True):
        with pytest.raises(ValueError):
            m.validate_budget(cfg.OpenAIBudgetConfig(True, total, "2026-10-01"))
    for date in ("2026-02-30", "2099-01-01", "2026-1-1", "", None):
        with pytest.raises(ValueError):
            m.validate_budget(cfg.OpenAIBudgetConfig(True, "100", date))


def test_cost_client_queries_utc_start_and_all_pages_without_completions():
    m = module()
    payloads = [page("0.10", more=True, cursor="next"), page("0.20", "23.20")]
    class Client(m.BudgetCostsClient):
        def _request_json(self, request):
            assert request.full_url.startswith(openai_usage.COSTS_URL + "?")
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            assert query["start_time"] == ["1790812800"]  # Oct 1 UTC, not local midnight.
            assert query["bucket_width"] == ["1d"]
            if len(payloads) == 1:
                assert query["page"] == ["next"]
            return payloads.pop(0)
    total = Client("test-only-not-a-secret").fetch_spend("2026-10-01", datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert total == Decimal("23.50")
    assert not payloads


@pytest.mark.parametrize("payload", [
    {}, {"data": [], "has_more": "false"}, page("NaN"), page("-1"), page(True),
    {"data": [{"results": [result("2", "eur")]}], "has_more": False},
    {"data": [{"results": [{}]}], "has_more": False},
    {"data": [{"results": None}], "has_more": False},
    page("2", more=True, cursor=None),
])
def test_malformed_costs_never_become_zero(payload):
    m = module()
    class Client(m.BudgetCostsClient):
        def _request_json(self, request):
            return payload
    with pytest.raises(openai_usage.OpenAIUsageError):
        Client("test-only").fetch_spend("2026-10-01", datetime(2026, 10, 31, tzinfo=timezone.utc))


def test_display_percent_is_cost_divided_by_configured_total_and_zero_is_measured():
    m = module()
    config = cfg.OpenAIBudgetConfig(True, "100.00", "2026-10-01")
    snap = m.BudgetSnapshot(config=config, spent=Decimal("23.50"), state="ok")
    assert snap.ai.primary_value == "23.5%"
    assert snap.ai.fields == (("USED", "$23.50"), ("TOTAL", "$100.00"))
    assert snap.ai.budget_ratio == .235
    assert m.BudgetSnapshot(config=config, spent=Decimal("123.45"), state="ok").ai.primary_value == "123.5%"
    assert m.BudgetSnapshot(config=config, spent=Decimal("0"), state="ok").ai.primary_value == "0%"
    assert m.BudgetSnapshot(config=config, state="network").ai.primary_value == "--"
    assert m.BudgetSnapshot(config=cfg.OpenAIBudgetConfig()).ai is None


def eventually(check):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.01)
    assert check()


def test_disabled_worker_never_reads_key_or_network_and_enabled_respects_refresh_interval():
    m = module()
    reads = []
    class Store:
        def get(self):
            reads.append(1)
            return "test-only"
    class Client:
        def __init__(self, key): pass
        def fetch_spend(self, start_date, now, **kwargs): return Decimal("23.50")
    service = m.OpenAIBudgetService(cfg.OpenAIBudgetConfig(), secret_store=Store(), client_factory=Client)
    try:
        assert not service.refresh()
        assert reads == []
        service.configure(cfg.OpenAIBudgetConfig(True, "100", "2026-10-01"))
        eventually(lambda: service.snapshot().state == "ok")
        assert service.snapshot().ai.primary_value == "23.5%"
        assert not service.refresh()
        assert len(reads) == 1
    finally:
        service.close()


def test_disabling_during_fetch_discards_old_account_result():
    m = module()
    entered, release = threading.Event(), threading.Event()
    class Store:
        def get(self): return "test-only"
    class Client:
        def __init__(self, key): pass
        def fetch_spend(self, start_date, now, **kwargs):
            entered.set()
            release.wait(3)
            return Decimal("23.50")
    service = m.OpenAIBudgetService(cfg.OpenAIBudgetConfig(True, "100", "2026-10-01"), secret_store=Store(), client_factory=Client)
    try:
        assert entered.wait(2)
        service.configure(cfg.OpenAIBudgetConfig())
        release.set()
        assert service.snapshot().ai is None
    finally:
        release.set()
        service.close()
    assert service.snapshot().ai is None


def test_disabling_interrupts_remaining_pagination():
    m = module()
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Store:
        def get(self): return "test-only"
    class Client(m.BudgetCostsClient):
        def _request_json(self, request):
            calls.append(request.full_url)
            if len(calls) == 1:
                entered.set()
                release.wait(3)
                return page("2", more=True, cursor="next")
            return page("3")
    service = m.OpenAIBudgetService(cfg.OpenAIBudgetConfig(True, "100", "2026-10-01"), secret_store=Store(), client_factory=Client)
    try:
        assert entered.wait(2)
        service.configure(cfg.OpenAIBudgetConfig())
        release.set()
    finally:
        release.set()
        service.close()
    assert len(calls) == 1


def test_malformed_page_and_bucket_envelopes_do_not_report_zero():
    m = module()
    for payload in (
        {"object": "error", "data": [], "has_more": False},
        {"object": "page", "data": [{"object": "not-a-bucket", "results": []}], "has_more": False},
        {"object": "page", "data": [{"object": "bucket", "start_time": True, "end_time": 1790899200, "results": []}], "has_more": False},
        {"object": "page", "data": [{"object": "bucket", "start_time": 1, "end_time": 2, "results": []}], "has_more": False},
    ):
        class Client(m.BudgetCostsClient):
            def _request_json(self, request): return payload
        with pytest.raises(openai_usage.OpenAIUsageError):
            Client("test-only").fetch_spend("2026-10-01", datetime(2026, 10, 31, tzinfo=timezone.utc))


def test_valid_empty_page_is_measured_zero():
    m = module()
    class Client(m.BudgetCostsClient):
        def _request_json(self, request): return {"object":"page", "data":[], "has_more":False, "next_page":None}
    assert Client("test-only").fetch_spend("2026-10-01", datetime(2026,10,31,tzinfo=timezone.utc)) == Decimal("0")


def test_subsequent_inflight_refresh_is_pending_not_old_measured_value(monkeypatch):
    from types import SimpleNamespace
    m = module()
    clock = [0.0]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Store:
        def get(self): return "test-only"
    class Client:
        def __init__(self, key): pass
        def fetch_spend(self, start_date, now, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                entered.set()
                release.wait(3)
            return Decimal("23.50")
    service = m.OpenAIBudgetService(cfg.OpenAIBudgetConfig(True, "100", "2026-10-01"), secret_store=Store(), client_factory=Client)
    try:
        eventually(lambda: service.snapshot().state == "ok")
        last_success = service.snapshot().last_success
        clock[0] = 601.0
        assert service.refresh()
        assert entered.wait(2)
        assert service.snapshot().state == "pending"
        assert service.snapshot().ai.primary_value == "--"
        assert service.snapshot().last_success == last_success
    finally:
        release.set()
        service.close()


@pytest.mark.parametrize("kind,state", [(openai_usage.APIErrorKind.UNAUTHORIZED,"auth"), (openai_usage.APIErrorKind.FORBIDDEN,"auth"), (openai_usage.APIErrorKind.RATE_LIMITED,"rate_limited"), (openai_usage.APIErrorKind.NETWORK,"network")])
def test_errors_do_not_leak_response_or_show_zero(kind, state):
    m = module()
    class Store:
        def get(self): return "test-only"
    class Client:
        def __init__(self, key): pass
        def fetch_spend(self, start_date, now, **kwargs): raise openai_usage.OpenAIUsageError(kind, "private-response-must-not-escape")
    service = m.OpenAIBudgetService(cfg.OpenAIBudgetConfig(True, "100", "2026-10-01"), secret_store=Store(), client_factory=Client)
    try:
        eventually(lambda: service.snapshot().state == state)
        assert service.snapshot().ai.primary_value == "--"
        assert "private-response" not in repr(service.snapshot())
    finally:
        service.close()
