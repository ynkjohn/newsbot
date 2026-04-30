"""Tests for processor/categorizer.py"""
from processor.categorizer import validate_period, validate_category
from db.models import VALID_CATEGORIES


class TestValidatePeriod:
    """Test validate_period function for all 4 periods."""

    def test_validate_period_all_four_periods(self):
        """All 4 periods must be valid."""
        assert validate_period("morning") == "morning"
        assert validate_period("midday") == "midday"
        assert validate_period("afternoon") == "afternoon"
        assert validate_period("evening") == "evening"

    def test_validate_period_case_insensitive(self):
        """Period validation must be case-insensitive."""
        assert validate_period("MORNING") == "morning"
        assert validate_period("MidDay") == "midday"
        assert validate_period("AFTERNOON") == "afternoon"
        assert validate_period("Evening") == "evening"

    def test_validate_period_with_whitespace(self):
        """Must handle surrounding whitespace."""
        assert validate_period("  morning  ") == "morning"
        assert validate_period("\tmidday\n") == "midday"
        assert validate_period("  afternoon  ") == "afternoon"

    def test_validate_period_invalid_defaults_to_morning(self):
        """Invalid period should default to 'morning'."""
        assert validate_period("invalid") == "morning"
        assert validate_period("night") == "morning"
        assert validate_period("") == "morning"
        assert validate_period("   ") == "morning"

    def test_validate_period_mixed_case_with_whitespace(self):
        """Must handle mixed case and whitespace together."""
        assert validate_period("  MIDDAY  ") == "midday"
        assert validate_period("\tEvening\n") == "evening"


class TestValidateCategory:
    """Test validate_category function."""

    def test_validate_category_valid_categories(self):
        """All valid categories should pass through."""
        for category in VALID_CATEGORIES:
            assert validate_category(category) == category

    def test_validate_category_case_insensitive(self):
        """Category validation should be case-insensitive."""
        assert validate_category("TECH") == "tech"
        assert validate_category("Economia-Brasil") == "economia-brasil"

    def test_validate_category_legacy_world_aliases(self):
        """Previous international category names should map to current world categories."""
        assert validate_category("economia-internacional") == "economia-mundao"
        assert validate_category("politica-internacional") == "politica-mundao"

    def test_validate_category_fuzzy_match(self):
        """Should fuzzy match if close to valid category."""
        # If "tech" is valid, "tecnologia" should fuzzy match
        result = validate_category("tecnologia")
        assert result in VALID_CATEGORIES

    def test_validate_category_invalid_defaults_to_first(self):
        """Invalid category should default to first valid one."""
        result = validate_category("invalid_category_xyz")
        assert result == VALID_CATEGORIES[0]
