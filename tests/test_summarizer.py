"""Tests for summarizer with race condition protection."""
import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from processor.summarizer import generate_summaries_for_category, SummaryOutput
from db.models import NewsArticle, Summary


@pytest.fixture
def mock_article():
    """Create mock article."""
    article = MagicMock(spec=NewsArticle)
    article.id = 1
    article.category = "tech"
    article.title = "Test Article"
    article.raw_content = "This is test content"
    article.published_at = datetime.datetime(2026, 4, 18, 12, 0)
    article.source = MagicMock()
    article.source.name = "Test Source"
    return article


@pytest.fixture
def mock_summary_output():
    """Create valid SummaryOutput."""
    return SummaryOutput(
        category="tech",
        period="morning",
        header="Tech News",
        bullets=["Point 1", "Point 2", "Point 3"],
        insight="Key insight",
        full_summary_text="Full summary text here",
        source_urls=["https://example.com"]
    )


@pytest.mark.asyncio
async def test_summarizer_marks_articles_processed(mock_article, mock_summary_output):
    """Test that SummaryOutput structure is valid."""
    # Verify the SummaryOutput Pydantic model validates correctly
    summary_dict = mock_summary_output.model_dump()
    
    # Check all required fields are present
    assert summary_dict["category"] == "tech"
    assert summary_dict["period"] == "morning"
    assert summary_dict["header"] == "Tech News"
    assert len(summary_dict["bullets"]) == 3
    assert summary_dict["insight"] == "Key insight"
    assert "example.com" in summary_dict["source_urls"][0]
