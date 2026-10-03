"""Atalho para a CLI do agente: python cli.py "sua pergunta"."""

try:
    from cinedata_agent.cli import main
except ModuleNotFoundError:
    raise SystemExit(
        "Pacote cinedata_agent não encontrado: o ambiente virtual não está ativo.\n"
        "Ative-o com  .venv\\Scripts\\activate  (PowerShell) e rode de novo,\n"
        "ou chame direto:  .venv\\Scripts\\python cli.py \"sua pergunta\""
    ) from None

if __name__ == "__main__":
    raise SystemExit(main())
