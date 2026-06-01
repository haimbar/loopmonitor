import pytest

from loopmonitor._report import format_break, format_peek

SAMPLE = {
    "pid": 42,
    "iteration": 300,
    "total": 1000,
    "elapsed_sec": 90,   # 1m 30s
    "eta_sec": 210,      # 3m 30s
    "tracked": {"loss": 0.25, "acc": 0.91},
}


# ── peek ────────────────────────────────────────────────────────────────────

def test_peek_contains_pid():
    assert "42" in format_peek(SAMPLE)


def test_peek_contains_iteration_and_total():
    out = format_peek(SAMPLE)
    assert "300" in out
    assert "1000" in out


def test_peek_contains_percentage():
    assert "30.0%" in format_peek(SAMPLE)


def test_peek_contains_elapsed_formatted():
    assert "01:30" in format_peek(SAMPLE)  # 90 s = 1 m 30 s


def test_peek_contains_eta_formatted():
    assert "03:30" in format_peek(SAMPLE)  # 210 s = 3 m 30 s


def test_peek_contains_all_tracked_keys():
    out = format_peek(SAMPLE)
    assert "loss=0.25" in out
    assert "acc=0.91" in out


def test_peek_without_total_shows_question_mark():
    s = {**SAMPLE, "total": None, "eta_sec": None}
    out = format_peek(s)
    assert "?" in out


def test_peek_with_empty_tracked_does_not_raise():
    s = {**SAMPLE, "tracked": {}}
    format_peek(s)  # must not raise


# ── break ────────────────────────────────────────────────────────────────────

def test_break_contains_stopping():
    assert "Stopping" in format_break(SAMPLE)


def test_break_includes_peek_header():
    # format_break builds on format_peek — pid should appear
    assert "42" in format_break(SAMPLE)


# ── time formatting ──────────────────────────────────────────────────────────

def test_time_hours_format():
    s = {**SAMPLE, "elapsed_sec": 3661, "eta_sec": 7322}
    out = format_peek(s)
    assert "1:01:01" in out   # 3661 s = 1 h 1 m 1 s
    assert "2:02:02" in out   # 7322 s = 2 h 2 m 2 s


def test_time_days_format():
    s = {**SAMPLE, "elapsed_sec": 90061, "eta_sec": None}
    out = format_peek(s)
    assert "1d" in out        # 90061 s > 1 day


def test_time_unknown_shows_question_mark():
    s = {**SAMPLE, "elapsed_sec": None, "eta_sec": None}
    out = format_peek(s)
    assert out.count("?") >= 2
