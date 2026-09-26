"""The overlay's text policy. The AppKit panel is exercised by running it."""

from talkie.ui.overlay import (
    LISTENING,
    MAX_CHARS,
    WORKING,
    Overlay,
    caption,
    trim,
)


def test_nothing_heard_yet_says_so_rather_than_going_blank():
    """An empty strip looks like a failure; it is only that nothing has come
    back yet."""
    assert caption("") == LISTENING
    assert caption("   ") == LISTENING
    assert caption(None) == LISTENING


def test_a_one_shot_clip_waiting_on_the_request_says_something_different():
    assert caption("", state="working") == WORKING


def test_text_wins_over_any_placeholder():
    assert caption("hello there", state="working") == "hello there"


def test_surrounding_whitespace_is_dropped():
    assert caption("  hello \n") == "hello"


def test_a_short_transcript_is_shown_whole():
    assert trim("the quick brown fox", 100) == "the quick brown fox"


def test_a_long_transcript_keeps_its_tail():
    """The newest words are the ones being checked — a caption showing the
    start of a two-minute hold would be useless."""
    text = "word " * 200
    out = trim(text.strip(), 40)
    assert out.startswith("… ")
    assert out.endswith("word")
    assert len(out) <= 44


def test_trimming_never_cuts_a_word_in_half():
    """A half-word reads as an error in the transcript, which is the one thing
    this window exists to rule out."""
    text = "alpha bravo charlie delta echo foxtrot golf hotel"
    out = trim(text, 20)
    body = out.removeprefix("… ")
    assert body in text
    assert text.split(body)[0].endswith(" ")


def test_a_single_word_longer_than_the_limit_is_still_shown():
    out = trim("x" * 300, 50)
    assert out.startswith("… ")
    assert len(out) == 52


def test_the_default_limit_is_applied_through_caption():
    assert len(caption("y" * 500)) == MAX_CHARS + 2


# -- when the strip comes down --------------------------------------------


class Panel:
    """Enough NSPanel for the show/finish/settle policy, with no AppKit."""

    def __init__(self):
        self.visible = False

    def orderFrontRegardless(self):
        self.visible = True

    def orderOut_(self, _):
        self.visible = False


def bare_overlay():
    """An Overlay whose AppKit objects are stubs — the policy is what is tested,
    and the panel itself is verified by running the app."""
    overlay = Overlay.__new__(Overlay)
    overlay._panel = Panel()
    overlay._label = type("Label", (), {"setStringValue_": lambda self, t: None})()
    overlay._visible = False
    overlay._finishing = False
    overlay._token = 0
    overlay._reposition = lambda: None
    return overlay


def test_settle_hides_a_strip_that_never_produced_a_transcript():
    """A tap too short to transcribe: nothing else would ever take it down."""
    overlay = bare_overlay()
    overlay._apply_show(LISTENING)
    overlay._apply_settle()
    assert overlay._panel.visible is False


def test_settle_leaves_a_finished_transcript_up_to_be_read():
    overlay = bare_overlay()
    overlay._apply_show(LISTENING)
    overlay._apply_finish("all done")
    overlay._apply_settle()
    assert overlay._panel.visible is True


def test_the_next_dictation_clears_the_fading_one():
    overlay = bare_overlay()
    overlay._apply_finish("all done")
    overlay._apply_show(LISTENING)
    overlay._apply_settle()
    assert overlay._panel.visible is False


def test_a_stale_fade_never_hides_the_dictation_that_followed_it():
    """The linger timer from clip one must not fire over clip two."""
    overlay = bare_overlay()
    overlay._apply_finish("first")
    stale = overlay._token
    overlay._apply_show(LISTENING)
    overlay._expire(stale)
    assert overlay._panel.visible is True
