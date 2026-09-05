"""``data/scripts/snippets.json`` schema round-trip (component doc §5.1/§7)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from engine.core.schemas.snippets_schema import SnippetsSchemaError, validate_snippets_config

SNIPPETS_PATH = Path(__file__).parent.parent.parent / "data" / "scripts" / "snippets.json"


@pytest.fixture
def example_config() -> dict:
    with SNIPPETS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_example_file_round_trips(example_config):
    validate_snippets_config(example_config)  # must not raise


def test_missing_schema_version_rejected(example_config):
    del example_config["schema_version"]
    with pytest.raises(SnippetsSchemaError):
        validate_snippets_config(example_config)


def test_unknown_category_rejected(example_config):
    example_config["categories"] = list(example_config["categories"]) + ["not_a_real_category"]
    with pytest.raises(SnippetsSchemaError):
        validate_snippets_config(example_config)


def test_snippet_with_bad_category_rejected(example_config):
    config = copy.deepcopy(example_config)
    config["snippets"][0]["category"] = "not_in_categories_list"
    with pytest.raises(SnippetsSchemaError):
        validate_snippets_config(config)


def test_snippet_missing_lua_template_marker_rejected(example_config):
    config = copy.deepcopy(example_config)
    config["snippets"][0]["lua_template"] = "engine.log_message('no markers here')"
    with pytest.raises(SnippetsSchemaError):
        validate_snippets_config(config)


def test_duplicate_snippet_id_rejected(example_config):
    config = copy.deepcopy(example_config)
    config["snippets"].append(copy.deepcopy(config["snippets"][0]))
    with pytest.raises(SnippetsSchemaError):
        validate_snippets_config(config)


def test_param_missing_default_rejected(example_config):
    config = copy.deepcopy(example_config)
    del config["snippets"][1]["params"][0]["default"]
    with pytest.raises(SnippetsSchemaError):
        validate_snippets_config(config)


def test_param_bad_type_rejected(example_config):
    config = copy.deepcopy(example_config)
    config["snippets"][1]["params"][0]["type"] = "not_a_real_type"
    with pytest.raises(SnippetsSchemaError):
        validate_snippets_config(config)


def test_not_an_object_rejected():
    with pytest.raises(SnippetsSchemaError):
        validate_snippets_config([1, 2, 3])
