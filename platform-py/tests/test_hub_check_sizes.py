"""File-size ratchet of `hub-check` (AGENTS.md "Code size"): no new file over the limit, no baselined file grows."""

from __future__ import annotations

from hub_check import MAX_FILE_LINES, oversized


def test_new_file_over_the_limit_fails_and_small_files_pass() -> None:
    assert oversized({"a.py": MAX_FILE_LINES + 1, "b.py": MAX_FILE_LINES}, {}) == [
        f"a.py: {MAX_FILE_LINES + 1} lines (limit {MAX_FILE_LINES})"
    ]


def test_baselined_file_may_shrink_but_not_grow() -> None:
    assert oversized({"big.py": 700}, {"big.py": 800}) == []
    assert oversized({"big.py": 801}, {"big.py": 800}) == [
        "big.py: 801 lines (baseline 800); split it, do not grow it"
    ]


def test_baseline_entry_must_go_once_the_file_fits_or_disappears() -> None:
    assert oversized({"big.py": 400}, {"big.py": 800}) == [
        "big.py: fits now; remove it from size-baseline.json"
    ]
    assert oversized({}, {"gone.py": 800}) == ["gone.py: fits now; remove it from size-baseline.json"]
