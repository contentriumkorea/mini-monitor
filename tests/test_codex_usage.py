# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import ai_mini_monitor.ai.codex_usage as codex_usage
from ai_mini_monitor.ai.codex_usage import (
    CodexUsageSnapshot,
    CodexUsageProvider,
    CodexUsageStatus,
    FILE_ATTRIBUTE_REPARSE_POINT,
    _is_reparse_or_symlink,
    _is_within,
    to_ai_data,
)
from ai_mini_monitor.models import AIProviderKind, SyncStatus


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _rate(
    window_minutes: int = 300,
    *,
    used_percent: float | None = 25.0,
    remaining_percent: float | None = None,
    resets_at: float = 1_786_368_600,
) -> dict[str, object]:
    output: dict[str, object] = {
        "window_minutes": window_minutes,
        "resets_at": resets_at,
    }
    if used_percent is not None:
        output["used_percent"] = used_percent
    if remaining_percent is not None:
        output["remaining_percent"] = remaining_percent
    return output


def _event(
    updated_at: datetime,
    *,
    primary: object | None = None,
    secondary: object | None = None,
    **extra: object,
) -> dict[str, object]:
    rate_limits: dict[str, object] = {}
    if primary is not None:
        rate_limits["primary"] = primary
    if secondary is not None:
        rate_limits["secondary"] = secondary
    return {
        "timestamp": updated_at.isoformat().replace("+00:00", "Z"),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": rate_limits,
            "output": extra.pop("payload_output", "ignored output"),
            "path": extra.pop("payload_path", "ignored path content"),
        },
        "prompt": extra.pop("prompt", "ignored prompt"),
        **extra,
    }


