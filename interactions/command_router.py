import structlog

logger = structlog.get_logger()

# Command definitions: command -> (handler_function_name, description)
COMMANDS = {
    "!start": ("subscribe", "Inscrever-se nos resumos"),
    "!inscrever": ("subscribe", "Inscrever-se nos resumos"),
    "!stop": ("unsubscribe", "Cancelar inscrição"),
    "!sair": ("unsubscribe", "Cancelar inscrição"),
    "!politica": ("category", "politica-brasil"),
    "!economia": ("category", "economia-brasil"),
    "!cripto": ("category", "economia-cripto"),
    "!mundao": ("category_world", "economia-mundao + politica-mundao"),      # mantido por compatibilidade
    "!geopolitica": ("category_world", "economia-mundao + politica-mundao"),  # novo nome
    "!tech": ("category", "tech"),
    "!hoje": ("today", "Todos os resumos do dia"),
    "!help": ("help", "Lista de comandos"),
}


def parse_message(message: str, is_group: bool = False) -> tuple[str, str | None]:
    """Parse an incoming WhatsApp message.

    Args:
        message: The message text
        is_group: Whether this message is from a group chat

    Returns:
        (message_type, detail) where message_type is "command", "question", or "other"
        and detail is the command name or None for questions.

    Rules:
        - In groups: Only process commands (!) or LLM questions (already filtered by @mention in WhatsApp bridge)
        - In DMs: Process commands, greetings, and questions normally
    """
    text = message.strip().lower()

    if not text:
        return ("other", None)

    # Check if it's a command
    if text.startswith("!"):
        command = text.split()[0]
        if command in COMMANDS:
            return ("command", command)
        return ("command", "!help")  # Unknown command -> help

    # In groups: only commands are allowed at this point
    # (LLM questions are already @mentioned in the bridge, so they arrive as normal questions)
    # Non-command, non-question messages should be ignored
    if is_group:
        # If it's not a command and not a valid question, ignore in groups
        if not _is_valid_question(text):
            logger.debug(f"Ignoring non-command, non-question group message: {text[:50]}")
            return ("other", None)
        # Otherwise fall through to question handling below

    # Check if it's a greeting - respond with help menu (only in DMs)
    if not is_group and _is_greeting(text):
        return ("command", "!help")  # Treat greeting as help request

    # Ignore spam/random characters (3+ repeated chars, excessive caps, etc)
    # Check for spam patterns
    if _is_spam(text):
        return ("other", None)

    # It's a question/free text - but only if it looks like a real question
    if _is_valid_question(text):
        return ("question", None)
    
    return ("other", None)


def _is_greeting(text: str) -> bool:
    """Detect common Portuguese greetings."""
    greetings = {
        "oi", "olá", "alo", "e aí", "eai", "eae", "tudo bem", "opa",
        "oláa", "oiii", "oiiii", "tae", "eae",
        "hey", "opa, tudo"
    }
    
    # Remove common punctuation
    clean_text = text.rstrip("?!.,")
    
    # Check if it matches a greeting or is very short and greeting-like
    return clean_text in greetings


def _is_spam(text: str) -> bool:
    """Detect obvious spam patterns."""
    # Check for 3+ repeated characters
    for i in range(len(text) - 2):
        if text[i] == text[i + 1] == text[i + 2]:
            return True
    
    # Check for very short meaningless text (1-2 chars only)
    if len(text.split()) <= 1 and len(text) <= 3:
        return True
    
    return False


def _is_valid_question(text: str) -> bool:
    """Check if text looks like a real question."""
    # Must have at least 3 words or contain news-related keywords
    words = text.split()
    
    # Very short messages (1-2 words) usually aren't real questions
    if len(words) <= 2:
        return False
    
    # List of keywords that indicate real questions about news
    news_keywords = {
        "qual", "quem", "quando", "onde", "por", "porque", "como", "oque",
        "o que", "explica", "explique", "me explique", "me diz", "me disses",
        "detalhado", "mais", "contexto", "significa", "quer dizer",
        "noticia", "noticias", "resumo", "resumos", "ultima", "ultimas",
        "aconteceu", "acontecendo", "sobre", "assunto", "tema", "politica",
        "economia", "cripto", "tecnologia", "tech", "brasil", "mundo",
    }
    
    # For 3+ words, always accept as question
    if len(words) >= 3:
        return True
    
    return False
