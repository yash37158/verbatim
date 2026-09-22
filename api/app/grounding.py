"""Quote verification: the mechanism that makes groundedness a property of the system.

The model returns a quote to support each claim. We locate that quote inside the passage
it cited and return the *source's* own characters, not the model's. Matching is tolerant
of whitespace, smart quotes and case, because a false negative silently deletes a true
citation; but what the user is shown is always copied out of the document.
"""

import re

_WHITESPACE = re.compile(r"\s+")
_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", "…": "...",
}


def _fold(text: str) -> tuple[str, list[int]]:
    """Normalised text plus, for each normalised character, its offset in the original."""
    out: list[str] = []
    index: list[int] = []
    at_space = True
    for i, ch in enumerate(text):
        folded = _FOLD.get(ch, ch)
        if folded.isspace():
            if at_space:
                continue
            out.append(" ")
            index.append(i)
            at_space = True
        else:
            for c in folded.lower():
                out.append(c)
                index.append(i)
            at_space = False
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def normalize(text: str) -> str:
    return _fold(text)[0]


def locate(quote: str, source: str) -> tuple[int, int] | None:
    """Character span of `quote` within `source`, or None if the model did not quote it."""
    needle = normalize(quote)
    if not needle:
        return None
    haystack, index = _fold(source)
    at = haystack.find(needle)
    if at == -1:
        return None
    return index[at], index[at + len(needle) - 1] + 1


def verify(quote: str, source: str) -> str | None:
    """The document's own wording for `quote`, or None when the quote is not in the source."""
    span = locate(quote, source)
    return source[span[0] : span[1]] if span else None
