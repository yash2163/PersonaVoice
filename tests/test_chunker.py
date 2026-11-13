from app.chunker import SentenceChunker


def run(deltas, flush=True):
    c = SentenceChunker()
    out = []
    for d in deltas:
        out += c.feed(d)
    if flush:
        out += c.flush()
    return out


def test_empty_input():
    assert run([]) == []
    assert run([""]) == []
    assert run(["   \n  "]) == []


def test_first_sentence_emitted_as_soon_as_complete():
    c = SentenceChunker()
    assert c.feed("I build voice agents for a living. And ") == ["I build voice agents for a living."]


def test_boundary_needs_following_whitespace():
    c = SentenceChunker()
    assert c.feed("I build voice agents.") == []  # could still be "agents.Foo"
    assert c.feed(" Next") == ["I build voice agents."]


def test_decimal_is_not_boundary():
    out = run(["I spent 1.5 years at Minfy working on agents. It was great experience overall, honestly."])
    assert out[0].startswith("I spent 1.5 years at Minfy working on agents.")


def test_abbreviations_are_not_boundaries():
    for text in [
        "Things like e.g. retrieval and agents matter a lot to me. Second one here is longer than usual.",
        "I worked with Dr. Rao on a paper about retrieval. Second sentence goes here for length.",
        "I like tools, vs. frameworks, and so on etc. but mostly tools. Another sentence follows here.",
        "I studied in the U.S. for a short summer program last year. Then I came back home to India.",
    ]:
        out = run([text])
        assert out[0].count(".") >= 2, out


def test_short_first_sentence_merges_with_next():
    out = run(["Sure. I'd say my superpower is breaking vague problems into small shippable steps. Next one."])
    assert out[0] == "Sure. I'd say my superpower is breaking vague problems into small shippable steps."


def test_later_chunks_merge_to_40_chars():
    out = run(["First sentence is long enough to go. Yes. Fine. This one will finally be long enough to stand alone."])
    assert out[0] == "First sentence is long enough to go."
    assert out[1].startswith("Yes. Fine. This one")


def test_long_unbroken_text_splits_at_comma():
    text = "word " * 30 + "end, " + "more " * 40 + "tail"
    c = SentenceChunker()
    out = c.feed(text)
    assert out, "expected a split once >250 chars without a boundary"
    assert out[0].endswith(",")
    assert "".join(out + c.flush()).replace(" ", "") == text.replace(" ", "")


def test_long_unbroken_without_soft_break_splits_at_space():
    text = "word " * 80
    c = SentenceChunker()
    out = c.feed(text)
    assert out and len(out[0]) <= 255


def test_flush_returns_remainder_and_pending():
    c = SentenceChunker()
    assert c.feed("Hi. ") == []  # too short, held back to merge
    assert c.feed("and then trailing words") == []
    assert c.flush() == ["Hi. and then trailing words"]
    assert c.flush() == []


def test_newline_is_boundary():
    out = run(["This is the first line of the answer\nand this is the second line that follows it"])
    assert out == ["This is the first line of the answer", "and this is the second line that follows it"]


def test_ellipsis_is_not_boundary():
    out = run(["Right, so... let me think about how I would put this for you. Then more."])
    assert out[0].startswith("Right, so... let me think")


def test_token_by_token_matches_whole_text():
    text = "My superpower is learning fast. For example, at Minfy I picked up Bedrock in 2.5 weeks. That mattered a lot! Why? Because shipping early wins."
    whole = run([text])
    streamed = run(list(text))
    assert streamed == whole


def test_exclamation_and_question():
    out = run(["That is a really great question to ask! Here is how I think about it? Honestly it depends."])
    assert out[0] == "That is a really great question to ask!"
