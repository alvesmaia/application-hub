"""Host usage read from /proc (inside the hub container on Linux it is the host's): CPU from the difference between
two /proc/stat samples, memory as MemTotal - MemAvailable."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub.ops.host_usage import HostUsage, parse_meminfo, parse_stat

STAT_1 = "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 1 1 1\n"
STAT_2 = "cpu  150 0 150 750 150 0 0 0 0 0\ncpu0 1 1 1 1\n"  # +100 busy of +200 total
MEMINFO = "MemTotal:       16777216 kB\nMemFree:  1000 kB\nMemAvailable:   11534336 kB\n"


def test_parse_stat_counts_idle_and_iowait_as_not_busy() -> None:
    assert parse_stat(STAT_1) == (200, 1000)
    assert parse_stat("nonsense") is None


def test_parse_meminfo_used_gib() -> None:
    assert parse_meminfo(MEMINFO) == pytest.approx(5.0)
    assert parse_meminfo("MemTotal: 10 kB\n") is None


def test_host_usage_cpu_fraction_from_two_samples(tmp_path: Path) -> None:
    (tmp_path / "stat").write_text(STAT_1)
    (tmp_path / "meminfo").write_text(MEMINFO)
    usage = HostUsage(tmp_path, settle_s=0)
    usage.sample()  # first sample: no difference yet
    (tmp_path / "stat").write_text(STAT_2)
    cpu, mem = usage.sample()
    assert cpu == pytest.approx(0.5) and mem == pytest.approx(5.0)


def test_host_usage_without_proc_answers_none(tmp_path: Path) -> None:
    assert HostUsage(tmp_path / "missing", settle_s=0).sample() == (None, None)
