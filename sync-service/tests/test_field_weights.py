"""Unit tests for VectorStoreConfig.text_fields union type + FtsConfig.match_mode/use_omw — Phase 23 Plan 01.

Tests cover:
- list[str] text_fields normalizes to {field: 1.0} dict (backward compat)
- dict[str, float] text_fields stored as-is (per-field weights)
- empty list/dict default → empty dict
- FtsConfig defaults: language='en', match_mode='and', use_omw=False
- FtsConfig match_mode='or' parses correctly
- FtsConfig invalid match_mode raises ValidationError
- FtsConfig use_omw=True parses to bool True
- FtsConfig extra keys ignored (model_config extra="ignore")
- Backward compat: existing config.yaml with list text_fields and no match_mode still parses
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Import helpers (allows test to be discovered even before implementation exists)
# ---------------------------------------------------------------------------

def _import_settings():
    """Import VectorStoreConfig and FtsConfig, patching out the module-level
    settings singleton (which calls load_config() and requires config.yaml).

    The module-level `settings = load_config()` line in settings.py runs at
    import time. In the test environment there is no config.yaml, so we patch
    _CONFIG_PATH to a non-existent path and catch the resulting error using
    the fact that pydantic model classes are defined BEFORE the singleton line.

    Strategy: patch open() just for the singleton call, or import via
    importlib after injecting the path stub.
    """
    import sys
    import importlib
    from pathlib import Path
    from unittest.mock import patch, MagicMock

    # Evict cached module so re-import runs the module body fresh
    for key in list(sys.modules):
        if key == "config.settings" or key == "config":
            del sys.modules[key]

    # Patch load_config so the module-level `settings = load_config()` succeeds
    # without needing a real config.yaml. The AppConfig model must still parse.
    dummy_raw = {
        "source": {"type": "csv"},
        "vector_store": {"collection": "Test", "text_fields": [], "search_mode": "hybrid"},
        "embedding": {"type": "weaviate_builtin"},
    }
    with patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=dummy_raw):
        import config.settings as _settings_mod
        VectorStoreConfig = _settings_mod.VectorStoreConfig
        FtsConfig = _settings_mod.FtsConfig

    return VectorStoreConfig, FtsConfig


# ---------------------------------------------------------------------------
# VectorStoreConfig.text_fields — normalization tests
# ---------------------------------------------------------------------------

class TestFieldWeightsConfig:
    def test_list_normalized_to_dict_with_weight_one(self):
        """list[str] text_fields → {field: 1.0 for each field} (backward compat)."""
        VectorStoreConfig, _ = _import_settings()
        cfg = VectorStoreConfig(text_fields=["a", "b"])
        assert cfg.text_fields == {"a": 1.0, "b": 1.0}

    def test_dict_stored_as_is(self):
        """dict[str, float] text_fields stored without modification."""
        VectorStoreConfig, _ = _import_settings()
        cfg = VectorStoreConfig(text_fields={"description": 1.0, "tags": 0.5})
        assert cfg.text_fields == {"description": 1.0, "tags": 0.5}

    def test_empty_default(self):
        """VectorStoreConfig() with no text_fields → empty dict."""
        VectorStoreConfig, _ = _import_settings()
        cfg = VectorStoreConfig()
        assert cfg.text_fields == {}

    def test_empty_list_normalizes_to_empty_dict(self):
        """Empty list text_fields → empty dict (not empty list)."""
        VectorStoreConfig, _ = _import_settings()
        cfg = VectorStoreConfig(text_fields=[])
        assert cfg.text_fields == {}


# ---------------------------------------------------------------------------
# FtsConfig — match_mode + use_omw tests
# ---------------------------------------------------------------------------

class TestFtsConfigMatchMode:
    def test_defaults(self):
        """FtsConfig() defaults: language='en', match_mode='and', use_omw=False."""
        _, FtsConfig = _import_settings()
        cfg = FtsConfig()
        assert cfg.language == "en"
        assert cfg.match_mode == "and"
        assert cfg.use_omw is False

    def test_match_mode_or(self):
        """FtsConfig(match_mode='or') stores 'or'."""
        _, FtsConfig = _import_settings()
        cfg = FtsConfig(match_mode="or")
        assert cfg.match_mode == "or"

    def test_match_mode_invalid_raises(self):
        """FtsConfig(match_mode='maybe') → ValidationError (Literal constraint)."""
        from pydantic import ValidationError
        _, FtsConfig = _import_settings()
        with pytest.raises(ValidationError):
            FtsConfig(match_mode="maybe")

    def test_use_omw_true(self):
        """FtsConfig(use_omw=True) stores True."""
        _, FtsConfig = _import_settings()
        cfg = FtsConfig(use_omw=True)
        assert cfg.use_omw is True

    def test_extra_ignored(self):
        """FtsConfig with extra keys does not raise (model_config extra='ignore')."""
        _, FtsConfig = _import_settings()
        # Should not raise
        cfg = FtsConfig(language="it", unknown_key="x")
        assert cfg.language == "it"
