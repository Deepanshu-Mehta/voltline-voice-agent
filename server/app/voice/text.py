"""Text helpers for speech: sentence chunking for streaming TTS, tool fillers, and trimming a
reply to the words the listener actually heard."""

from __future__ import annotations

import re

# Abbreviations whose full stop does not end a sentence.
_ABBREV = ("a.m.", "p.m.", "e.g.", "i.e.", "mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "no.")
_CAN_END = ("a.m.", "p.m.", "etc.")
_BOUNDARY = re.compile(r"[.!?](?:[\"')\]]*)\s")

FILLERS = {
    "get_order_status": "Let me look up that order.",
    "check_stock": "Let me check stock for you.",
    "get_price": "Let me check the price.",
    "book_callback": "Booking that callback now.",
    "create_support_ticket": "I'll open a ticket for you.",
    "search_knowledge_base": "Let me look that up.",
}
DEFAULT_FILLER = "One moment."


class SentenceChunker:
    """Collects streamed LLM tokens and releases whole sentences, so TTS starts on the first
    sentence while the LLM is still writing the rest. `min_chars` avoids sending tiny fragments
    such as "Sure." on their own when a longer clause follows quickly; 0 sends every sentence."""

    def __init__(self, min_chars: int = 0):
        self.buffer = ""
        self.min_chars = min_chars

    def push(self, text: str) -> list[str]:
        self.buffer += text
        out: list[str] = []
        start = 0
        for m in _BOUNDARY.finditer(self.buffer):
            end = m.end()
            candidate = self.buffer[start:end]
            word = candidate.rstrip().split(" ")[-1].lower()
            if word in _ABBREV:
                # "7 p.m. Anything else?" ends a sentence; "9 a.m. to 7" does not. Decide on the
                # next word's capital letter, and wait for it if it has not streamed in yet.
                nxt = self.buffer[end:].lstrip()
                if word not in _CAN_END or not nxt or not nxt[0].isupper():
                    if word in _CAN_END and not nxt:
                        break
                    continue
            if len(candidate.strip()) < self.min_chars:
                continue
            out.append(candidate)
            start = end
        self.buffer = self.buffer[start:]
        return [s for s in out if s.strip()]

    def flush(self) -> str:
        rest, self.buffer = self.buffer, ""
        return rest


def filler_for(tool_name: str) -> str:
    return FILLERS.get(tool_name, DEFAULT_FILLER)


def heard_text(words: list[str], starts_s: list[float], played_ms: float) -> str:
    """The words whose audio had started playing when playback stopped."""
    kept = [w for w, s in zip(words, starts_s) if s * 1000 < played_ms]
    return " ".join(kept)


# A transcript ending on one of these words is almost certainly mid-sentence ("Do the cameras
# work with ..."), so the turn waits longer before ending.
_INCOMPLETE_TAIL = set(
    "a an the and or but so to of for with about from in on at by my your our their this that these those "
    "is are was were do does can could would should will if because like than um uh".split()
)


def sounds_unfinished(text: str) -> bool:
    """Speech-to-text punctuates fragments too ("Do the cameras work with?"), so the last word
    decides, whatever punctuation follows it."""
    words = text.strip().lower().split()
    return bool(words) and words[-1].strip(".,?!;:") in _INCOMPLETE_TAIL


_SPEECH_SAFE = str.maketrans({"\u202f": " ", "\u00a0": " ", "\u2009": " ", "\u2011": "-", "\u2010": "-"})


def clean_for_speech(text: str) -> str:
    """Narrow no-break spaces and non-breaking hyphens (common in LLM output) -> plain ones."""
    return text.translate(_SPEECH_SAFE)
