"""Agente Text-to-SQL: recebe uma pergunta em português, consulta o banco e responde.

Fluxo típico (2 requisições ao modelo):
1. o modelo lê o system prompt (schema + regras) e chama `run_sql` com uma consulta;
2. recebe o resultado e redige a resposta.
Se o SQL falhar, o erro volta ao modelo para correção (até MAX_SQL_RETRIES vezes).
"""

from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, ModelSettings, RunContext, UsageLimits
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from cinedata_agent.db import Database, QueryError, QueryResult
from cinedata_agent.formatting import format_cell
from cinedata_agent.guardrails import UnsafeQueryError
from cinedata_agent.prompts import build_system_prompt

# Tentativas extras quando o SQL gerado falha (guardrail, erro do SQLite ou timeout)
MAX_SQL_RETRIES = 2

# Teto de requisições por pergunta: 1 consulta + 2 correções + 1 resposta.
# Protege a cota diária de um modelo que entre em loop.
MAX_REQUESTS_PER_QUESTION = 4


@dataclass(frozen=True)
class ExecutedQuery:
    sql: str
    result: QueryResult | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.result is not None


@dataclass
class AgentDeps:
    db: Database
    queries: list[ExecutedQuery] = field(default_factory=list)


@dataclass(frozen=True)
class AgentAnswer:
    question: str
    answer: str
    queries: list[ExecutedQuery]
    model_name: str | None
    requests: int
    messages: list[ModelMessage]
    cached: bool = False

    @property
    def final_query(self) -> ExecutedQuery | None:
        """Última consulta bem-sucedida: a que embasou a resposta."""
        return next((q for q in reversed(self.queries) if q.succeeded), None)


def format_result_for_model(result: QueryResult) -> str:
    """Resultado em texto compacto: menos tokens que JSON e fácil de ler para o modelo.

    Dinheiro e margem já vão formatados (R$ 1,04 mi / 76,0%), para o modelo não
    precisar converter escala.
    """
    if not result.rows:
        return "A consulta não retornou nenhuma linha."
    lines = [" | ".join(result.columns)]
    lines += [" | ".join(format_cell(col, v) for col, v in zip(result.columns, row)) for row in result.rows]
    footer = f"({len(result.rows)} linhas"
    if result.truncated:
        footer += "; resultado truncado, existem mais linhas"
    lines.append(footer + ")")
    return "\n".join(lines)


def create_agent(model: Model | str | None = None) -> Agent[AgentDeps, str]:
    """Cria o agente. `model` pode ser omitido e passado depois em `ask` (útil nos testes)."""
    agent = Agent(
        model,
        deps_type=AgentDeps,
        output_type=str,
        retries={"tools": MAX_SQL_RETRIES},
        # temperature 0: queremos o SQL mais provável, não criatividade
        model_settings=ModelSettings(temperature=0),
    )

    @agent.instructions
    def system_prompt(ctx: RunContext[AgentDeps]) -> str:
        return build_system_prompt(ctx.deps.db.reference_year)

    @agent.tool
    def run_sql(ctx: RunContext[AgentDeps], query: str) -> str:
        """Executa uma consulta SQL de leitura (SQLite) no catálogo de filmes e retorna o resultado.

        Args:
            query: uma única consulta SELECT ou WITH, de preferência sobre as views vw_*.
        """
        try:
            result = ctx.deps.db.execute(query)
        except (UnsafeQueryError, QueryError) as e:
            ctx.deps.queries.append(ExecutedQuery(sql=query, error=str(e)))
            raise ModelRetry(f"A consulta falhou: {e}") from e
        ctx.deps.queries.append(ExecutedQuery(sql=query, result=result))
        return format_result_for_model(result)

    return agent


def ask(
    agent: Agent[AgentDeps, str],
    question: str,
    db: Database,
    *,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
) -> AgentAnswer:
    """Faz uma pergunta ao agente e devolve a resposta com as consultas executadas."""
    deps = AgentDeps(db=db)
    result = agent.run_sync(
        question,
        deps=deps,
        model=model,
        message_history=message_history,
        usage_limits=UsageLimits(request_limit=MAX_REQUESTS_PER_QUESTION),
    )
    return AgentAnswer(
        question=question,
        answer=result.output,
        queries=deps.queries,
        model_name=result.response.model_name,
        requests=result.usage.requests,
        messages=result.all_messages(),
    )
