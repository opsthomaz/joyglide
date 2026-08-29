# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``user_preferences.validate_settings`` — value-level coercion.

``load_settings`` already migrates *keys* (fills missing, strips legacy).
``validate_settings`` is the value-level guard: users are explicitly told
to hand-edit settings.json (see latency_trace.py instructions), so a typo
like ``"sensitivity": "fast"`` or ``"acceleration_level": 99`` must not flow
onto the hot path. Out-of-range numerics clamp; wrong types reset to the
documented default.
"""
from user_preferences import DEFAULTS, validate_settings


def _valid():
    """A fresh copy of the known-good defaults."""
    return DEFAULTS.copy()


class TestValidValuesPassThrough:
    def test_defaults_are_unchanged(self):
        v = validate_settings(_valid())
        for key, default in DEFAULTS.items():
            assert v[key] == default

    def test_in_range_custom_values_preserved(self):
        raw = _valid()
        raw["sensitivity"] = 2.5
        raw["acceleration_level"] = 3
        raw["scroll_sensitivity"] = 7
        raw["profile"] = "gaming"
        v = validate_settings(raw)
        assert v["sensitivity"] == 2.5
        assert v["acceleration_level"] == 3
        assert v["scroll_sensitivity"] == 7
        assert v["profile"] == "gaming"


class TestNumericClamping:
    def test_sensitivity_clamped_to_range(self):
        assert validate_settings({**_valid(), "sensitivity": 99.0})["sensitivity"] == 3.0
        assert validate_settings({**_valid(), "sensitivity": 0.1})["sensitivity"] == 0.5

    def test_acceleration_level_clamped(self):
        assert validate_settings({**_valid(), "acceleration_level": 99})["acceleration_level"] == 3
        assert validate_settings({**_valid(), "acceleration_level": 0})["acceleration_level"] == 1

    def test_scroll_sensitivity_clamped(self):
        assert validate_settings({**_valid(), "scroll_sensitivity": 100})["scroll_sensitivity"] == 10
        assert validate_settings({**_valid(), "scroll_sensitivity": -5})["scroll_sensitivity"] == 1

    def test_deadzone_negative_clamped_to_zero(self):
        assert validate_settings({**_valid(), "deadzone": -3})["deadzone"] == 0


class TestWrongTypeResetsToDefault:
    def test_non_numeric_sensitivity_resets(self):
        assert validate_settings({**_valid(), "sensitivity": "fast"})["sensitivity"] == DEFAULTS["sensitivity"]

    def test_non_int_acceleration_level_resets(self):
        assert validate_settings({**_valid(), "acceleration_level": "high"})["acceleration_level"] == DEFAULTS["acceleration_level"]

    def test_non_bool_flag_resets(self):
        # double_click_enabled is a bool; a string must reset, not be truthy-coerced.
        assert validate_settings({**_valid(), "double_click_enabled": "yes"})["double_click_enabled"] is True
        assert validate_settings({**_valid(), "disable_acceleration": 1})["disable_acceleration"] is True

    def test_devices_must_be_dict(self):
        assert validate_settings({**_valid(), "devices": "corrupt"})["devices"] == {}


class TestProfileEnum:
    def test_unknown_profile_resets_to_default(self):
        assert validate_settings({**_valid(), "profile": "bogus"})["profile"] == "dynamic"

    def test_known_profiles_preserved(self):
        for p in ("dynamic", "gaming", "cinematic"):
            assert validate_settings({**_valid(), "profile": p})["profile"] == p


class TestIntFieldsCoercedFromFloat:
    def test_float_acceleration_level_becomes_int(self):
        # A hand-edited 2.0 should become int 2, not stay a float.
        v = validate_settings({**_valid(), "acceleration_level": 2.0})
        assert v["acceleration_level"] == 2
        assert isinstance(v["acceleration_level"], int)


class TestCorruptSettingsFile:
    """Users are told to hand-edit settings.json; a stray comma must not
    crash the app at import with no UI to explain why."""

    def test_invalid_json_is_backed_up_and_defaults_written(self, tmp_path, monkeypatch):
        import user_preferences as up

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"sensitivity": 1.0,,}')
        monkeypatch.setattr(up, "get_settings_path", lambda: settings_file)

        loaded = up.load_settings()

        assert loaded["sensitivity"] == DEFAULTS["sensitivity"]
        assert (tmp_path / "settings.json.corrupt").exists()
        assert (tmp_path / "settings.json.corrupt").read_text() == '{"sensitivity": 1.0,,}'


class TestButtonMapValidation:
    def test_invalid_action_resets_that_button_only(self):
        from user_preferences import DEFAULT_BUTTON_MAP
        raw = _valid(); raw["button_map"] = {**DEFAULT_BUTTON_MAP, "R": "teleport", "A": "middle"}
        v = validate_settings(raw)
        assert v["button_map"]["R"] == "left"
        assert v["button_map"]["A"] == "middle"

    def test_unknown_button_dropped_and_missing_button_filled(self):
        from user_preferences import DEFAULT_BUTTON_MAP
        raw = _valid(); raw["button_map"] = {"R": "right", "BANANA": "left"}
        v = validate_settings(raw)
        assert "BANANA" not in v["button_map"]
        assert v["button_map"]["R"] == "right"
        assert v["button_map"]["ZR"] == DEFAULT_BUTTON_MAP["ZR"]
        assert set(v["button_map"]) == set(DEFAULT_BUTTON_MAP)

    def test_non_dict_button_map_resets(self):
        from user_preferences import DEFAULT_BUTTON_MAP
        raw = _valid(); raw["button_map"] = "everything"
        assert validate_settings(raw)["button_map"] == DEFAULT_BUTTON_MAP

    def test_defaults_button_map_is_a_copy(self):
        """Mutating the loaded map must not mutate the module default."""
        from user_preferences import DEFAULT_BUTTON_MAP, DEFAULTS
        assert DEFAULTS["button_map"] == DEFAULT_BUTTON_MAP
        assert DEFAULTS["button_map"] is not DEFAULT_BUTTON_MAP
