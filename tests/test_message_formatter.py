"""Tests for delivery.message_formatter.format_digest."""
import datetime
from unittest.mock import MagicMock

import pytest

from delivery.message_formatter import format_digest
from db.models import Summary


def _summary(text: str, period: str = "morning") -> Summary:
    s = MagicMock(spec=Summary)
    s.summary_text = text
    s.period = period
    return s


@pytest.mark.parametrize(
    "period,expected_fragment",
    [
        ("morning", "Manhã"),
        ("midday", "Meio-dia"),
        ("afternoon", "Tarde"),
        ("evening", "Noite"),
    ],
)
def test_format_digest_period_labels(period, expected_fragment):
    d = datetime.date(2026, 4, 20)
    out = format_digest([_summary("Line one")], d, period)
    assert expected_fragment in out
    assert "20/04/2026" in out


def test_format_digest_empty_summaries():
    d = datetime.date(2026, 4, 20)
    out = format_digest([], d, "morning")
    assert "Manhã" in out
    assert "!geopolitica" in out
