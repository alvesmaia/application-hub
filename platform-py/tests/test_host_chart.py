"""admin/runtime, host capacity: the last 5 minutes of CPU and memory as a stacked area (system, platform, apps; the top
is the total), with the 25/50/75/100% reference lines, and the value of each instant on hover."""

from __future__ import annotations

from hub.ops import host_chart as hc

NOW = 1_800_000_000.0


def test_history_keeps_five_minutes_and_one_sample_per_poll() -> None:
    h = hc.HostHistory()
    h.add(NOW - 400, (1, 0, 0), (1, 0, 0))  # older than the window
    h.add(NOW - 10, (1, 0, 0), (1, 0, 0))
    h.add(NOW - 8, (2, 0, 0), (2, 0, 0))  # same poll (another viewer): ignored
    h.add(NOW, (0.5, 0.25, 0.75), (1.0, 0.5, 0.5))
    assert [s[0] for s in h.window(NOW)] == [NOW - 10, NOW]


def test_the_area_stacks_the_parts_and_the_top_is_the_total() -> None:
    pts = [(NOW - 300, (1.0, 0.0, 1.0)), (NOW, (1.0, 1.0, 2.0))]
    c = hc.chart(pts, total=4.0, unit="vCPU", now=NOW)
    assert [cls for cls, _ in c["layers"]] == ["sys", "plat", "apps"]
    assert c["total"] == f"0.0,{hc.H / 2:.1f} {hc.W:.1f},0.0"  # 2 of 4 = middle, 4 of 4 = top
    assert c["grid"] == [hc.H * 0.75, hc.H * 0.5, hc.H * 0.25, 0.0]


def test_hover_shows_the_instant_with_total_and_parts() -> None:
    c = hc.chart([(NOW, (0.8, 0.4, 0.6))], total=4.0, unit="vCPU", now=NOW)
    title = c["slices"][0][2]
    assert "Total 1,80 / 4,00 vCPU" in title and "Sistema 0,80" in title and "Aplicações 0,60" in title


def test_scale_labels_of_the_bar() -> None:
    assert hc.scale(4.0, "vCPU") == ["0", "1", "2", "3", "4 vCPU"]
    assert hc.scale(7.6, "GiB") == ["0", "1,9", "3,8", "5,7", "7,6 GiB"]


def test_legend_of_the_capacity_panel_shows_each_part() -> None:
    from hub.ops.runtime_view import UsageBars, legend

    bars = UsageBars([], [], 1.8, 4.0, (0.8, 0.4, 0.6), (1.5, 1.0, 1.5))
    rows = legend(bars, cpu_total=4.0, mem_total=8.0)
    assert rows == [("sys", "Sistema", "0,80 vCPU · 1,5 GiB"), ("plat", "Plataforma", "0,40 vCPU · 1,0 GiB"),
                    ("apps", "Aplicações", "0,60 vCPU · 1,5 GiB"), ("free", "Livre", "2,20 vCPU · 4,0 GiB")]  # fmt: skip
