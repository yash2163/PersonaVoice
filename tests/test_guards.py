from app.guards import OpenerFilter


def run(deltas):
    f = OpenerFilter()
    return "".join(f.feed(d) for d in deltas) + f.flush()


def test_strips_filler_opener():
    assert run(["Sure, I'd say it's about staying calm."]) == "I'd say it's about staying calm."
    assert run(["Yeah. That is a fair question to ask me."]) == "That is a fair question to ask me."
    assert run(["Ha, nice try! I stay in character here."]) == "Nice try! I stay in character here."


def test_works_when_opener_is_split_across_tokens():
    assert run(["S", "ure", ",", " I would say", " it depends."]) == "I would say it depends."


def test_leaves_real_answers_alone():
    for t in ["I'm from Chhattisgarh and grew up there.", "Surely that matters, and I think so too.", "Well-known tools matter to me."]:
        assert run([t]) == t


def test_never_empties_an_answer():
    assert run(["Yes."]) == "Yes."
    assert run(["Okay!"]) == "Okay!"


def test_short_answer_flushes():
    assert run(["Sure. Done."]) == "Done."
