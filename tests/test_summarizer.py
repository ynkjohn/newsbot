"""Tests for structured summarizer output."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.models import NewsArticle, Summary
from processor.llm_client import LLMUsage
from processor.summarizer import (
    SummaryOutput,
    _select_articles_for_summary,
    generate_summaries_for_category,
)


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
        items=[
            {
                "event_key": "ia-monetizacao",
                "title": "Empresas aceleram monetização de IA",
                "why_it_matters": "A disputa passa a depender de receita recorrente e vantagem defensável, não apenas de anúncios de produto.",
                "what_happened": "A rodada trouxe anúncios de produto e expansão comercial em IA aplicada.",
                "watchlist": "Acompanhar conversão em receita, uso recorrente e barreiras competitivas.",
                "source_indexes": [1],
                "source_article_ids": [],
                "importance": "high",
                "importance_score": 5,
                "novelty": "new",
                "sentiment": "neutral",
                "material_change": True,
                "trust_status": "trusted",
                "command_hint": "!ia",
            }
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


def test_summary_output_preserves_short_command_hint():
    parsed = SummaryOutput(
        category="tech",
        period="morning",
        header="🧠 Tecnologia — Manhã",
        bullets=[
            "A nova rodada concentrou anúncios relevantes de produto.",
            "Os players reforçaram diferenciação em IA aplicada.",
            "O mercado reagiu mais à execução do que ao discurso.",
        ],
        insight="A principal implicação é que a disputa em IA entrou em fase de monetização concreta.",
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
        ],
        items=[
            {
                "event_key": "ia-monetizacao",
                "title": "Empresas aceleram monetização de IA",
                "why_it_matters": "A disputa passa a depender de receita recorrente e vantagem defensável.",
                "what_happened": "A rodada trouxe anúncios de produto e expansão comercial em IA aplicada.",
                "watchlist": "Acompanhar conversão em receita e uso recorrente.",
                "source_article_ids": [1],
                "importance": "high",
                "importance_score": 5,
                "novelty": "new",
                "sentiment": "neutral",
                "material_change": True,
                "trust_status": "trusted",
                "command_hint": "!ia",
            }
        ],
    )

    assert parsed.items[0].command_hint == "!ia"


def test_summary_output_clamps_importance_score_to_allowed_range(mock_summary_output):
    payload = mock_summary_output.model_dump()
    payload["items"][0]["importance_score"] = 10

    parsed = SummaryOutput(**payload)

    assert parsed.items[0].importance_score == 5


def test_summary_output_trims_extra_bullets(mock_summary_output):
    payload = mock_summary_output.model_dump()
    payload["bullets"] = [
        "Primeiro bullet com tamanho suficiente para passar na validação.",
        "Segundo bullet com tamanho suficiente para passar na validação.",
        "Terceiro bullet com tamanho suficiente para passar na validação.",
        "Quarto bullet com tamanho suficiente para passar na validação.",
        "Quinto bullet com tamanho suficiente para passar na validação.",
        "Sexto bullet excedente que deve ser removido antes da validação.",
    ]

    parsed = SummaryOutput(**payload)

    assert len(parsed.bullets) == 5
    assert parsed.bullets[-1].startswith("Quinto")


def test_summary_output_rejects_numeric_position_command_hint(mock_summary_output):
    payload = mock_summary_output.model_dump()
    payload["items"][0]["command_hint"] = "!1"

    with pytest.raises(ValueError):
        SummaryOutput(**payload)


def test_select_articles_for_summary_prefers_relevant_politics_over_recent_noise():
    def article(article_id, title, source_name, published_at):
        item = MagicMock(spec=NewsArticle)
        item.id = article_id
        item.title = title
        item.url = f"https://example.com/{article_id}"
        item.published_at = published_at
        item.source = MagicMock()
        item.source.name = source_name
        return item

    newest_noise = article(
        1,
        "Restaurante famoso abre nova unidade em Brasília",
        "Metropoles",
        datetime.datetime(2026, 4, 30, 19, 30),
    )
    older_relevant = article(
        2,
        "STF decide que desoneração precisa prever impacto",
        "G1 Política",
        datetime.datetime(2026, 4, 30, 18, 50),
    )
    selected = _select_articles_for_summary(
        [newest_noise, older_relevant],
        "politica-brasil",
    )

    assert selected[0] is older_relevant
    assert newest_noise not in selected


def test_select_articles_for_summary_diversifies_duplicate_politics_events():
    def article(article_id, title, published_at):
        item = MagicMock(spec=NewsArticle)
        item.id = article_id
        item.title = title
        item.url = f"https://example.com/{article_id}"
        item.published_at = published_at
        item.source = MagicMock()
        item.source.name = "G1 Política"
        return item

    duplicates = [
        article(
            article_id,
            f"PL da Dosimetria tem nova votação no Congresso {article_id}",
            datetime.datetime(2026, 4, 30, 19, article_id % 60),
        )
        for article_id in range(20, 35)
    ]
    older_distinct = article(
        2,
        "STF decide que desoneração precisa prever impacto",
        datetime.datetime(2026, 4, 30, 18, 50),
    )

    selected = _select_articles_for_summary(
        [*duplicates, older_distinct],
        "politica-brasil",
    )

    assert older_distinct in selected


@pytest.mark.asyncio
@patch("processor.summarizer.get_llm_client")
@patch("processor.summarizer.async_session")
async def test_summarizer_marks_articles_processed(
    mock_async_session, mock_get_llm_client, mock_article, mock_summary_output
):
    mock_llm = AsyncMock()
    mock_llm.chat_json_async_with_usage.return_value = (
        mock_summary_output.model_dump(),
        LLMUsage(
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            estimated_cost_usd=0.0000196,
        ),
    )
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

    mock_result_no_summary = MagicMock()
    mock_result_no_summary.scalar_one_or_none.return_value = None

    mock_session.execute.side_effect = [
        mock_result_no_summary,
        mock_result_articles,
        mock_result_summary,
        mock_update_result,
    ]
    mock_async_session.return_value.__aenter__.return_value = mock_session
    mock_async_session.return_value.__aexit__ = AsyncMock()

    result = await generate_summaries_for_category([mock_article], "morning")

    assert result is not None
    assert result.id == 321
    assert result.model_used == "test-model"
    assert result.key_takeaways["version"] == 3
    assert result.key_takeaways["sections"][0]["key"] == "o_que_mudou"
    assert result.key_takeaways["items"][0]["command_hint"] == "!ia"
    assert result.key_takeaways["items"][0]["source_article_ids"] == [1]
    assert result.token_count == 120
    assert result._llm_usage["total_tokens"] == 120
    assert "Empresas aceleram monetização de IA — !ia" in result.summary_text
    assert mock_session.commit.called
    assert mock_session.add.called


@pytest.mark.asyncio
@patch("processor.summarizer.get_llm_client")
@patch("processor.summarizer.async_session")
async def test_summarizer_skips_existing_summary_without_calling_llm(
    mock_async_session, mock_get_llm_client, mock_article
):
    mock_llm = AsyncMock()
    mock_get_llm_client.return_value = mock_llm

    existing_summary = Summary(
        category="tech",
        period="morning",
        date=datetime.date(2026, 4, 30),
        summary_text="Resumo antigo",
        key_takeaways={},
        source_article_ids=[1],
        model_used="old-model",
    )

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()

    mock_result_existing = MagicMock()
    mock_result_existing.scalar_one_or_none.return_value = existing_summary
    mock_session.execute.return_value = mock_result_existing

    mock_async_session.return_value.__aenter__.return_value = mock_session
    mock_async_session.return_value.__aexit__ = AsyncMock()

    result = await generate_summaries_for_category([mock_article], "morning")

    assert result is None
    assert not mock_llm.chat_json_async_with_usage.called
    assert mock_session.execute.await_count == 1


@pytest.mark.asyncio
@patch("processor.summarizer.get_llm_client")
@patch("processor.summarizer.async_session")
async def test_summarizer_replaces_existing_summary_when_requested(
    mock_async_session, mock_get_llm_client, mock_article, mock_summary_output
):
    mock_llm = AsyncMock()
    mock_llm.chat_json_async_with_usage.return_value = (
        mock_summary_output.model_dump(),
        LLMUsage(
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            estimated_cost_usd=0.0000196,
        ),
    )
    mock_llm.model_name = "test-model"
    mock_get_llm_client.return_value = mock_llm

    existing_summary = Summary(
        category="tech",
        period="morning",
        date=datetime.date(2026, 4, 30),
        summary_text="Resumo antigo",
        key_takeaways={"version": 1},
        source_article_ids=[99],
        model_used="old-model",
        token_count=123,
        created_at=datetime.datetime(2026, 4, 30, 7, 0),
        sent_at=datetime.datetime(2026, 4, 30, 8, 0),
    )
    existing_summary.id = 999

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    mock_result_articles = MagicMock()
    mock_result_articles.scalars.return_value.all.return_value = [mock_article]

    mock_result_summary = MagicMock()
    mock_result_summary.scalar_one_or_none.return_value = existing_summary

    mock_update_result = MagicMock()
    mock_session.execute.side_effect = [mock_result_articles, mock_result_summary, mock_update_result]

    mock_async_session.return_value.__aenter__.return_value = mock_session
    mock_async_session.return_value.__aexit__ = AsyncMock()

    result = await generate_summaries_for_category(
        [mock_article],
        "morning",
        replace_existing=True,
    )

    assert result is existing_summary
    assert result.source_article_ids == [1]
    assert result.model_used == "test-model"
    assert result.token_count == 120
    assert result.created_at != datetime.datetime(2026, 4, 30, 7, 0)
    assert result.sent_at is None
    assert "Empresas aceleram monetização de IA — !ia" in result.summary_text
    assert result.key_takeaways["version"] == 3
    assert result.key_takeaways["items"][0]["source_article_ids"] == [1]
    assert result._llm_usage["estimated_cost_usd"] == 0.0000196
    assert not mock_session.add.called
    assert mock_session.commit.called
