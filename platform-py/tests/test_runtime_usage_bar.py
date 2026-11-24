"""Host bar on /admin/runtime shows the measured usage: platform and apps from the containers' own numbers, the
system as the rest of the host's usage, free what is left; every segment's hover says value / total (share)."""

from __future__ import annotations

from hub.ops import runtime as rt
from hub.ops.runtime_view import usage_bars


def _host() -> rt.Host:
    return rt.Host(8, 16)


def test_segments_split_the_measured_usage() -> None:
    bars = usage_bars(_host(), (1.3 / 8, 5.2), plat=(0.48, 1.76), apps=(0.5, 1.2))
    cpu = {lbl.split(":")[0]: (flex, lbl) for flex, cls, lbl in bars.cpu}
    assert cpu["Sistema"][1] == "Sistema: 0,32 / 8,00 vCPU (4,0%)"
    assert cpu["Plataforma"][1] == "Plataforma: 0,48 / 8,00 vCPU (6,0%)"
    assert cpu["Aplicações"][1] == "Aplicações: 0,50 / 8,00 vCPU (6,2%)"
    assert cpu["Livre"][1] == "Livre: 6,70 / 8,00 vCPU (83,8%)"
    mem = [lbl for _, _, lbl in bars.mem]
    assert mem[0] == "Sistema: 2,24 / 16,00 GiB (14,0%)" and mem[3] == "Livre: 10,80 / 16,00 GiB (67,5%)"
    assert (bars.cpu_used, bars.mem_used) == (1.3, 5.2)


def test_no_host_reading_uses_the_containers_sum() -> None:
    bars = usage_bars(_host(), (None, None), plat=(0.5, 1.0), apps=(0.5, 1.0))
    assert bars.cpu[0][2] == "Sistema: 0,00 / 8,00 vCPU (0,0%)" and bars.cpu_used == 1.0


def test_containers_not_measured_show_the_usage_without_detail() -> None:
    bars = usage_bars(_host(), (0.25, 4.0), plat=None, apps=None)
    assert bars.cpu[0][2].startswith("2,00 / 8,00 vCPU")  # numbers only (owner's call), no "Sistema"
    assert [lbl.split(":")[0] for _, _, lbl in bars.cpu][1:3] == ["Plataforma", "Aplicações"]
    assert bars.cpu[1][0] == bars.cpu[2][0] == "flex:0.0000"


def test_rounding_never_shows_a_negative_share() -> None:
    """Measured system usage = host usage minus the containers: float noise left "-0,00 / 16,00 vCPU (-0,0%)"."""
    from hub.ops.runtime_view import _seg

    for v in (-5.6e-17, -0.0):
        assert _seg(v, 16.0, "ops-seg sys", "Sistema", "vCPU") == (
            "flex:0.0000",
            "ops-seg sys",
            "Sistema: 0,00 / 16,00 vCPU (0,0%)",
        )
