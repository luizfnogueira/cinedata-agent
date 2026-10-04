"""Interface de chat do agente CineData.

Rodar (com o venv ativo, na raiz do projeto):
    streamlit run app/streamlit_app.py
"""

import time
from dataclasses import dataclass
from urllib.error import URLError

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from cinedata_agent.agent import AgentAnswer
from cinedata_agent.charts import build_chart, plan_chart
from cinedata_agent.config import PROJECT_ROOT, get_settings
from cinedata_agent.db import QueryResult
from cinedata_agent.errors import AGENT_ERRORS, describe_error
from cinedata_agent.evaluation import load_cases
from cinedata_agent.formatting import format_cell, is_margin, money_symbol
from cinedata_agent.quota import QuotaStatus, fetch_quota
from cinedata_agent.service import CineDataService

st.set_page_config(page_title="CineData · Analista de Filmes", page_icon="🎬")


@dataclass
class Turn:
    """Uma pergunta do histórico e o que voltou dela."""

    question: str
    answer: AgentAnswer | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0


# ── Recursos compartilhados ─────────────────────────────────────────────


@st.cache_resource(show_spinner="Carregando o catálogo de filmes...")
def get_service() -> CineDataService:
    # Um serviço para todas as sessões: o banco leva ~3 s para abrir.
    return CineDataService.from_settings(get_settings())


@st.cache_data(ttl=60, show_spinner=False)
def get_quota(api_key: str) -> QuotaStatus | None:
    try:
        return fetch_quota(api_key)
    except URLError:
        return None


@st.cache_data(show_spinner=False)
def example_questions() -> dict[str, list[str]]:
    examples: dict[str, list[str]] = {}
    for case in load_cases(PROJECT_ROOT / "evals" / "casos.toml"):
        if case.tipo == "consulta":
            examples.setdefault(case.categoria, []).append(case.pergunta)
    return examples


# ── Renderização ────────────────────────────────────────────────────────


def escape_markdown(text: str) -> str:
    # "$" vira fórmula matemática no markdown do Streamlit: "R$ 1 ... R$ 2" quebraria o texto.
    return text.replace("$", "\\$")


def display_frame(result: QueryResult) -> pd.DataFrame:
    """Tabela para leitura: dinheiro e margem formatados como na resposta; o resto, cru."""
    frame = pd.DataFrame(result.rows, columns=result.columns)
    for column in result.columns:
        if money_symbol(column) or is_margin(column):
            frame[column] = [format_cell(column, v) if v is not None else None for v in frame[column]]
    return frame


def current_theme() -> str:
    theme = getattr(st.context, "theme", None)
    return getattr(theme, "type", None) or "light"


def render_turn(turn: Turn) -> None:
    if turn.error:
        st.error(turn.error, icon="⚠️")
        return
    answer = turn.answer
    st.markdown(escape_markdown(answer.answer))

    if answer.cached:
        st.caption("⚡ Resposta do cache · 0 requisições")
    else:
        st.caption(f"{answer.model_name} · {answer.requests} requisições · {turn.elapsed_seconds:.1f} s")

    final = answer.final_query
    if final is None or not final.result.rows:
        return
    if plan := plan_chart(final.result):
        st.altair_chart(build_chart(plan, current_theme()), width="stretch")
    with st.expander("Ver dados e SQL"):
        st.dataframe(display_frame(final.result), hide_index=True, width="stretch")
        if final.result.truncated:
            st.caption(f"Mostrando as primeiras {len(final.result.rows)} linhas.")
        st.code(final.sql.strip(), language="sql")
        retries = sum(not q.succeeded for q in answer.queries)
        if retries:
            st.caption(f"O agente corrigiu {retries} consulta(s) com erro antes desta.")


def render_sidebar(api_key: str, models: list[str]) -> None:
    with st.sidebar:
        st.subheader("Cota gratuita de hoje")
        quota = get_quota(api_key)
        if quota is None:
            st.caption("Não foi possível consultar a cota agora.")
        else:
            st.metric("Requisições restantes", f"{quota.remaining} de {quota.limit}")
            st.progress(quota.remaining / quota.limit if quota.limit else 0.0)
            st.caption("Renova às 21h (Brasília). O contador do OpenRouter pode atrasar alguns minutos.")
        if st.button("Atualizar cota", width="stretch"):
            get_quota.clear()
            st.rerun()

        st.subheader("Configurações")
        st.toggle(
            "Usar respostas guardadas (cache)",
            value=True,
            key="use_cache",
            help="Perguntas já feitas voltam do cache sem gastar requisições.",
        )
        if st.button("Limpar conversa", width="stretch"):
            st.session_state.turns = []
            st.rerun()

        st.subheader("Modelos")
        st.caption("Se um falhar ou demorar, o próximo assume:")
        st.markdown("\n".join(f"{i}. `{name}`" for i, name in enumerate(models, start=1)))

        st.subheader("Perguntas de exemplo")
        for category, questions in example_questions().items():
            with st.expander(category):
                for question in questions:
                    if st.button(question, key=f"ex-{question}", width="stretch"):
                        st.session_state.pending_question = question
                        st.rerun()


# ── Página ──────────────────────────────────────────────────────────────


def main() -> None:
    st.title("🎬 CineData · Analista de Filmes")
    st.caption(
        "Pergunte em português sobre o catálogo de ~95 mil filmes: bilheteria, notas, elenco, gêneros e "
        "produtoras. Cada pergunta nova gasta ~2 requisições da cota gratuita; perguntas repetidas vêm do cache."
    )

    try:
        settings = get_settings()
    except ValidationError:
        st.error("OPENROUTER_API_KEY não configurada. Copie `.env.example` para `.env` e preencha a chave.")
        st.stop()
    try:
        service = get_service()
    except FileNotFoundError as e:
        st.error(str(e))
        st.stop()

    st.session_state.setdefault("turns", [])
    render_sidebar(settings.openrouter_api_key.get_secret_value(), settings.models)

    for turn in st.session_state.turns:
        with st.chat_message("user"):
            st.markdown(escape_markdown(turn.question))
        with st.chat_message("assistant"):
            render_turn(turn)

    typed = st.chat_input("Ex.: Quais os 5 filmes de terror com maior bilheteria?")
    question = typed or st.session_state.pop("pending_question", None)
    if not question:
        if not st.session_state.turns:
            st.info("Escreva uma pergunta abaixo ou escolha um exemplo na barra lateral.", icon="💬")
        return

    with st.chat_message("user"):
        st.markdown(escape_markdown(question))
    with st.chat_message("assistant"):
        turn = Turn(question)
        with st.spinner("Consultando o catálogo..."):
            start = time.monotonic()
            try:
                turn.answer = service.ask(question, use_cache=st.session_state.use_cache)
            except AGENT_ERRORS as e:
                turn.error = describe_error(e)
            turn.elapsed_seconds = time.monotonic() - start
        render_turn(turn)
    st.session_state.turns.append(turn)
    if turn.answer and not turn.answer.cached:
        get_quota.clear()  # a cota mudou: a barra lateral busca de novo na próxima interação


main()
