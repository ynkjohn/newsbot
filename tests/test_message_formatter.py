"""Tests for delivery.message_formatter.format_digest."""
import datetime
from unittest.mock import MagicMock

import pytest

from delivery.message_formatter import format_digest, split_message
from db.models import Summary
from processor.summary_format import command_hint_for_title, render_headline_item, render_summary_text


def _summary(text: str, period: str = "morning") -> Summary:
    s = MagicMock(spec=Summary)
    s.summary_text = text
    s.period = period
    return s


def _digest_summary(category: str, items: list[dict], period: str = "morning") -> Summary:
    s = MagicMock(spec=Summary)
    s.category = category
    s.period = period
    s.summary_text = ""
    s.created_at = datetime.datetime(2026, 4, 27, 7, 0)
    s.key_takeaways = {
        "version": 3,
        "header": category,
        "lead": "",
        "items": items,
        "status": "trusted",
        "placeholder_reason": "",
        "editorial_counts": {"trusted": len(items), "total": len(items)},
    }
    return s


def _item(title: str, command: str, importance: int) -> dict:
    return {
        "position": 1,
        "event_key": command.removeprefix("!"),
        "title": title,
        "why_it_matters": "Importancia editorial suficiente para o item.",
        "what_happened": "Fato concreto com detalhes suficientes para renderizacao.",
        "watchlist": "Observar o proximo passo relevante.",
        "command_hint": command,
        "source_article_ids": [1],
        "importance_score": importance,
        "novelty": "new",
        "material_hash": command,
        "trust_status": "trusted",
        "trust_reason": "",
    }


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
    assert "20/04" in out


def test_format_digest_empty_summaries():
    d = datetime.date(2026, 4, 20)
    out = format_digest([], d, "morning")
    assert "Manhã" in out
    assert "!geopolitica" in out


def test_format_digest_footer_explains_headline_commands():
    out = format_digest([], datetime.date(2026, 4, 20), "morning")

    assert "Para aprofundar, mande o comando da manchete." in out
    assert "Editorias: !politica !economia !cripto !geopolitica !tech" in out


def test_format_digest_builds_balanced_headline_bulletin_with_counters():
    d = datetime.date(2026, 4, 27)
    summaries = [
        _digest_summary(
            "politica-brasil",
            [
                _item("Politica forte 1", "!p1", 5),
                _item("Politica forte 2", "!p2", 4),
                _item("Politica forte 3", "!p3", 4),
                _item("Politica menor 4", "!p4", 2),
            ],
        ),
        _digest_summary(
            "economia-brasil",
            [
                _item("Economia forte 1", "!e1", 5),
                _item("Economia forte 2", "!e2", 4),
                _item("Economia menor 3", "!e3", 2),
            ],
        ),
        _digest_summary("politica-mundao", [_item("Gaza pressiona cessar-fogo", "!gaza", 5)]),
        _digest_summary("economia-mundao", [_item("Petroleo sobe com tensao", "!petroleo", 4)]),
        _digest_summary("tech", [_item("EVs chineses avancam", "!evs", 4)]),
    ]

    out = format_digest(summaries, d, "morning")

    assert "NewsBot — Manhã 27/04" in out
    assert "🇧🇷 Política Brasil" in out
    assert "1. Politica forte 1 — !p1" in out
    assert "3. Politica forte 3 — !p3" in out
    assert "+1 em !politica" in out
    assert "Politica menor 4" not in out
    assert "💰 Economia Brasil" in out
    assert "+1 em !economia" in out
    assert "🌍 Mundo" in out
    assert "🌍 Economia Mundo" in out
    assert "🚀 Tech" in out
    assert "Por que importa" not in out


def test_split_message_does_not_add_part_prefixes():
    text = "Bloco inicial\n\n" + "x" * 20 + "\n\n" + "y" * 20

    parts = split_message(text, max_chars=35)

    assert len(parts) == 2
    assert all(not part.startswith("NewsBot ") for part in parts)


def test_split_message_splits_single_oversized_block_without_prefixes():
    parts = split_message("x" * 81, max_chars=40)

    assert [len(part) for part in parts] == [40, 40, 1]
    assert all(not part.startswith("NewsBot ") for part in parts)



def test_command_hint_for_title_prefers_short_specific_terms():
    reserved = {"!hoje", "!politica", "!econbr", "!geopolitica", "!econmundo", "!tech", "!help"}

    assert command_hint_for_title(
        "Caixa libera novo lote do PIS/Pasep; saldo medio e de R$ 2,8 mil",
        reserved_commands=reserved,
        used_commands=set(),
    ) == "!pis"
    assert command_hint_for_title(
        "Toyota reduz preço do RAV4 para competir com chineses",
        reserved_commands=reserved,
        used_commands={"!pis"},
    ) == "!rav4"
    assert command_hint_for_title(
        "Gaza registra alta de violações do cessar-fogo",
        reserved_commands=reserved,
        used_commands={"!pis", "!rav4"},
    ) == "!gaza"


