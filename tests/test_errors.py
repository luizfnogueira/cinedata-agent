"""Testes das mensagens de erro e da leitura da cota. Não chamam a API."""

from pydantic_ai import FallbackExceptionGroup, ModelHTTPError, UsageLimitExceeded

from cinedata_agent.errors import describe_error, describe_error_for_user
from cinedata_agent.llm import ModelTimeoutError
from cinedata_agent.quota import parse_quota


def test_cota_diaria_esgotada():
    error = ModelHTTPError(429, "qwen", {"message": "Rate limit exceeded: free-models-per-day"})
    assert "21h" in describe_error(error)


def test_limite_por_minuto():
    error = ModelHTTPError(429, "qwen", {"message": "Rate limit exceeded: free-models-per-min"})
    assert "minuto" in describe_error(error)


def test_chave_invalida():
    assert "OPENROUTER_API_KEY" in describe_error(ModelHTTPError(401, "qwen"))


def test_todos_os_modelos_falharam_lista_cada_motivo():
    group = FallbackExceptionGroup(
        "all failed",
        [ModelHTTPError(429, "qwen", {"message": "upstream"}), ModelTimeoutError("nemotron", "x")],
    )
    message = describe_error(group)
    assert "qwen está sobrecarregado" in message
    assert "nemotron não respondeu a tempo" in message


def test_limite_de_requisicoes_por_pergunta():
    assert "reformulá-la" in describe_error(UsageLimitExceeded("limit"))


def test_leitura_da_cota():
    payload = {"data": {"free_model_daily_requests": {"used": 12, "limit": 50, "remaining": 38}}}
    quota = parse_quota(payload)
    assert (quota.used, quota.limit, quota.remaining) == (12, 50, 38)


def test_mensagens_para_o_usuario_final_nao_tem_detalhes_tecnicos():
    quota = ModelHTTPError(429, "qwen/qwen3.8-27b:free", {"message": "Rate limit exceeded: free-models-per-day"})
    busy = FallbackExceptionGroup("x", [ModelHTTPError(429, "qwen/qwen3.8-27b:free", {"message": "upstream"}), ModelTimeoutError("nemotron", "x")])
    messages = {
        "cota": describe_error_for_user(FallbackExceptionGroup("x", [quota])),
        "lotado": describe_error_for_user(busy),
        "chave": describe_error_for_user(ModelHTTPError(401, "qwen")),
        "consulta": describe_error_for_user(UsageLimitExceeded("limit")),
    }
    assert "21h" in messages["cota"]
    assert "sobrecarregado" in messages["lotado"]
    assert "responsável" in messages["chave"]
    assert "reformulá-la" in messages["consulta"]
    for text in messages.values():
        assert not any(term in text.lower() for term in ("qwen", "nemotron", "openrouter", "429", "requisi"))
