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
    article.processed = False
    article.summary_id = None
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
async def test_summary_output_validation(mock_summary_output):
    """Verify the SummaryOutput Pydantic model structure and validation."""
    # Verify the SummaryOutput Pydantic model validates correctly
    summary_dict = mock_summary_output.model_dump()
    
    # Check all required fields are present
    assert summary_dict["category"] == "tech"
    assert summary_dict["period"] == "morning"
    assert summary_dict["header"] == "Tech News"
    assert len(summary_dict["bullets"]) == 3
    assert summary_dict["insight"] == "Key insight"
    assert "example.com" in summary_dict["source_urls"][0]


@pytest.mark.asyncio
@patch("processor.summarizer.get_llm_client")
@patch("processor.summarizer.async_session")
async def test_summarizer_marks_articles_processed(
    mock_async_session, mock_get_llm_client, mock_article, mock_summary_output
):
    """Test that articles are marked as processed when a summary is generated."""
    # Mock LLM client
    mock_llm = AsyncMock()
    mock_llm.chat_json_async.return_value = mock_summary_output.model_dump()
    mock_llm.model_name = "test-model"
    mock_get_llm_client.return_value = mock_llm
    
    # Mock session
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.get = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.add = MagicMock()
    
    # Result for article loading (first session.execute)
    mock_result_articles = MagicMock()
    mock_result_articles.scalars.return_value.all.return_value = [mock_article]
    
    # Result for existing summary check (second session.execute)
    mock_result_summary = MagicMock()
    mock_result_summary.scalar_one_or_none.return_value = None
    
    # Assign side_effect for multiple calls
    mock_session.execute.side_effect = [mock_result_articles, mock_result_summary]
    
    # Mock session.get for marking as processed
    mock_session.get.return_value = mock_article
    
    # Set up async context manager for session
    mock_async_session.return_value.__aenter__.return_value = mock_session
    mock_async_session.return_value.__aexit__ = AsyncMock()
    
    # Call the function
    result = await generate_summaries_for_category([mock_article], "morning")
    
    # Verify the results
    assert result is not None
    assert mock_article.processed is True
    assert mock_article.summary_id == result.id
    
    # Verify DB actions
    assert mock_session.commit.called
    assert mock_session.add.called
