"""Tests for structured summarizer output."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.models import NewsArticle
from processor.summarizer import SummaryOutput, generate_summaries_for_category


@pytest.fixture
def mock_article():
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
    return SummaryOutput(
        category="tech",
        period="morning",
        header="🧠 Tecnologia — Manhã",
        bullets=[
            "A nova rodada concentrou anúncios de produto e expansão comercial.",
            "Os players mais relevantes reforçaram diferenciação em IA aplicada.",
            "O mercado reagiu mais à execução do que ao discurso promocional.",
        ],
        insight="O ponto central é que a disputa saiu do piloto e entrou em fase de monetização e escala.",
        sections=[
            {
                "key": "o_que_mudou",
                "title": "O que mudou",
                "content": "Os anúncios recentes mostraram avanço simultâneo em produto, distribuição e capacidade operacional.",
            },
            {
                "key": "por_que_importa",
                "title": "Por que importa",
                "content": "Isso importa porque o mercado agora compara execução concreta, barreira competitiva e velocidade de adoção.",
            },
            {
                "key": "watchlist",
                "title": "Watchlist",
                "content": "O próximo sinal relevante será a conversão dessas apostas em receita, uso recorrente e vantagem defensável.",
            },
        ],
    )


def test_summary_output_validation(mock_summary_output):
    summary_dict = mock_summary_output.model_dump()
    assert summary_dict["category"] == "tech"
    assert summary_dict["period"] == "morning"
    assert summary_dict["header"] == "🧠 Tecnologia — Manhã"
    assert len(summary_dict["bullets"]) == 3
    assert summary_dict["insight"].startswith("O ponto central")
    assert summary_dict["sections"][0]["key"] == "o_que_mudou"


@pytest.mark.asyncio
@patch("processor.summarizer.get_llm_client")
@patch("processor.summarizer.async_session")
async def test_summarizer_marks_articles_processed(
    mock_async_session, mock_get_llm_client, mock_article, mock_summary_output
):
    mock_llm = AsyncMock()
    mock_llm.chat_json_async.return_value = mock_summary_output.model_dump()
    mock_llm.model_name = "test-model"
    mock_get_llm_client.return_value = mock_llm

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    added_objects = []

    def add_side_effect(obj):
        obj.id = 321
        added_objects.append(obj)

    mock_session.add.side_effect = add_side_effect

    mock_result_articles = MagicMock()
    mock_result_articles.scalars.return_value.all.return_value = [mock_article]

    mock_result_summary = MagicMock()
    mock_result_summary.scalar_one_or_none.return_value = None

    mock_update_result = MagicMock()

    mock_session.execute.side_effect = [mock_result_articles, mock_result_summary, mock_update_result]
    mock_async_session.return_value.__aenter__.return_value = mock_session
    mock_async_session.return_value.__aexit__ = AsyncMock()

    result = await generate_summaries_for_category([mock_article], "morning")

    assert result is not None
    assert result.id == 321
    assert result.model_used == "test-model"
    assert result.key_takeaways["version"] == 2
    assert result.key_takeaways["sections"][0]["key"] == "o_que_mudou"
    assert "Insight-chave" in result.summary_text
    assert mock_session.commit.called
    assert mock_session.add.called
