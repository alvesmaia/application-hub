"""`.form-field input{width:100%}` (styles.css) also stretched checkboxes and radios: in an app's form the box
grew to the whole row with the label pushed to the far right. The shell keeps them at their own size."""

from __future__ import annotations

from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "../platform/assets/shell/hub.css").read_text("utf-8")


def test_checkbox_and_radio_keep_their_size_inside_form_fields() -> None:
    rule = ".form-field :is(input[type=checkbox],input[type=radio]){width:16px;height:16px;min-height:0;padding:0;margin:0;flex:0 0 auto}"
    assert rule in CSS
