"""Interface de linha de comando do agente.

Uso:
    python cli.py "Quais os 10 filmes com maior receita?"   # uma pergunta
    python cli.py                                           # modo interativo
    python cli.py --cota                                    # requisições restantes hoje
    python cli.py --sem-cache "..."                         # ignora respostas guardadas
    python cli.py --modelo nvidia/nemotron-3.5-lightning:free "..."   # um modelo só, sem fallback
"""

import argparse
import sys
import time
from urllib.error import URLError

from pydantic import ValidationError

from cinedata_agent.agent import AgentAnswer
from cinedata_agent.config import Settings, get_settings
from cinedata_agent.errors import AGENT_ERRORS, describe_error
from cinedata_agent.quota import fetch_quota
from cinedata_agent.service import CineDataService


def print_answer(answer: AgentAnswer, elapsed_seconds: float) -> None:
    print(f"\n{answer.answer}\n")
    for i, query in enumerate(answer.queries, start=1):
        if query.succeeded:
            status = f"{len(query.result.rows)} linhas, {query.result.elapsed_seconds:.2f} s"
        else:
            status = f"ERRO: {query.error}"
        print(f"--- SQL {i} ({status})")
        print(query.sql.strip())
    origin = "cache (0 requisições)" if answer.cached else f"requisições: {answer.requests}"
    print(f"--- modelo: {answer.model_name} · {origin} · tempo: {elapsed_seconds:.1f} s\n")


def print_quota(settings: Settings) -> int:
    try:
        quota = fetch_quota(settings.openrouter_api_key.get_secret_value())
    except URLError as e:
        print(f"Não foi possível consultar a cota: {e}")
        return 1
    print(f"Cota de hoje: {quota.used} usadas, {quota.remaining} de {quota.limit} restantes (renova às 21h).")
    print("O contador do OpenRouter pode atrasar alguns minutos.")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Garante acentos corretos no console do Windows
    # (line_buffering: mostra o progresso mesmo com a saída redirecionada)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    parser = argparse.ArgumentParser(description="Pergunte ao catálogo de filmes da CineData em português.")
    parser.add_argument("pergunta", nargs="*", help="pergunta em linguagem natural (vazio = modo interativo)")
    parser.add_argument("--modelo", help="usa um único modelo do OpenRouter, sem fallback")
    parser.add_argument("--sem-cache", action="store_true", help="ignora respostas guardadas e consulta o modelo")
    parser.add_argument("--limpar-cache", action="store_true", help="apaga todas as respostas guardadas e sai")
    parser.add_argument("--cota", action="store_true", help="mostra quantas requisições gratuitas restam hoje e sai")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ValidationError:
        print("OPENROUTER_API_KEY não configurada. Copie .env.example para .env e preencha a chave.")
        return 1

    if args.cota:
        return print_quota(settings)

    try:
        print("Carregando o banco...", end=" ")
        service = CineDataService.from_settings(settings, model_name=args.modelo)
        print("pronto.")
    except FileNotFoundError as e:
        print(f"\n{e}")
        return 1

    # "with": fecha banco e conexões HTTP explicitamente ao sair (ver CineDataService.close)
    with service:
        return run(service, args)


def run(service: CineDataService, args: argparse.Namespace) -> int:
    if args.limpar_cache:
        removed = service.cache.clear() if service.cache else 0
        print(f"{removed} respostas removidas do cache.")
        return 0

    def handle(question: str) -> bool:
        start = time.monotonic()
        try:
            answer = service.ask(question, use_cache=not args.sem_cache)
        except AGENT_ERRORS as e:
            print(describe_error(e))
            return False
        print_answer(answer, time.monotonic() - start)
        return True

    if args.pergunta:
        return 0 if handle(" ".join(args.pergunta)) else 1

    print("Modo interativo. Digite sua pergunta (ou 'sair'). Perguntas novas gastam ~2 requisições da cota.")
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
