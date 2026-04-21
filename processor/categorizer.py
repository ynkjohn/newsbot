from db.models import VALID_CATEGORIES


def validate_category(category: str) -> str:
    """Validate and normalize a category string. Returns the category if valid,
    or the closest match if slightly off."""
    category = category.strip().lower()

    if category in VALID_CATEGORIES:
        return category

    # Try fuzzy match: check if the category contains a valid one
    for valid in VALID_CATEGORIES:
        if valid in category or category in valid:
            return valid

    # Default to first category if no match
    return VALID_CATEGORIES[0]


def validate_period(period: str) -> str:
    """Validate period is one of: 'morning', 'midday', 'afternoon', 'evening'."""
    period = period.strip().lower()
    if period in ("morning", "midday", "afternoon", "evening"):
        return period
    return "morning"
