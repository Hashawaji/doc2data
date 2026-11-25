from importlib import import_module

import pytest

from doc2data.extractors import EXTRACTORS, ExtractionError, get_extractor
from doc2data.extractors.rules import RulesExtractor


def test_names_are_matched_without_regard_to_case_or_stray_spaces():
    assert isinstance(get_extractor(" Rules "), RulesExtractor)


def test_an_unknown_name_is_an_extraction_error_that_lists_the_choices():
    with pytest.raises(ExtractionError, match="Unknown extractor 'gemni'. Choose one of: rules, gemini"):
        get_extractor("gemni")


def test_every_registered_extractor_exists_under_its_own_name():
    for name, (module, cls) in EXTRACTORS.items():
        assert getattr(import_module(f"doc2data.extractors.{module}"), cls).name == name
