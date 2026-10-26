"""Small text utilities used across the services."""

from __future__ import annotations

import html
import math
import re
from collections import Counter
from html.parser import HTMLParser

STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before being
    below between both but by can could did do does doing down during each etc few for from further had has
    have having he her here hers herself him himself his how i if in into is it its itself just me more
    most my myself no nor not now of off on once only or other our ours ourselves out over own same she
    should so some such than that the their theirs them themselves then there these they this those through
    to too under until up very was we were what when where which while who whom why will with would you
    your yours yourself yourselves us via per within across using use used able ability work working
    must should nice plus strong good great excellent experience experienced years year yrs role team
    teams company job candidate candidates looking join opportunity responsibilities requirements required
    preferred including include includes well new etc e.g i.e like need needs based help make sure
    knowledge understanding skills skill familiarity proficiency proficient hands-on hands minimum least
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*[a-z0-9+#]|[a-z0-9]")


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    """Convert (possibly entity-escaped) HTML to readable plain text."""
    if not value:
        return ""
    if "&lt;" in value and "<" not in value:
        value = html.unescape(value)
    if "<" not in value:
        return clean_whitespace(html.unescape(value))
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return clean_whitespace("".join(parser.parts))


def clean_whitespace(value: str) -> str:
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in value.replace("\r", "").split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln == "•":
            continue
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def tokenize(value: str) -> list[str]:
    return [t for t in _WORD_RE.findall(value.lower()) if t not in STOPWORDS and not t.isdigit()]


def norm_key(value: str) -> str:
    """Normalization for dedupe keys and fuzzy equality."""
    value = value.lower()
    value = re.sub(r"\b(pvt|private|ltd|limited|inc|llc|llp|corp|corporation|co|technologies|solutions)\b\.?", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def numbers_in(value: str) -> set[str]:
    """Numeric tokens, used to stop the model inventing metrics."""
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", value)}


class TfIdf:
    """Tiny TF-IDF over a corpus; enough for ranking a few thousand postings."""

    def __init__(self, documents: list[list[str]]):
        self.n_docs = max(1, len(documents))
        df: Counter[str] = Counter()
        for doc in documents:
            df.update(set(doc))
        self.idf = {t: math.log((1 + self.n_docs) / (1 + c)) + 1 for t, c in df.items()}
        self._default_idf = math.log(1 + self.n_docs) + 1

    def vector(self, tokens: list[str]) -> dict[str, float]:
        if not tokens:
            return {}
        tf = Counter(tokens)
        vec = {t: (1 + math.log(c)) * self.idf.get(t, self._default_idf) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    @staticmethod
    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(t, 0.0) for t, v in a.items())
