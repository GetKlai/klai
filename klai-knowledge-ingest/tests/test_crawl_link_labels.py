"""Link labels that the crawl4ai pruning filter drops must survive extraction.

PruningContentFilter judges a wrapper element (``<span>``, ``<strong>``) before its
children and removes it when it is mostly link text, which leaves
"Ga naar ." in fit_markdown while raw_markdown still has the label. The fixture
below is the output of crawl4ai 0.9.5 (the version on core-01) for the
synthetic HTML in the comment next to it.
"""

from __future__ import annotations

from knowledge_ingest.crawl_result_processing import extract_result

# <p>Ga naar <span><a href=".../instellingen">Instellingen</a></span> en kies ...</p>
# <p>Open <strong><a href=".../belplan">Belplan</a></strong> om de ...</p>
# <p>Klik op <a href=".../opslaan"><span>Opslaan</span></a> als je klaar bent ...</p>
_RAW = """# Telefoon instellen
Dit artikel legt uit hoe je de telefoon instelt voor gebruik op kantoor en onderweg met de app.
Ga naar [Instellingen](https://example.com/instellingen) en kies daar de optie voor je toestel.
Open **[Belplan](https://example.com/belplan)** om de doorschakeling te wijzigen voor je team.
Klik op [Opslaan](https://example.com/opslaan) als je klaar bent met de wijzigingen hierboven.
## Gerelateerde artikelen
  * [Telefoon koppelen](https://example.com/a)
  * [Voicemail instellen](https://example.com/b)
"""

_FIT = """# Telefoon instellen
Dit artikel legt uit hoe je de telefoon instelt voor gebruik op kantoor en onderweg met de app.
Ga naar en kies daar de optie voor je toestel.
Open om de doorschakeling te wijzigen voor je team.
Klik op [Opslaan](https://example.com/opslaan) als je klaar bent met de wijzigingen hierboven.
## Gerelateerde artikelen
"""


def _extract(fit: str, raw: str):
    return extract_result(
        "https://example.com/x", {"markdown": {"fit_markdown": fit, "raw_markdown": raw}}
    )


def test_labels_lost_by_pruning_come_back_from_raw_markdown() -> None:
    result = _extract(_FIT, _RAW)

    lines = result.fit_markdown.splitlines()
    assert _RAW.splitlines()[2] in lines  # Ga naar [Instellingen](...)
    assert _RAW.splitlines()[3] in lines  # Open **[Belplan](...)**
    assert "Ga naar en" not in result.fit_markdown


def test_only_label_gaps_are_filled_nothing_else_from_raw_comes_back() -> None:
    result = _extract(_FIT, _RAW)

    assert "Telefoon koppelen" not in result.fit_markdown  # list the filter removed stays removed
    assert len(result.fit_markdown.splitlines()) == len(_FIT.splitlines())
    assert result.raw_markdown == _RAW


def test_fit_without_gaps_is_returned_unchanged() -> None:
    result = _extract(_RAW, _RAW)

    assert result.fit_markdown == _RAW


def test_a_line_that_matches_two_different_raw_lines_is_left_alone() -> None:
    raw = "Ga naar [A](https://example.com/a) nu.\nGa naar [B](https://example.com/b) nu.\n"
    fit = "Ga naar nu.\n"

    assert _extract(fit, raw).fit_markdown == fit


def test_word_count_follows_the_restored_text() -> None:
    result = _extract("Ga naar en kies.", "Ga naar [Instellingen](https://example.com/i) en kies.")

    assert result.word_count == len(result.fit_markdown.split())


def test_a_line_that_also_occurs_without_a_link_is_left_alone() -> None:
    raw = "Open [Instellingen](https://example.com/i) nu.\nOpen nu.\n"
    fit = "Open nu.\nOpen nu.\n"

    assert _extract(fit, raw).fit_markdown == fit
