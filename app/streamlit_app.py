"""Interface de chat do agente CineData, no estilo do Gemini.

Rodar (com o venv ativo, na raiz do projeto):
    streamlit run app/streamlit_app.py
"""

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from cinedata_agent.charts import build_chart, plan_chart
from cinedata_agent.config import PROJECT_ROOT, get_settings
from cinedata_agent.db import QueryResult
from cinedata_agent.errors import AGENT_ERRORS, describe_error_for_user
from cinedata_agent.evaluation import load_cases
from cinedata_agent.formatting import format_cell, is_margin, money_symbol
from cinedata_agent.history import ConversationStore, StoredTurn
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
# Sugestões da tela inicial (ids de evals/casos.toml), uma de cada tipo de análise.
SUGGESTION_IDS = ("fin-01", "pop-01", "ele-03", "gen-03")

STYLE = """
<style>
.cd-greeting { text-align: center; margin: 16vh 0 1.5rem; }
.cd-greeting h1 {
    font-size: 2.3rem; font-weight: 500; line-height: 1.25; padding: 0;
    background: linear-gradient(90deg, #4f8ff7, #9b72cb 55%, #d96570);
    -webkit-background-clip: text; background-clip: text; color: transparent;
}
.cd-greeting p { color: #9aa0a6; margin-top: .5rem; }
/* Itens da barra lateral alinhados à esquerda, como na lista de conversas do Gemini */
[data-testid="stSidebar"] .stButton button { justify-content: flex-start; text-align: left; }
[data-testid="stSidebar"] .stButton button p { text-align: left; }
</style>
"""


# ── Recursos compartilhados ─────────────────────────────────────────────


@st.cache_resource(show_spinner="Carregando o catálogo de filmes...")
def get_service() -> CineDataService:
    # Um serviço para todas as sessões: o banco leva ~3 s para abrir.
    return CineDataService.from_settings(get_settings())


@st.cache_resource
def get_store() -> ConversationStore:
    return ConversationStore(get_settings().cache_dir / "conversas.db")


@st.cache_data(show_spinner=False)
def load_examples() -> tuple[dict[str, list[str]], dict[str, str]]:
    """Perguntas de evals/casos.toml: agrupadas por categoria da atividade e indexadas por id."""
    by_category: dict[str, list[str]] = {category: [] for category in EXAMPLE_CATEGORIES}
    by_id: dict[str, str] = {}
    for case in load_cases(PROJECT_ROOT / "evals" / "casos.toml"):
        if case.tipo == "consulta" and case.categoria in by_category:
            by_category[case.categoria].append(case.pergunta)
            by_id[case.id] = case.pergunta
    return by_category, by_id


def ask_question(question: str) -> None:
    """Agenda a pergunta para a próxima execução da página (vale para digitada, sugestão ou exemplo)."""
    st.session_state.pending_question = question
    st.rerun()


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
    return getattr(theme, "type", None) or "dark"


def render_answer(turn: StoredTurn) -> None:
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


def render_turn(turn: StoredTurn) -> None:
    with st.chat_message("user"):
        st.markdown(escape_markdown(turn.question))
    with st.chat_message("assistant"):
        render_answer(turn)


