"""Whether a model's verbatim quote really stands in the text it claims to quote.

A model that has to point at the words its judgement rests on can still copy
words that are not there, so code checks the copy before the judgement counts.
Shared by the selection step (a quoted sentence of a passage) and the case
(the visitor's own words behind a contact wish or an out-of-scope subject).
"""

from __future__ import annotations

import re

WORD = re.compile(r"\w+")
# A copied sentence is accepted when four fifths of its words stand in the
# text: the model drops markup and mends a broken link text, and a looser bar
# would let through a quote that was never there.
QUOTE_WORD_SHARE = 0.8
# A quote of one or two words ("ja graag", "medewerker", a phone number) has no
# room for a dropped or mended word, so it must stand as one phrase.
_SHORT_QUOTE_WORDS = 3


def words_stand_in(quote: str, text: str) -> bool:
    """The quote's words stand in the text in this order and close together.

    A word-share check accepts a quote whose words lie scattered over three
    sentences, so the words must follow each other within twice the quote's
    length (markup and a mended link text may sit in between).
    """
    needles = WORD.findall(quote.lower())
    words = WORD.findall(text.lower())
    if not needles:
        return False
    if len(needles) < _SHORT_QUOTE_WORDS:
        return any(words[start : start + len(needles)] == needles for start in range(len(words)))
    for start, word in enumerate(words):
        if word != needles[0]:
            continue
        position, matched = start, 0
        for needle in needles:
            while position < len(words) and words[position] != needle:
                position += 1
            if position >= len(words) or position - start > 2 * len(needles):
                break
            matched += 1
            position += 1
        if matched >= QUOTE_WORD_SHARE * len(needles):
            return True
    return False
