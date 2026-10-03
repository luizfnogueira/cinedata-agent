"""Interface de linha de comando do agente.

Uso:
    python cli.py "Quais os 10 filmes com maior receita?"   # uma pergunta
    python cli.py                                           # modo interativo
    python cli.py --modelo nvidia/nemotron-3.5-lightning:free "..."
"""

import argparse
import sys
import time

from pydantic import ValidationError
from pydantic_ai import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded

from cinedata_agent.agent import AgentAnswer, ask, create_agent
from cinedata_agent.config import get_settings
from cinedata_agent.db import Database
from cinedata_agent.llm import build_model


def explain_model_error(error: ModelHTTPError) -> str:
    body = str(error.body or "").lower()
    if error.status_code == 401:
        return "Chave do OpenRouter inválida ou ausente. Confira OPENROUTER_API_KEY no .env."
    if error.status_code == 429 and "per-day" in body:
        return "Cota diária de modelos gratuitos esgotada (50 requisições). Ela renova às 21h (horário de Brasília)."
    if error.status_code == 429:
        return "O provedor do modelo está sobrecarregado no momento. Tente de novo em instantes ou use outro modelo (--modelo)."
    return f"Erro {error.status_code} do OpenRouter: {error.body}"


def print_answer(answer: AgentAnswer) -> None:
    print(f"\n{answer.answer}\n")
    for i, query in enumerate(answer.queries, start=1):
        status = f"{len(query.result.rows)} linhas, {query.result.elapsed_seconds:.2f} s" if query.succeeded else f"ERRO: {query.error}"
        print(f"--- SQL {i} ({status})")
        print(query.sql.strip())
    print(f"--- modelo: {answer.model_name} · requisições: {answer.requests}\n")


def main(argv: list[str] | None = None) -> int:
    # Garante acentos corretos no console do Windows
    # (line_buffering: mostra o progresso mesmo com a saída redirecionada)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    parser = argparse.ArgumentParser(description="Pergunte ao catálogo de filmes da CineData em português.")
    parser.add_argument("pergunta", nargs="*", help="pergunta em linguagem natural (vazio = modo interativo)")
    parser.add_argument("--modelo", help="modelo do OpenRouter a usar (padrão: o primeiro de CINEDATA_MODELS)")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
        model = build_model(settings, args.modelo)
        print("Carregando o banco...", end=" ", flush=True)
        db = Database.from_settings(settings)
        print("pronto.")
    except ValidationError:
        print("OPENROUTER_API_KEY não configurada. Copie .env.example para .env e preencha a chave.")
        return 1
    except FileNotFoundError as e:
        print(e)
        return 1

    agent = create_agent(model)
    questions = [" ".join(args.pergunta)] if args.pergunta else None

    def handle(question: str) -> bool:
        try:
            start = time.monotonic()
            answer = ask(agent, question, db)
            print_answer(answer)
            print(f"(tempo total: {time.monotonic() - start:.1f} s)\n")
            return True
        except ModelHTTPError as e:
            print(explain_model_error(e))
        except ModelAPIError as e:
            print(f"{e} Tente de novo ou use outro modelo (--modelo).")
        except UsageLimitExceeded:
            print("O agente atingiu o limite de requisições para esta pergunta sem chegar a uma resposta. Tente reformulá-la.")
        except UnexpectedModelBehavior as e:
            print(f"O modelo não conseguiu gerar uma consulta válida: {e}")
        return False

    if questions:
        return 0 if handle(questions[0]) else 1

    print("Modo interativo. Digite sua pergunta (ou 'sair'). Cada pergunta gasta ~2 requisições da cota.")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question.lower() in {"sair", "exit", "quit"}:
            return 0
        if question:
            handle(question)


if __name__ == "__main__":
    raise SystemExit(main())
