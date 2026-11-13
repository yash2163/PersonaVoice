import asyncio

from app.tts import PollyTTS, to_ssml


def test_plain_text_is_wrapped_and_escaped():
    assert to_ssml("Tom & Jerry <3") == "<speak>Tom &amp; Jerry &lt;3</speak>"


def test_rate_and_pauses():
    out = to_ssml("First, then second. Done", rate_pct=90, comma_pause_ms=120, end_pause_ms=350)
    assert out == (
        '<speak><prosody rate="90%">First,<break time="120ms"/> then second. Done'
        '<break time="350ms"/></prosody></speak>'
    )


def test_numbers_with_commas_are_not_split():
    assert "<break" not in to_ssml("It cost 1,000 rupees", comma_pause_ms=120)


class _Client:
    def __init__(self):
        self.calls = []

    def synthesize_speech(self, **kw):
        self.calls.append(kw)

        class _S:
            def read(self_inner):
                return b"mp3"

        return {"AudioStream": _S()}


def test_polly_sends_ssml_and_can_skip_the_end_pause():
    client = _Client()
    tts = PollyTTS(client, "Matthew", "neural", {}, rate_pct=90, comma_pause_ms=100, sentence_pause_ms=300)
    assert asyncio.run(tts.synth("Hello there.")) == b"mp3"
    assert asyncio.run(tts.synth("Hmm.", pause_after=False)) == b"mp3"
    assert client.calls[0]["TextType"] == "ssml"
    assert 'time="300ms"' in client.calls[0]["Text"]
    assert "300ms" not in client.calls[1]["Text"]