def test_command_hint_for_title_adds_context_on_collision():
    reserved = {"!hoje", "!politica", "!econbr", "!geopolitica", "!econmundo", "!tech", "!help"}

    assert command_hint_for_title(
        "Gaza registra alta de violações do cessar-fogo",
        reserved_commands=reserved,
        used_commands={"!gaza"},
    ) == "!gaza-cessar"


def test_render_headline_item_uses_one_line_with_command():
    item = {
        "position": 3,
        "title": "Caixa libera novo lote do PIS/Pasep",
        "command_hint": "!pis",
        "importance_score": 5,
        "trust_status": "trusted",
    }

    assert render_headline_item(7, item) == "7. Caixa libera novo lote do PIS/Pasep — !pis"


def test_render_summary_text_uses_structured_headline_items():
    out = render_summary_text(
        "politica-brasil",
        "morning",
        {
            "header": "POLÍTICA BRASIL",
            "items": [
                {
                    "position": 1,
                    "title": "Governo negocia pauta econômica no Congresso",
                    "command_hint": "!congresso",
                    "importance_score": 5,
                    "trust_status": "trusted",
                }
            ],
        },
    )

    assert "1. Governo negocia pauta econômica no Congresso — !congresso" in out
    assert "Por que importa" not in out


def test_render_summary_text_returns_headlines_without_old_labels():
    out = render_summary_text(
        "politica-brasil",
        "morning",
        {
            "header": "POLITICA BRASIL",
            "items": [
                {
                    "position": 1,
                    "event_key": "titulo-eleitor",
                    "title": "Prazo para tirar titulo termina em 10 dias",
                    "why_it_matters": "Texto antigo que nao deve aparecer no comando de editoria.",
                    "what_happened": "Outro bloco antigo que nao deve aparecer.",
                    "watchlist": "Mais um bloco antigo que nao deve aparecer.",
                    "command_hint": "!titulo",
                    "source_article_ids": [1],
                    "importance_score": 5,
                    "novelty": "new",
                    "material_hash": "abc",
                    "trust_status": "trusted",
                    "trust_reason": "",
                }
            ],
        },
    )

    assert "🏛️ POLÍTICA BRASIL" in out
    assert "1. Prazo para tirar titulo termina em 10 dias — !titulo" in out
    assert "Por que importa" not in out
    assert "O que aconteceu" not in out
    assert "Fique de olho" not in out
    assert "Para aprofundar, mande o comando da notícia." in out


def test_render_summary_text_preserves_placeholder_status():
    out = render_summary_text(
        "politica-brasil",
        "morning",
        {"status": "placeholder", "items": [{"title": "Nao deve aparecer", "command_hint": "!nao"}]},
    )

    assert "Resumo em preparação. Volte em instantes." in out
    assert "Nao deve aparecer" not in out


def test_render_summary_text_keeps_legacy_sections_without_items():
    out = render_summary_text(
        "politica-brasil",
        "morning",
        {
            "header": "POLITICA BRASIL",
            "bullets": ["Congresso pauta medida fiscal"],
            "insight": "Agenda politica segue concentrada no fiscal.",
            "sections": [
                {
                    "key": "contexto",
                    "title": "Contexto",
                    "content": "O governo tenta organizar a base para votar a proposta.",
                }
            ],
        },
    )

    assert "Congresso pauta medida fiscal" in out
    assert "Agenda politica segue concentrada no fiscal." in out
    assert "O governo tenta organizar a base para votar a proposta." in out


def test_render_summary_text_limits_category_extras_and_counts_remaining():
    items = []
    for index in range(1, 9):
        items.append(
            {
                "position": index,
                "event_key": f"noticia-{index}",
                "title": f"Noticia relevante {index}",
                "why_it_matters": "Importancia suficiente para passar na normalizacao.",
                "what_happened": "Aconteceu um fato concreto com detalhes suficientes.",
                "watchlist": "Observar o proximo passo relevante.",
                "command_hint": f"!noticia{index}",
                "source_article_ids": [index],
                "importance_score": 5,
                "novelty": "new",
                "material_hash": f"hash-{index}",
                "trust_status": "trusted",
                "trust_reason": "",
            }
        )

    out = render_summary_text("economia-brasil", "morning", {"items": items})

    assert "6. Noticia relevante 6 — !noticia6" in out
    assert "+2 noticias em outro boletim de economia." in out
    assert "7. Noticia relevante 7" not in out
