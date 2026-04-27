from __future__ import annotations

import datetime

from processor.summary_format import PERIOD_GREETINGS, display_category, display_period


def help_text() -> str:
    return "\n".join(
        [
            "NewsBot envia manchetes curtas por editoria.",
            "",
            "• Use !hoje para ver o boletim do dia",
            "• Use !politica, !economia, !cripto, !geopolitica ou !tech para uma editoria",
            "• Para aprofundar uma notícia, mande o comando que aparece no fim da manchete, como !pis ou !gaza",
            "• Também respondo perguntas livres sobre as notícias recentes",
            "",
            "Assinatura:",
            "• !start ou !inscrever para ativar os resumos",
            "• !stop ou !sair para pausar o envio",
        ]
    )


def subscribe_confirmation(schedule: str) -> str:
    return "\n".join(
        [
            "Inscrição confirmada.",
            f"Você vai receber os resumos em {schedule}.",
            "Se quiser, também pode pedir um recorte específico com !politica, !economia, !cripto, !geopolitica, !tech ou !hoje.",
        ]
    )


def subscribe_reactivated(schedule: str) -> str:
    return f"Assinatura reativada. Os resumos voltam a chegar em {schedule}."


def already_subscribed(schedule: str) -> str:
    return f"Você já está ativo. Os envios seguem no ritmo de {schedule}."


def unsubscribe_confirmation() -> str:
    return "Inscrição pausada. Quando quiser voltar, é só mandar !start."


def not_subscribed() -> str:
    return "Você não está com uma assinatura ativa no momento."


def no_summary_available(label: str) -> str:
    return f"Ainda não tenho um resumo pronto para {label}."


def retry_no_pending() -> str:
    return "Não encontrei resumos pendentes de envio para hoje."


def retry_no_subscribers() -> str:
    return "Não há assinantes ativos para reenviar esse lote."


def retry_completed(sent_count: int) -> str:
    return f"Reenvio concluído para {sent_count} assinante(s)."


def digest_intro(period: str, date: datetime.date) -> str:
    greeting = PERIOD_GREETINGS.get(period, "Olá")
    return f"{greeting}. Fechamento de {display_period(period)} em {date.strftime('%d/%m/%Y')}."


def digest_footer() -> str:
    return "Para aprofundar, mande o comando da manchete. Editorias: !politica !economia !cripto !geopolitica !tech"


def natural_question_fallback() -> str:
    return "Não tenho dados recentes sobre esse assunto. Se quiser, posso te responder sobre as notícias do dia."


def question_processing_error() -> str:
    return "Tive um problema para responder agora. Tenta de novo em instantes."


def category_summary_label(category: str) -> str:
    return display_category(category)
