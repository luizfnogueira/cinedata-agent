"""Interface de chat do agente CineData.

Rodar (com o venv ativo, na raiz do projeto):
    streamlit run app/streamlit_app.py
"""

from dataclasses import dataclass

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from cinedata_agent.agent import AgentAnswer
from cinedata_agent.charts import build_chart, plan_chart
from cinedata_agent.config import PROJECT_ROOT, get_settings
from cinedata_agent.db import QueryResult
from cinedata_agent.errors import AGENT_ERRORS, describe_error_for_user
from cinedata_agent.evaluation import load_cases
from cinedata_agent.formatting import format_cell, is_margin, money_symbol
from cinedata_agent.service import CineDataService

st.set_page_config(page_title="CineData Analytics", page_icon="🎬")

# Categorias de análise da atividade, na ordem em que aparecem como exemplos.
EXAMPLE_CATEGORIES = (
    "Bilheteria e Finanças",
    "Popularidade e Engajamento",
    "Elenco e Equipe",
    "Gêneros e Produtoras",
    "Avaliações dos Usuários",
)


@dataclass
class Turn:
    """Uma pergunta do histórico e o que voltou dela."""

    question: str
    answer: AgentAnswer | None = None
    error: str | None = None


# ── Recursos compartilhados ─────────────────────────────────────────────


@st.cache_resource(show_spinner="Carregando o catálogo de filmes...")
def get_service() -> CineDataService:
    # Um serviço para todas as sessões: o banco leva ~3 s para abrir.
    return CineDataService.from_settings(get_settings())


@st.cache_data(show_spinner=False)
def example_questions() -> dict[str, list[str]]:
    """Perguntas da suíte de avaliação (evals/casos.toml), agrupadas pelas categorias da atividade."""
    examples: dict[str, list[str]] = {category: [] for category in EXAMPLE_CATEGORIES}
    for case in load_cases(PROJECT_ROOT / "evals" / "casos.toml"):
        if case.tipo == "consulta" and case.categoria in examples:
            examples[case.categoria].append(case.pergunta)
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
        st.warning(turn.error, icon="⚠️")
        return
    answer = turn.answer
    st.markdown(escape_markdown(answer.answer))

    final = answer.final_query
    if final is None or not final.result.rows:
        return
    if plan := plan_chart(final.result):
        st.altair_chart(build_chart(plan, current_theme()), width="stretch")
    with st.expander("Ver dados da consulta"):
        st.dataframe(display_frame(final.result), hide_index=True, width="stretch")
        if final.result.truncated:
            st.caption(f"Mostrando as primeiras {len(final.result.rows)} linhas.")
        st.caption("Consulta SQL gerada pelo agente (somente leitura):")
        st.code(final.sql.strip(), language="sql")


def render_sidebar() -> None:
    with st.sidebar:
        st.subheader("Como usar")
        st.markdown(
            "Faça perguntas em português sobre o catálogo de filmes da CineData. O agente traduz a "
            "pergunta em uma consulta à camada Gold, executa e responde com os dados."
        )
        st.caption("Cada pergunta é respondida de forma independente. O agente só lê os dados: nada é alterado.")
        if st.button("Nova conversa", icon="🔄", width="stretch"):
            st.session_state.turns = []
            st.rerun()

        st.subheader("Perguntas de exemplo")
        for category, questions in example_questions().items():
            with st.expander(category):
                for question in questions:
                    if st.button(question, key=f"ex-{question}", width="stretch"):
                        st.session_state.pending_question = question
                        st.rerun()


# ── Página ──────────────────────────────────────────────────────────────


def main() -> None:
    st.title("🎬 CineData Analytics")
    st.caption("Análises do catálogo de filmes em linguagem natural: bilheteria, notas, elenco, gêneros e produtoras.")

    try:
        get_settings()
        service = get_service()
    except (ValidationError, FileNotFoundError):
        st.error("A aplicação não está configurada corretamente. Siga o passo a passo do README.", icon="🚫")
        st.stop()

    st.session_state.setdefault("turns", [])
    render_sidebar()

    for turn in st.session_state.turns:
        with st.chat_message("user"):
            st.markdown(escape_markdown(turn.question))
        with st.chat_message("assistant"):
            render_turn(turn)

    typed = st.chat_input("Ex.: Quais os 5 filmes mais populares?")
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
            try:
                turn.answer = service.ask(question)
            except AGENT_ERRORS as e:
                turn.error = describe_error_for_user(e)
        render_turn(turn)
    st.session_state.turns.append(turn)


main()
