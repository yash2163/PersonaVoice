"""Server-side safety nets for model habits the prompt alone doesn't reliably stop."""
import re

# A leading acknowledgement ("Sure, ...", "Yeah. ...", "Ha, ...").
_OPENER = re.compile(r"^\s*(?:sure|yeah|yep|yes|okay|ok|well|ha|haha|absolutely|great question)\s*[,.!:\u2014\u2013]+\s*", re.I)


class OpenerFilter:
    """Strips a leading filler word from a streamed answer.

    Holds back the first few characters (about one token's worth of delay) so the opener can be
    recognised, then passes everything through untouched.
    """

    HOLD_CHARS = 16

    def __init__(self) -> None:
        self._buf = ""
        self._released = False

    def feed(self, delta: str) -> str:
        if self._released:
            return delta
        self._buf += delta
        return self._release() if len(self._buf) >= self.HOLD_CHARS else ""

    def flush(self) -> str:
        return "" if self._released else self._release()

    def _release(self) -> str:
        self._released = True
        text = self._buf
        self._buf = ""
        m = _OPENER.match(text)
        if m and len(text) > m.end():  # never reduce the whole answer to nothing
            rest = text[m.end():]
            text = rest[:1].upper() + rest[1:]
        return text
