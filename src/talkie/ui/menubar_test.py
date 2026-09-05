"""Label logic. The AppKit shell around it is proven by running the app."""

from talkie.app import ERROR, IDLE, RECORDING, TRANSCRIBING
from talkie.ui.menubar import icon_for, stats_label, status_label


def test_every_state_has_its_own_icon():
    icons = [icon_for(s) for s in (IDLE, RECORDING, TRANSCRIBING, ERROR)]
    assert len(set(icons)) == 4


def test_an_unknown_state_falls_back_to_idle():
    assert icon_for("nonsense") == icon_for(IDLE)
    assert status_label("nonsense") == status_label(IDLE)


def test_status_labels_are_human():
    assert status_label(RECORDING) == "Status: Recording…"
    assert status_label(ERROR) == "Status: Last attempt failed"


def test_stats_label_reads_naturally():
    assert stats_label({"clips": 14, "seconds": 372, "cost": 0.0123}) == (
        "Today: 14 clips · 6.2 min · $0.01"
    )


def test_stats_label_singular():
    assert "1 clip ·" in stats_label({"clips": 1, "seconds": 3, "cost": 0.0})


def test_sub_cent_days_do_not_render_as_zero():
    """$0.00 would read as free; it isn't, and the point is showing the cost."""
    assert "<$0.01" in stats_label({"clips": 3, "seconds": 30, "cost": 0.0004})


def test_empty_history_says_so():
    assert stats_label({}) == "Today: nothing yet"
    assert stats_label({"clips": 0, "seconds": 0, "cost": 0}) == "Today: nothing yet"


def test_failures_are_surfaced():
    label = stats_label({"clips": 5, "seconds": 60, "cost": 0.01, "failures": 2})
    assert label.endswith("2 failed")


def test_a_missing_cost_does_not_explode():
    assert stats_label({"clips": 2, "seconds": 20, "cost": None})