def _write_lines(path: Path, values: list[object], *, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_consent_gate_does_not_resolve_or_scan_sessions(tmp_path, monkeypatch) -> None:
    def fail_scan(_root: Path):
        raise AssertionError("sessions must not be scanned before consent")

    monkeypatch.setattr(codex_usage, "_scan_jsonl_files", fail_scan)
    provider = CodexUsageProvider(
        consent_granted=False,
        sessions_root=tmp_path / "never-read",
    )

    snapshot = provider.refresh(NOW)

    assert snapshot.status is CodexUsageStatus.CONSENT_REQUIRED
    assert snapshot.source_path is None
    assert snapshot.updated_at is None


@pytest.mark.parametrize("use_codex_home", [True, False])
def test_environment_root_resolution_stays_in_injected_temp_tree(tmp_path, use_codex_home) -> None:
    codex_home = tmp_path / "codex-home"
    user_profile = tmp_path / "profile"
    chosen = codex_home / "sessions" if use_codex_home else user_profile / ".codex" / "sessions"
    event_file = chosen / "2026" / "session.jsonl"
    _write_lines(event_file, [_event(NOW - timedelta(minutes=1), primary=_rate())])
    environment = {"USERPROFILE": str(user_profile)}
    if use_codex_home:
        environment["CODEX_HOME"] = str(codex_home)

    snapshot = CodexUsageProvider(
        consent_granted=True,
        environ=environment,
    ).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.source_path == event_file.resolve()


def test_windows_profile_with_spaces_and_unicode_uses_default_codex_home(tmp_path) -> None:
    user_profile = tmp_path / "Je Yun 한글 계정"
    event_file = (
        user_profile
        / ".codex"
        / "sessions"
        / "2026"
        / "08"
        / "11"
        / "rollout-다른-PC.jsonl"
    )
    _write_lines(
        event_file,
        [
            _event(
                NOW - timedelta(minutes=1),
                primary=_rate(used_percent=18),
                secondary=_rate(10_080, used_percent=35),
            )
        ],
    )

    snapshot = CodexUsageProvider(
        consent_granted=True,
        environ={"USERPROFILE": str(user_profile)},
    ).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.source_path == event_file.resolve()
    assert snapshot.five_hour_remaining_percent == 82
    assert snapshot.seven_day_remaining_percent == 65


def test_custom_codex_home_takes_precedence_over_a_populated_user_profile(tmp_path) -> None:
    custom_home = tmp_path / "D drive style" / "codex-state"
    profile_home = tmp_path / "profile" / ".codex"
    custom_event = custom_home / "sessions" / "nested" / "custom.jsonl"
    profile_event = profile_home / "sessions" / "profile.jsonl"
    _write_lines(custom_event, [_event(NOW, primary=_rate(used_percent=12))])
    _write_lines(profile_event, [_event(NOW, primary=_rate(used_percent=91))])

    snapshot = CodexUsageProvider(
        consent_granted=True,
        environ={
            "CODEX_HOME": str(custom_home),
            "USERPROFILE": str(tmp_path / "profile"),
        },
    ).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.source_path == custom_event.resolve()
    assert snapshot.five_hour_remaining_percent == 88


def test_explicit_missing_codex_home_does_not_read_the_profile_fallback(tmp_path) -> None:
    missing_custom_home = tmp_path / "missing-custom-home"
    profile_event = tmp_path / "profile" / ".codex" / "sessions" / "profile.jsonl"
    _write_lines(profile_event, [_event(NOW, primary=_rate(used_percent=4))])

    snapshot = CodexUsageProvider(
        consent_granted=True,
        environ={
            "CODEX_HOME": str(missing_custom_home),
            "USERPROFILE": str(tmp_path / "profile"),
        },
    ).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.SESSIONS_NOT_FOUND
    assert snapshot.source_path is None


def test_latest_event_timestamp_wins_even_when_its_file_mtime_is_older(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    latest_event = sessions / "older-mtime.jsonl"
    older_event = sessions / "newer-mtime.jsonl"
    _write_lines(
        latest_event,
        [_event(NOW - timedelta(minutes=1), primary=_rate(used_percent=15))],
        mtime=100,
    )
    _write_lines(
        older_event,
        [_event(NOW - timedelta(minutes=2), primary=_rate(used_percent=0))],
        mtime=200,
    )

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.source_path == latest_event.resolve()
    assert snapshot.five_hour_remaining_percent == 85.0


def test_named_spark_limit_does_not_replace_general_plan_usage(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    general_event = _event(
        NOW - timedelta(minutes=2),
        primary=_rate(10_080, used_percent=15),
    )
    general_event["payload"]["rate_limits"].update(
        {"limit_name": None, "plan_type": "pro"}
    )
    spark_event = _event(
        NOW - timedelta(minutes=1),
        primary=_rate(10_080, used_percent=0),
    )
    spark_event["payload"]["rate_limits"].update(
        {"limit_name": "GPT-5.3-Codex-Spark", "plan_type": None}
    )
    _write_lines(sessions / "general.jsonl", [general_event], mtime=100)
    _write_lines(sessions / "spark.jsonl", [spark_event], mtime=200)

    snapshot = CodexUsageProvider(
        consent_granted=True,
        sessions_root=sessions,
    ).refresh(NOW)
    display = to_ai_data(snapshot, NOW)

    assert snapshot.source_path == (sessions / "general.jsonl").resolve()
    assert snapshot.seven_day_remaining_percent == 85.0
    assert display.primary_value == "85%"
    assert display.primary_label == "7D LEFT"


def test_only_named_limits_are_not_reported_as_general_plan_usage(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    spark_event = _event(NOW, primary=_rate(10_080, used_percent=0))
    spark_event["payload"]["rate_limits"].update(
        {"limit_name": "GPT-5.3-Codex-Spark", "plan_type": None}
    )
    _write_lines(sessions / "spark.jsonl", [spark_event])

    snapshot = CodexUsageProvider(
        consent_granted=True,
        sessions_root=sessions,
    ).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.NO_RATE_LIMITS
    assert snapshot.source_path is None


def test_named_limit_at_end_of_same_file_does_not_hide_prior_general_event(
    tmp_path,
) -> None:
    sessions = tmp_path / "sessions"
    general_event = _event(
        NOW - timedelta(minutes=2),
        primary=_rate(10_080, used_percent=15),
    )
    general_event["payload"]["rate_limits"].update(
        {"limit_name": None, "plan_type": "pro"}
    )
    spark_event = _event(
        NOW - timedelta(minutes=1),
        primary=_rate(10_080, used_percent=0),
    )
    spark_event["payload"]["rate_limits"].update(
        {"limit_name": "GPT-5.3-Codex-Spark", "plan_type": None}
    )
    event_file = sessions / "mixed.jsonl"
    _write_lines(event_file, [general_event, spark_event])

    snapshot = CodexUsageProvider(
        consent_granted=True,
        sessions_root=sessions,
    ).refresh(NOW)

    assert snapshot.source_path == event_file.resolve()
    assert snapshot.seven_day_remaining_percent == 85.0
    assert snapshot.updated_at == NOW - timedelta(minutes=2)


@pytest.mark.parametrize("same_file", [False, True])
def test_partial_general_windows_are_merged_without_replacing_weekly_usage(
    tmp_path,
    same_file,
) -> None:
    sessions = tmp_path / "sessions"
    weekly_event = _event(
        NOW - timedelta(minutes=2),
        primary=_rate(10_080, used_percent=15),
    )
    short_event = _event(
        NOW - timedelta(minutes=1),
        primary=_rate(300, used_percent=0),
    )
    weekly_path = sessions / "weekly.jsonl"
    if same_file:
        _write_lines(weekly_path, [weekly_event, short_event])
    else:
        _write_lines(weekly_path, [weekly_event], mtime=200)
        _write_lines(sessions / "short.jsonl", [short_event], mtime=100)

    snapshot = CodexUsageProvider(
        consent_granted=True,
        sessions_root=sessions,
    ).refresh(NOW)
    display = to_ai_data(snapshot, NOW)

    assert snapshot.source_path == weekly_path.resolve()
    assert snapshot.five_hour_remaining_percent == 100.0
    assert snapshot.seven_day_remaining_percent == 85.0
    assert snapshot.updated_at == NOW - timedelta(minutes=2)
    assert display.primary_value == "85%"
    assert display.primary_label == "7D LEFT"
    assert display.fields[0] == ("5H LEFT", "100%")


def test_future_timestamp_does_not_replace_current_general_usage(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    current_path = sessions / "current.jsonl"
    _write_lines(
        current_path,
        [_event(NOW - timedelta(minutes=1), primary=_rate(10_080, used_percent=15))],
        mtime=100,
    )
    _write_lines(
        sessions / "future.jsonl",
        [_event(NOW + timedelta(hours=1), primary=_rate(10_080, used_percent=0))],
        mtime=200,
    )

    snapshot = CodexUsageProvider(
        consent_granted=True,
        sessions_root=sessions,
    ).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.source_path == current_path.resolve()
    assert snapshot.seven_day_remaining_percent == 85.0
    assert snapshot.updated_at == NOW - timedelta(minutes=1)


def test_stale_five_hour_detail_is_not_paired_with_current_weekly_usage(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    _write_lines(
        sessions / "weekly.jsonl",
        [_event(NOW - timedelta(minutes=1), primary=_rate(10_080, used_percent=15))],
        mtime=100,
    )
    _write_lines(
        sessions / "old-short.jsonl",
        [_event(NOW - timedelta(hours=1), primary=_rate(300, used_percent=0))],
        mtime=200,
    )

    snapshot = CodexUsageProvider(
        consent_granted=True,
        sessions_root=sessions,
    ).refresh(NOW)
    display = to_ai_data(snapshot, NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.five_hour_remaining_percent is None
    assert snapshot.seven_day_remaining_percent == 85.0
    assert display.primary_value == "85%"
    assert all(label != "5H LEFT" for label, _value in display.fields)


def test_recent_event_beats_newer_file_with_only_stale_events(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    stale_new_file = sessions / "newer.jsonl"
    recent_old_file = sessions / "older.jsonl"
    _write_lines(
        stale_new_file,
        [_event(NOW - timedelta(hours=1), primary=_rate(used_percent=10))],
        mtime=200,
    )
    _write_lines(
        recent_old_file,
        [_event(NOW - timedelta(minutes=14), primary=_rate(used_percent=30))],
        mtime=100,
    )

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.source_path == recent_old_file.resolve()
    assert snapshot.five_hour_remaining_percent == 70.0


def test_strict_shape_maps_used_and_remaining_for_5h_and_7d(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    event_file = sessions / "session.jsonl"
    reset_5h = 1_786_368_600
    reset_7d = 1_786_973_400
    value = _event(
        NOW - timedelta(seconds=30),
        primary=_rate(300, used_percent=25.5, resets_at=reset_5h),
        secondary=_rate(
            10_080,
            used_percent=None,
            remaining_percent=42.25,
            resets_at=reset_7d,
        ),
        prompt="private prompt that must not escape",
        payload_output="private output that must not escape",
        payload_path="private event path that must not escape",
    )
    _write_lines(event_file, [value])

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.five_hour_remaining_percent == 74.5
    assert snapshot.seven_day_remaining_percent == 42.25
    assert snapshot.five_hour_reset_at == datetime.fromtimestamp(reset_5h, timezone.utc)
    assert snapshot.seven_day_reset_at == datetime.fromtimestamp(reset_7d, timezone.utc)
    assert snapshot.updated_at == NOW - timedelta(seconds=30)
    rendered = repr(snapshot)
    assert "private prompt" not in rendered
    assert "private output" not in rendered
    assert "private event path" not in rendered


@pytest.mark.parametrize("reset_key", ["reset_at", "resets_at"])
def test_both_observed_reset_field_spellings_are_supported(tmp_path, reset_key) -> None:
    sessions = tmp_path / "sessions"
    rate = _rate(used_percent=10)
    reset = rate.pop("resets_at")
    rate[reset_key] = reset
    _write_lines(sessions / "session.jsonl", [_event(NOW, primary=rate, secondary=None)])

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.five_hour_remaining_percent == 90
    assert snapshot.five_hour_reset_at == datetime.fromtimestamp(float(reset), timezone.utc)


def test_null_or_invalid_secondary_does_not_hide_a_valid_primary(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    _write_lines(
        sessions / "session.jsonl",
        [
            _event(
                NOW,
                primary=_rate(used_percent=22),
                secondary={"window_minutes": 10_080, "used_percent": "bad"},
            )
        ],
    )

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.five_hour_remaining_percent == 78
    assert snapshot.seven_day_remaining_percent is None


def test_wrong_event_shapes_and_invalid_numbers_are_skipped(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    event_file = sessions / "session.jsonl"
    valid = _event(NOW - timedelta(minutes=2), primary=_rate(used_percent=40))
    invalid: list[object] = [
        {"type": "response_item", "payload": {"type": "token_count", "rate_limits": {}}},
        {"timestamp": NOW.isoformat(), "type": "event_msg", "payload": {"type": "other", "rate_limits": {}}},
        {"timestamp": NOW.isoformat(), "type": "event_msg", "rate_limits": {"primary": _rate()}},
        _event(NOW, primary=_rate(used_percent=True)),
        _event(NOW, primary=_rate(used_percent=float("nan"))),
        _event(NOW, primary=_rate(used_percent=101)),
        _event(NOW, primary={**_rate(), "window_minutes": "300"}),
        _event(NOW, primary=_rate(resets_at=float("inf"))),
        _event(NOW, primary=_rate(resets_at=1e20)),
        _event(NOW, primary=_rate(resets_at=10**1000)),
        _event(NOW, primary=_rate(used_percent=25, remaining_percent=75)),
    ]
    _write_lines(event_file, [valid, *invalid])

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.five_hour_remaining_percent == 60.0
    assert snapshot.updated_at == NOW - timedelta(minutes=2)


def test_invalid_utf8_and_partial_last_line_do_not_hide_previous_event(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    event_file = sessions / "session.jsonl"
    event_file.parent.mkdir(parents=True)
    valid = json.dumps(_event(NOW - timedelta(minutes=1), primary=_rate())).encode()
    event_file.write_bytes(valid + b"\n\xff\xfe\n{\"timestamp\":\"partial")

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.five_hour_remaining_percent == 75.0


def test_file_and_line_limits_skip_oversized_input(tmp_path, monkeypatch) -> None:
    sessions = tmp_path / "sessions"
    oversized_file = sessions / "new.jsonl"
    valid_file = sessions / "old.jsonl"
    _write_lines(valid_file, [_event(NOW - timedelta(minutes=2), primary=_rate(used_percent=35))], mtime=100)
    oversized_file.write_bytes(b"x" * 513)
    os.utime(oversized_file, (300, 300))
    monkeypatch.setattr(codex_usage, "MAX_FILE_BYTES", 512)

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.source_path == valid_file.resolve()
    assert snapshot.five_hour_remaining_percent == 65.0

    monkeypatch.setattr(codex_usage, "MAX_FILE_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(codex_usage, "MAX_LINE_BYTES", 512)
    valid_line = json.dumps(_event(NOW - timedelta(minutes=1), primary=_rate(used_percent=15))).encode()
    oversized_line_file = sessions / "line.jsonl"
    oversized_line_file.write_bytes(valid_line + b"\n" + b"x" * 513 + b"\n")
    os.utime(oversized_line_file, (400, 400))

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.source_path == oversized_line_file.resolve()
    assert snapshot.five_hour_remaining_percent == 85.0


def test_only_regular_files_are_candidates_and_empty_states_are_explicit(tmp_path) -> None:
    missing = tmp_path / "missing"
    missing_snapshot = CodexUsageProvider(
        consent_granted=True,
        sessions_root=missing,
    ).refresh(NOW)
    assert missing_snapshot.status is CodexUsageStatus.SESSIONS_NOT_FOUND

    sessions = tmp_path / "sessions"
    (sessions / "directory.jsonl").mkdir(parents=True)
    empty_snapshot = CodexUsageProvider(
        consent_granted=True,
        sessions_root=sessions,
    ).refresh(NOW)
    assert empty_snapshot.status is CodexUsageStatus.NO_RATE_LIMITS


def test_root_escape_and_reparse_helpers_reject_unsafe_paths(tmp_path) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    inside = root / "inside.jsonl"
    outside = (tmp_path / "outside.jsonl").resolve()
    assert _is_within(root, inside)
    assert not _is_within(root, outside)

    class ReparseStat:
        st_file_attributes = FILE_ATTRIBUTE_REPARSE_POINT

    assert _is_reparse_or_symlink(inside, ReparseStat())


def test_symlinked_root_is_refused_when_platform_allows_symlinks(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable for this account")

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=linked).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.UNAVAILABLE


def test_display_conversion_prioritizes_seven_day_remaining_and_keeps_five_hour_secondary() -> None:
    snapshot = CodexUsageSnapshot(
        status=CodexUsageStatus.OK,
        five_hour_remaining_percent=74.5,
        seven_day_remaining_percent=42.25,
        five_hour_reset_at=NOW + timedelta(hours=1, minutes=20),
        seven_day_reset_at=NOW + timedelta(days=3, hours=2),
        updated_at=NOW - timedelta(seconds=30),
    )

    display = to_ai_data(snapshot, NOW)

    assert display.provider is AIProviderKind.CODEX_LOCAL
    assert display.status is SyncStatus.OK
    assert display.primary_value == "42%"
    assert display.primary_label == "7D LEFT"
    assert display.fields == (
        ("5H LEFT", "74%"),
        ("7D RESET", "3D 2H"),
    )
    assert display.budget_ratio == pytest.approx(0.5775)
    assert display.budget_label == "7D USED 58%"
    assert all(label != "5H RESET" for label, _value in display.fields)


def test_display_conversion_uses_seven_day_remaining_when_five_hour_is_absent() -> None:
    snapshot = CodexUsageSnapshot(
        status=CodexUsageStatus.OK,
        seven_day_remaining_percent=82,
        updated_at=NOW,
    )

    display = to_ai_data(snapshot, NOW)

    assert display.primary_value == "82%"
    assert display.primary_label == "7D LEFT"
    assert display.fields == (("7D RESET", "--"),)
    assert display.budget_ratio == pytest.approx(0.18)
    assert display.budget_label == "7D USED 18%"


@pytest.mark.parametrize(
    ("status", "primary", "sync"),
    [
        (CodexUsageStatus.CONSENT_REQUIRED, "CONSENT", SyncStatus.SETUP_REQUIRED),
        (CodexUsageStatus.SESSIONS_NOT_FOUND, "NO DATA", SyncStatus.SETUP_REQUIRED),
        (CodexUsageStatus.NO_RATE_LIMITS, "NO DATA", SyncStatus.SETUP_REQUIRED),
        (CodexUsageStatus.UNAVAILABLE, "UNAVAILABLE", SyncStatus.DELAYED),
    ],
)
def test_display_conversion_has_explicit_safe_empty_states(status, primary, sync) -> None:
    display = to_ai_data(CodexUsageSnapshot(status=status), NOW)
    assert display.primary_value == primary
    assert display.status is sync
    assert display.last_sync is None


def test_stale_fallback_remains_available_but_is_mapped_delayed(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    event_file = sessions / "stale.jsonl"
    _write_lines(
        event_file,
        [_event(NOW - timedelta(minutes=16), primary=_rate(used_percent=31))],
    )

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)
    display = to_ai_data(snapshot, NOW)

    assert snapshot.status is CodexUsageStatus.STALE
    assert snapshot.five_hour_remaining_percent == 69
    assert display.status is SyncStatus.DELAYED
    assert display.error_detail == "stale local rate-limit snapshot"
    assert str(event_file.resolve()) not in repr(display)


def test_scan_caps_keep_only_newest_files_and_report_truncation(tmp_path, monkeypatch) -> None:
    sessions = tmp_path / "sessions"
    files = []
    for index in range(4):
        path = sessions / f"session-{index}.jsonl"
        _write_lines(
            path,
            [_event(NOW - timedelta(minutes=index + 1), primary=_rate(used_percent=index))],
            mtime=100 + index,
        )
        files.append(path)
    monkeypatch.setattr(codex_usage, "MAX_CANDIDATE_FILES", 2)

    root = codex_usage._safe_root(sessions)
    assert root is not None
    candidates, incomplete = codex_usage._scan_jsonl_files(root)
    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert incomplete is True
    assert len(candidates) == 2
    assert [candidate.path for candidate in candidates] == [files[3].resolve(), files[2].resolve()]
    assert snapshot.source_path == files[2].resolve()
    assert snapshot.five_hour_remaining_percent == 98.0
    assert snapshot.status is CodexUsageStatus.STALE


@pytest.mark.parametrize(
    ("constant", "nested_parts"),
    [
        ("MAX_SCAN_ENTRIES", ("event.jsonl",)),
        ("MAX_SCAN_DEPTH", ("one", "two", "event.jsonl")),
        ("MAX_SCAN_SECONDS", ("event.jsonl",)),
    ],
)
def test_entry_depth_and_time_scan_budgets_fail_closed(
    tmp_path,
    monkeypatch,
    constant,
    nested_parts,
) -> None:
    sessions = tmp_path / "sessions"
    event_file = sessions.joinpath(*nested_parts)
    _write_lines(event_file, [_event(NOW, primary=_rate())])
    monkeypatch.setattr(codex_usage, constant, -1 if constant == "MAX_SCAN_SECONDS" else 0)

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.UNAVAILABLE
    assert snapshot.source_path is None


def test_total_read_budget_is_global_and_fails_closed(tmp_path, monkeypatch) -> None:
    sessions = tmp_path / "sessions"
    _write_lines(sessions / "event.jsonl", [_event(NOW, primary=_rate())])
    monkeypatch.setattr(codex_usage, "MAX_TOTAL_READ_BYTES", 8)

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.UNAVAILABLE
    assert snapshot.source_path is None


def test_reverse_reader_crosses_tiny_blocks_without_filewide_split(tmp_path, monkeypatch) -> None:
    sessions = tmp_path / "sessions"
    event_file = sessions / "event.jsonl"
    valid = json.dumps(_event(NOW - timedelta(minutes=1), primary=_rate(used_percent=9))).encode()
    event_file.parent.mkdir(parents=True)
    event_file.write_bytes(valid + b"\n" + (b"\n" * 10_000) + b"{\"partial\":")
    monkeypatch.setattr(codex_usage, "READ_BLOCK_BYTES", 17)

    snapshot = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert snapshot.status is CodexUsageStatus.OK
    assert snapshot.five_hour_remaining_percent == 91
    source = Path(codex_usage.__file__).read_text(encoding="utf-8")
    assert ".splitlines(" not in source


def test_file_replacement_after_scan_fails_identity_revalidation(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    event_file = sessions / "event.jsonl"
    _write_lines(event_file, [_event(NOW, primary=_rate(used_percent=10))])
    root = codex_usage._safe_root(sessions)
    assert root is not None
    candidates, incomplete = codex_usage._scan_jsonl_files(root)
    assert not incomplete and len(candidates) == 1

    replacement = sessions / "replacement.tmp"
    replacement.write_text(
        json.dumps(_event(NOW, primary=_rate(used_percent=90))) + "\n",
        encoding="utf-8",
    )
    os.replace(replacement, event_file)
    event, rejected = codex_usage._latest_event_in_file(
        candidates[0],
        root,
        NOW,
        codex_usage._ReadBudget(codex_usage.MAX_TOTAL_READ_BYTES),
        codex_usage.time.monotonic() + 1,
    )

    assert event is None
    assert rejected is True


@pytest.mark.skipif(os.name != "nt", reason="Windows final-handle verification")
def test_windows_final_handle_is_confined_to_the_scanned_root(tmp_path) -> None:
    sessions = (tmp_path / "sessions").resolve()
    event_file = sessions / "event.jsonl"
    _write_lines(event_file, [_event(NOW, primary=_rate())])
    descriptor = os.open(
        event_file,
        os.O_RDONLY | getattr(os, "O_BINARY", 0),
    )
    try:
        assert codex_usage._windows_final_path(descriptor) == event_file.resolve()
        assert codex_usage._opened_path_is_within(descriptor, sessions)
        assert not codex_usage._opened_path_is_within(
            descriptor,
            (tmp_path / "different-root").resolve(),
        )
    finally:
        os.close(descriptor)


def test_final_handle_check_and_unexpected_path_error_are_fail_closed(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    sessions = tmp_path / "sessions"
    _write_lines(sessions / "event.jsonl", [_event(NOW, primary=_rate())])
    monkeypatch.setattr(codex_usage, "_opened_path_is_within", lambda *_args: False)

    rejected = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert rejected.status is CodexUsageStatus.UNAVAILABLE
    assert rejected.source_path is None

    private_text = "C:/Users/private/.codex/sessions/secret.jsonl"

    def fail_scan(*_args, **_kwargs):
        raise OSError(private_text)

    monkeypatch.setattr(codex_usage, "_scan_jsonl_files", fail_scan)
    safe = CodexUsageProvider(consent_granted=True, sessions_root=sessions).refresh(NOW)

    assert safe.status is CodexUsageStatus.UNAVAILABLE
    assert private_text not in repr(safe)
    assert private_text not in caplog.text