def render_sidebar(store: ConversationStore) -> None:
    active_id = st.session_state.active_id
    with st.sidebar:
        st.markdown("### 🎬 CineData")
        if st.button("Nova conversa", icon=":material/edit_square:", type="tertiary", width="stretch"):
            st.session_state.active_id = None
            st.rerun()

        st.caption("Recentes")
        conversations = store.recent()
        if not conversations:
            st.caption("Suas conversas aparecem aqui.")
        for conversation in conversations:
            is_active = conversation.id == active_id
            title_col, menu_col = st.columns([0.84, 0.16], gap="small", vertical_alignment="center")
            with title_col:
                label = f"📌 {conversation.title}" if conversation.pinned else conversation.title
                if st.button(
                    label,
                    key=f"abrir-{conversation.id}",
                    type="secondary" if is_active else "tertiary",
                    width="stretch",
                    help=conversation.title,
                ):
                    st.session_state.active_id = conversation.id
                    st.rerun()
            with menu_col:
                with st.popover("", icon=":material/more_vert:", type="tertiary"):
                    pin_label = "Desafixar" if conversation.pinned else "Fixar"
                    if st.button(pin_label, key=f"fixar-{conversation.id}", icon=":material/keep:", type="tertiary"):
                        store.set_pinned(conversation.id, not conversation.pinned)
                        st.rerun()
                    if st.button("Excluir", key=f"excluir-{conversation.id}", icon=":material/delete:", type="tertiary"):
                        store.delete(conversation.id)
                        if is_active:
                            st.session_state.active_id = None
                        st.rerun()

        st.divider()
        st.caption("Perguntas de exemplo")
        examples, _ = load_examples()
        for category, questions in examples.items():
            with st.expander(category):
                for question in questions:
                    if st.button(question, key=f"ex-{question}", width="stretch"):
                        ask_question(question)

        st.divider()
        st.caption(
            "O agente traduz cada pergunta em uma consulta à camada Gold do catálogo e responde com os dados. "
            "Cada pergunta é respondida de forma independente, e nada no banco é alterado."
        )


def render_home() -> None:
    """Tela inicial: saudação no centro, caixa de pergunta logo abaixo e sugestões."""
    st.markdown(
        '<div class="cd-greeting"><h1>Olá! O que você quer saber<br/>sobre o catálogo de filmes?</h1>'
        "<p>Bilheteria, notas, elenco, gêneros e produtoras, em português.</p></div>",
        unsafe_allow_html=True,
    )
    # Dentro de um container, o chat_input fica no meio da página (e não fixo no rodapé).
    with st.container():
        typed = st.chat_input("Pergunte ao CineData", key="home_input")
    if typed:
        ask_question(typed)

    _, by_id = load_examples()
    suggestions = [by_id[i] for i in SUGGESTION_IDS if i in by_id]
    columns = st.columns(2)
    for i, question in enumerate(suggestions):
        with columns[i % 2]:
            if st.button(question, key=f"sug-{i}", width="stretch"):
                ask_question(question)


# ── Página ──────────────────────────────────────────────────────────────


def main() -> None:
    st.markdown(STYLE, unsafe_allow_html=True)
    try:
        get_settings()
        service = get_service()
        store = get_store()
    except (ValidationError, FileNotFoundError):
        st.error("A aplicação não está configurada corretamente. Siga o passo a passo do README.", icon="🚫")
        st.stop()

    st.session_state.setdefault("active_id", None)
    if st.session_state.active_id and not store.exists(st.session_state.active_id):
        st.session_state.active_id = None
    question = st.session_state.pop("pending_question", None)

    render_sidebar(store)

    if st.session_state.active_id is None and question is None:
        render_home()
        return

    is_new_conversation = st.session_state.active_id is None
    if is_new_conversation:
        st.session_state.active_id = store.create(question)
    conversation_id = st.session_state.active_id

    for turn in store.turns(conversation_id):
        render_turn(turn)

    if question:
        with st.chat_message("user"):
            st.markdown(escape_markdown(question))
        with st.chat_message("assistant"):
            answer, error = None, None
            with st.spinner("Consultando o catálogo..."):
                try:
                    answer = service.ask(question)
                except AGENT_ERRORS as e:
                    error = describe_error_for_user(e)
            render_answer(StoredTurn(question, answer, error))
        store.add_turn(conversation_id, question, answer, error)
        if is_new_conversation:
            st.rerun()  # atualiza a lista de recentes com a conversa nova

    typed = st.chat_input("Pergunte ao CineData", key="chat_input")
    if typed:
        ask_question(typed)


main()
