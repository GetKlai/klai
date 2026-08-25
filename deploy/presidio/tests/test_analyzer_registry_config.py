"""Integration test: builds the actual analyzer engine from
deploy/presidio/analyzer/conf/analyzer.yaml + sitecustomize.py's registration
and NLP-engine patch, the same wiring the Dockerfile bakes into the image
(SPEC-PRIVACY-MISTRAL-PII-001 Phase 1, AC-1, AC-2, and the REQ-2
language-agnosticism guarantee end to end).

Needs `spacy` (a presidio-analyzer dependency; spacy.blank() pipelines need no
model download — no network). No Docker: this exercises the same
`AnalyzerEngineProvider` call app.py makes, just in-process. The strongest
version of this same check — proving the image's actual sitecustomize.py
auto-load and env-var wiring work, not just the equivalent Python call — is
run against the real built image in CI
(.github/workflows/presidio-analyzer-image-build.yml's "Test recognizer pack
against the built image" step) and was verified manually against a running
container (see the PR description / task report) before this file was
written; that path can't run in a plain pytest job without Docker, so this
file is the offline-CI-safe equivalent of the same assertions.
"""

from __future__ import annotations

import os
from pathlib import Path

import conftest  # noqa: F401  (adds ../analyzer to sys.path)
import pytest

spacy = pytest.importorskip("spacy")

import sitecustomize  # noqa: E402  (import for side effect: registers recognizers + patches SpacyNlpEngine)
from presidio_analyzer import AnalyzerEngineProvider  # noqa: E402

# Inside the real image, ANALYZER_CONF_FILE (and the two empty-string
# overrides — see Dockerfile's comment for why they must be falsy on this
# presidio-analyzer version) are already set, so this uses the exact
# baked-in production config. Outside the image (plain local/CI run against
# a repo checkout), fall back to the repo-relative path with the same
# falsy overrides the Dockerfile sets.
_REPO_CONF_FILE = str(Path(__file__).resolve().parent.parent / "analyzer" / "conf" / "analyzer.yaml")

# The one path every check in this file must use. Inside the image only
# `tests/` is mounted, so `_REPO_CONF_FILE` does not exist there — reading it
# unconditionally broke collection in the "Test recognizer pack against the
# built image" step while passing locally. Resolve it the same way the engine
# fixture does, so the assertions run against whichever config the engine was
# actually built from.
_CONF_FILE = os.environ.get("ANALYZER_CONF_FILE") or _REPO_CONF_FILE


@pytest.fixture(scope="module")
def engine():
    provider = AnalyzerEngineProvider(
        analyzer_engine_conf_file=_CONF_FILE,
        nlp_engine_conf_file=os.environ.get("NLP_CONF_FILE", ""),
        recognizer_registry_conf_file=os.environ.get("RECOGNIZER_REGISTRY_CONF_FILE", ""),
    )
    return provider.create_engine()


class TestConfigLoadsAsIntended:
    def test_supported_languages_match_yaml(self, engine):
        """Read the YAML rather than restate it.

        This test is named "match_yaml" but hard-coded {en, nl, de}, so
        adding fr/es/pt to the config broke it as a literal mismatch rather
        than telling us anything about the engine. Derived from the file, it
        pins what the name claims: whatever the config declares is what the
        engine actually serves.
        """
        import yaml

        declared = yaml.safe_load(Path(_CONF_FILE).read_text(encoding="utf-8"))
        assert set(engine.supported_languages) == set(declared["supported_languages"])

    def test_every_supported_language_has_an_nlp_model_entry(self, engine):
        """A language in `supported_languages` with no `nlp_configuration`
        model entry fails when that language is first requested, not at
        startup — so the config looks fine until someone calls /analyze with
        it."""
        import yaml

        declared = yaml.safe_load(Path(_CONF_FILE).read_text(encoding="utf-8"))
        modelled = {m["lang_code"] for m in declared["nlp_configuration"]["models"]}
        assert set(declared["supported_languages"]) == modelled

    def test_klai_entities_are_registered(self, engine):
        entities = set(engine.get_supported_entities(language="en"))
        assert entities == {
            "NL_BSN",
            "NL_KVK",
            "NL_BTW",
            "NL_POSTCODE",
            "SECRET",
            "CREDIT_CARD",
            "IBAN_CODE",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
        }
        # PERSON is deliberately absent: REQ-2 says PERSON is GLiNER's job in
        # a later phase, not a byproduct of this pack. SpacyRecognizer is
        # explicitly disabled in conf/analyzer.yaml for exactly this reason.
        assert "PERSON" not in entities

    def test_no_spacy_model_is_loaded(self, engine):
        # REQ-2: every configured language pipeline is spacy.blank(), not a
        # trained model. A blank pipeline has no pipeline components at all.
        nlp_engine = engine.nlp_engine
        for lang in engine.supported_languages:
            pipeline = nlp_engine.get_nlp(lang)
            assert pipeline.pipe_names == [], (lang, pipeline.pipe_names)


def _configured_languages() -> list[str]:
    """Read the languages from the config the engine is built from.

    Restating them as a literal is how the acceptance and end-to-end tests
    would silently stop covering a language added to the YAML: the config
    tests would still pass, and nothing would actually exercise it.
    """
    import yaml

    declared = yaml.safe_load(Path(_CONF_FILE).read_text(encoding="utf-8"))
    return list(declared["supported_languages"])


_CONFIGURED_LANGUAGES = _configured_languages()


class TestAC1LanguageAccepted:
    @pytest.mark.parametrize("language", _CONFIGURED_LANGUAGES)
    def test_language_is_accepted_not_a_language_error(self, engine, language):
        # AC-1's actual assertion ("200, not a language error") at the
        # analyzer-engine level: analyze() must not raise for any configured
        # language.
        result = engine.analyze(text="hello", language=language)
        assert isinstance(result, list)


class TestLanguageAgnosticismEndToEnd:
    @pytest.mark.parametrize("language", _CONFIGURED_LANGUAGES)
    def test_bsn_detected_identically_through_full_engine(self, engine, language):
        sentences = {
            "en": "Please note my BSN is 111222333 for the application.",
            "nl": "Let op, mijn BSN is 111222333 voor de aanvraag.",
            "de": "Bitte beachten Sie, meine BSN ist 111222333 für den Antrag.",
            "fr": "Veuillez noter que mon BSN est 111222333 pour la demande.",
            "es": "Tenga en cuenta que mi BSN es 111222333 para la solicitud.",
            "pt": "Observe que o meu BSN é 111222333 para o pedido.",
        }
        text = sentences[language]
        results = engine.analyze(text=text, language=language)
        bsn_results = [r for r in results if r.entity_type == "NL_BSN"]
        assert len(bsn_results) == 1
        assert text[bsn_results[0].start : bsn_results[0].end] == "111222333"
        assert bsn_results[0].score == 1.0

    def test_invalid_bsn_not_detected_through_full_engine(self, engine):
        results = engine.analyze(text="My BSN is 111222334.", language="en")
        assert [r for r in results if r.entity_type == "NL_BSN"] == []
