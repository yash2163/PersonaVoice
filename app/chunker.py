"""Streaming sentence splitter.

feed(delta) -> list[str] returns sentences completed so far; flush() returns the rest.

Rules (see SPEC.md):
- Boundary: '.', '!' or '?' followed by whitespace, plus any newline.
- Not a boundary: '.' between digits, ellipses, or after abbreviations (e.g., i.e., vs., etc., Dr., ...).
- First chunk goes out as soon as its sentence completes; merged with the next if < 12 chars.
- Later chunks merge until >= 40 chars so audio is not choppy.
- > 250 chars with no boundary: split at the last comma / semicolon / dash.
"""
import re

FIRST_MIN_CHARS = 12
LATER_MIN_CHARS = 40
MAX_UNBROKEN_CHARS = 250

_TERMINATORS = ".!?"
_CLOSERS = "\"')]”’"
_ABBREVIATIONS = {"e.g.", "i.e.", "vs.", "etc.", "dr.", "mr.", "ms.", "mrs.", "prof.", "u.s."}
_SOFT_BREAK = re.compile(r"[,;—–]|\s-\s|--")


class SentenceChunker:
    def __init__(self) -> None:
        self._buf = ""
        self._pending = ""  # completed sentence(s) held back for merging
        self._emitted = 0

    # -- public API ---------------------------------------------------------
    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        for sentence in self._extract():
            self._accept(sentence, out)
        self._split_long(out)
        return out

    def flush(self) -> list[str]:
        out: list[str] = []
        for sentence in self._extract():
            self._accept(sentence, out)
        tail = self._buf.strip()
        self._buf = ""
        rest = " ".join(p for p in (self._pending, tail) if p)
        self._pending = ""
        if rest:
            out.append(rest)
            self._emitted += 1
        return out

    # -- internals ----------------------------------------------------------
    def _accept(self, sentence: str, out: list[str]) -> None:
        merged = f"{self._pending} {sentence}".strip() if self._pending else sentence
        minimum = FIRST_MIN_CHARS if self._emitted == 0 else LATER_MIN_CHARS
        if len(merged) >= minimum:
            out.append(merged)
            self._emitted += 1
            self._pending = ""
        else:
            self._pending = merged

    def _extract(self) -> list[str]:
        """Pull every *complete* sentence off the front of the buffer."""
        buf, n = self._buf, len(self._buf)
        sentences: list[str] = []
        start = i = 0
        while i < n:
            c = buf[i]
            if c == "\n":
                seg = buf[start:i].strip()
                if seg:
                    sentences.append(seg)
                start = i = i + 1
                continue
            if c in _TERMINATORS:
                j = i
                while j < n and buf[j] in _TERMINATORS:
                    j += 1
                k = j
                while k < n and buf[k] in _CLOSERS:
                    k += 1
                if k >= n:
                    break  # can't tell yet: need the next character
                if buf[k].isspace() and self._is_boundary(buf[start:j], buf[i:j]):
                    seg = buf[start:k].strip()
                    if seg:
                        sentences.append(seg)
                    start = i = k
                    continue
                i = k
                continue
            i += 1
        self._buf = buf[start:]
        return sentences

    @staticmethod
    def _is_boundary(text_through_run: str, run: str) -> bool:
        if len(run) > 1 and set(run) == {"."}:
            return False  # ellipsis: keep going
        if run == ".":
            tokens = text_through_run.split()
            token = tokens[-1].lstrip("\"'([").lower() if tokens else ""
            if token in _ABBREVIATIONS:
                return False
        return True

    def _split_long(self, out: list[str]) -> None:
        while len(self._buf) > MAX_UNBROKEN_CHARS:
            window = self._buf[:MAX_UNBROKEN_CHARS]
            cut = -1
            for m in _SOFT_BREAK.finditer(window):
                cut = m.end()
            if cut <= 0:
                cut = window.rfind(" ") + 1
            if cut <= 0:
                cut = MAX_UNBROKEN_CHARS
            piece, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if piece:
                self._accept(piece, out)
