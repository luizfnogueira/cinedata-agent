"""Mensagens amigáveis para os erros do agente, compartilhadas pela CLI e pela interface."""

from pydantic_ai import FallbackExceptionGroup, ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded

from cinedata_agent.llm import ModelTimeoutError, is_daily_quota_error

# Erros que o agente sabe explicar ao usuário; o resto é bug e deve propagar.
AGENT_ERRORS = (ModelAPIError, FallbackExceptionGroup, UsageLimitExceeded, UnexpectedModelBehavior)


def _describe_model_error(error: ModelAPIError) -> str:
    if isinstance(error, ModelTimeoutError):
        return f"{error.model_name} não respondeu a tempo"
    if isinstance(error, ModelHTTPError):
        if error.status_code == 401:
            return "chave do OpenRouter inválida ou ausente (confira OPENROUTER_API_KEY no .env)"
        if is_daily_quota_error(error):
            return "cota diária de modelos gratuitos esgotada (50 requisições); ela renova às 21h, horário de Brasília"
        if error.status_code == 429 and "per-min" in str(error.body).lower():
            return "limite de 20 requisições por minuto atingido; aguarde um minuto"
        if error.status_code == 429:
            return f"{error.model_name} está sobrecarregado (429)"
        return f"{error.model_name} retornou erro {error.status_code}"
    return f"{error.model_name}: {error}"


def describe_error(error: BaseException) -> str:
    """Explica em português por que a pergunta não pôde ser respondida."""
    if isinstance(error, FallbackExceptionGroup):
        reasons = "; ".join(
            _describe_model_error(e) if isinstance(e, ModelAPIError) else str(e) for e in error.exceptions
        )
        return f"Nenhum modelo conseguiu responder agora ({reasons}). Tente de novo em instantes."
    if isinstance(error, ModelAPIError):
        reason = _describe_model_error(error)
        return reason[0].upper() + reason[1:] + "."
    if isinstance(error, UsageLimitExceeded):
        return "O agente atingiu o limite de requisições para esta pergunta sem chegar a uma resposta. Tente reformulá-la."
    if isinstance(error, UnexpectedModelBehavior):
        return f"O modelo não conseguiu gerar uma consulta válida: {error}"
    return f"Erro inesperado: {error}"
