import re
import unicodedata

import structlog

logger = structlog.get_logger()

COMMANDS = {
    "!start": ("subscribe", "Inscrever-se nos resumos"),
    "!inscrever": ("subscribe", "Inscrever-se nos resumos"),
    "!stop": ("unsubscribe", "Cancelar inscrição"),
    "!sair": ("unsubscribe", "Cancelar inscrição"),
    "!politica": ("category", "politica-brasil"),
    "!economia": ("category", "economia-brasil"),
    "!cripto": ("category", "economia-cripto"),
    "!mundao": ("category_world", "economia-mundao + politica-mundao"),
    "!geopolitica": ("category_world", "economia-mundao + politica-mundao"),
    "!tech": ("category", "tech"),
    "!hoje": ("today", "Todos os resumos do dia"),
    "!help": ("help", "Lista de comandos"),
}


def parse_message(message: str, is_group: bool = False) -> tuple[str, str | None]:
    text = (message or "").strip().lower()
    if not text:
        return ("other", None)

    if text.startswith("!"):
        command = text.split()[0]
        if command in COMMANDS:
            return ("command", command)
        return ("command", "!help")

    if not is_group and _is_greeting(text):
        return ("command", "!help")

    if _is_spam(text):
        return ("other", None)

    if _is_valid_question(text):
        return ("question", None)

    if is_group:
        logger.debug("Ignoring non-actionable group message", preview=text[:50])
    return ("other", None)


def _normalized(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(char for char in ascii_text if not unicodedata.combining(char))
    return ascii_text.lower().strip()


def _is_greeting(text: str) -> bool:
    greetings = {
        "oi",
        "ola",
        "alo",
        "e ai",
        "eai",
        "eae",
        "tudo bem",
        "opa",
        "hey",
    }
    clean_text = _normalized(text.rstrip("?!.,"))
    return clean_text in greetings


def _is_spam(text: str) -> bool:
    if re.search(r"(.)\1\1", text):
        return True
    return len(text.split()) <= 1 and len(text) <= 3


def _is_valid_question(text: str) -> bool:
    normalized = _normalized(text)
    words = normalized.split()
    interrogatives = (
        "qual",
        "quais",
        "quem",
        "quando",
        "onde",
        "por que",
        "porque",
        "como",
        "o que",
        "oq",
    )
    news_keywords = {
        "explica",
        "explique",
        "me explique",
        "me diz",
        "me disse",
        "detalhado",
        "contexto",
        "significa",
        "quer dizer",
        "noticia",
        "noticias",
        "resumo",
        "resumos",
        "ultima",
        "ultimas",
        "aconteceu",
        "acontecendo",
        "sobre",
        "assunto",
        "tema",
        "politica",
        "economia",
        "cripto",
        "tecnologia",
        "tech",
        "brasil",
        "mundo",
        "bolsa",
        "mercado",
        "selic",
        "bitcoin",
        "dolar",
        "fed",
    }

    has_question_mark = "?" in text
    has_interrogative = any(normalized.startswith(prefix) for prefix in interrogatives)
    has_news_keyword = any(keyword in normalized for keyword in news_keywords)

    if len(words) <= 2:
        return has_question_mark and has_news_keyword

    return has_question_mark or has_interrogative or has_news_keyword
