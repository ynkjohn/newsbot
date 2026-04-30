"""Tests for core.periods — single source of truth for pipeline periods."""
import pytest

from core.periods import (
    VALID_PERIODS,
    VALID_PERIODS_SET,
    is_valid_period,
    period_display_name,
    validate_period,
)


class TestValidPeriods:
    def test_canonical_order(self):
        assert VALID_PERIODS == ("morning", "midday", "afternoon", "evening")

    def test_set_matches_tuple(self):
        assert VALID_PERIODS_SET == frozenset(VALID_PERIODS)


class TestIsValidPeriod:
    @pytest.mark.parametrize("period", ["morning", "midday", "afternoon", "evening"])
    def test_valid_periods(self, period: str):
        assert is_valid_period(period) is True

    @pytest.mark.parametrize("period", ["dawn", "night", "Morning", "MORNING", "", "lunch"])
    def test_invalid_periods(self, period: str):
        assert is_valid_period(period) is False


class TestValidatePeriod:
    @pytest.mark.parametrize("period", VALID_PERIODS)
    def test_returns_period_on_success(self, period: str):
        assert validate_period(period) == period

    def test_raises_on_invalid(self):
        with pytest.raises(ValueError, match="Invalid period 'dawn'"):
            validate_period("dawn")


class TestPeriodDisplayName:
    @pytest.mark.parametrize(
        "period,expected",
        [
            ("morning", "Manhã"),
            ("midday", "Meio-dia"),
            ("afternoon", "Tarde"),
            ("evening", "Noite"),
        ],
    )
    def test_known_labels(self, period: str, expected: str):
        assert period_display_name(period) == expected

    def test_unknown_falls_back(self):
        assert period_display_name("custom") == "Custom"
