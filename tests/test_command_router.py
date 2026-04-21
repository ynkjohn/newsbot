"""Tests for interactions.command_router.parse_message."""
import pytest

from interactions.command_router import parse_message


def test_parse_empty():
    assert parse_message("") == ("other", None)
    assert parse_message("   ") == ("other", None)


def test_parse_known_commands():
    assert parse_message("!help") == ("command", "!help")
    assert parse_message("!geopolitica") == ("command", "!geopolitica")
    assert parse_message("!START") == ("command", "!start")


def test_parse_unknown_command_maps_to_help():
    assert parse_message("!unknown") == ("command", "!help")


def test_parse_question_dm():
    assert parse_message("O que aconteceu com a economia hoje?") == ("question", None)


def test_parse_greeting_dm_maps_to_help():
    assert parse_message("oi") == ("command", "!help")


def test_parse_group_non_command_ignored():
    assert parse_message("random chat", is_group=True) == ("other", None)


def test_parse_group_command_without_question():
    assert parse_message("!politica", is_group=True) == ("command", "!politica")


@pytest.mark.parametrize(
    "text",
    ["Qual o impacto das eleicoes na bolsa?"],
)
def test_parse_group_question(text):
    assert parse_message(text, is_group=True) == ("question", None)
