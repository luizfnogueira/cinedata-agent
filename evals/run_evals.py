"""Roda a suíte de avaliação do agente e gera o relatório em evals/resultados/.

Uso (com o venv ativo, na raiz do projeto):
    python evals/run_evals.py --dry-run            # só valida os SQLs de referência (0 requisições)
    python evals/run_evals.py                      # roda todos os casos
    python evals/run_evals.py --ids fin-01,ele-02  # só alguns casos
    python evals/run_evals.py --categoria Elenco   # só uma categoria (busca por trecho do nome)
    python evals/run_evals.py --sem-cache          # ignora respostas guardadas

Respostas ficam no cache: rodar de novo sem mudar o prompt custa 0 requisições.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from pydantic_ai import FallbackExceptionGroup

from cinedata_agent.config import PROJECT_ROOT, get_settings
from cinedata_agent.db import Database
from cinedata_agent.errors import describe_error
from cinedata_agent.evaluation import STATUS_ICON, CaseResult, EvalCase, load_cases, markdown_report, run_case, summarize
from cinedata_agent.llm import is_daily_quota_error
from cinedata_agent.quota import fetch_quota
from cinedata_agent.service import CineDataService

CASES_PATH = PROJECT_ROOT / "evals" / "casos.toml"
RESULTS_DIR = PROJECT_ROOT / "evals" / "resultados"

# Requisições estimadas por pergunta não respondida pelo cache (consulta + resposta)
ESTIMATED_REQUESTS = {"consulta": 2, "recusa": 1}


def select_cases(cases: list[EvalCase], ids: str | None, categoria: str | None) -> list[EvalCase]:
    if ids:
        wanted = {i.strip() for i in ids.split(",")}
        unknown = wanted - {c.id for c in cases}
        if unknown:
            raise SystemExit(f"Casos inexistentes: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in wanted]
    if categoria:
        cases = [c for c in cases if categoria.lower() in c.categoria.lower()]
    return cases


def dry_run(cases: list[EvalCase]) -> int:
    """Executa só os SQLs de referência: confere que rodam e mostra o início do resultado."""
    try:
        db = Database.from_settings(get_settings())
    except ValidationError:
        db = Database(PROJECT_ROOT / "cinerocket.db")
    problems = 0
    for case in cases:
        if case.tipo == "recusa":
            print(f"[{case.id}] recusa (sem SQL de referência)")
            continue
        result = db.execute(case.sql)
        missing = [c for c in case.colunas + ([case.ordem_por] if case.ordem_por else []) if c not in result.columns]
        status = "OK " if result.rows and not missing else "ERRO"
        problems += status == "ERRO"
        print(f"[{case.id}] {status} {len(result.rows)} linhas · {result.elapsed_seconds:.2f} s · {case.pergunta}")
        if missing:
            print(f"        colunas inexistentes no gabarito: {missing}")
        for row in result.rows[:3]:
            print(f"        {row}")
    print(f"\n{len(cases)} casos, {problems} com problema. Nenhuma requisição ao modelo foi feita.")
    return 1 if problems else 0


def is_quota_exhausted(result: CaseResult) -> bool:
    error = result.error
    if isinstance(error, FallbackExceptionGroup):
        return any(is_daily_quota_error(e) for e in error.exceptions)
    return error is not None and is_daily_quota_error(error)


def write_reports(results: list[CaseResult], name: str = "relatorio") -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    md_path = RESULTS_DIR / f"{name}.md"
    md_path.write_text(markdown_report(results, generated_at), encoding="utf-8", newline="\n")
    payload = {
        "gerado_em": generated_at,
        "resumo": summarize(results),
        "casos": [
            {
                "id": r.case.id,
                "categoria": r.case.categoria,
                "pergunta": r.case.pergunta,
                "status": r.status,
                "motivo": r.motivo,
                "tempo_s": round(r.elapsed_seconds, 2),
                "modelo": r.answer.model_name if r.answer else None,
                "requisicoes": r.answer.requests if r.answer else None,
                "do_cache": r.answer.cached if r.answer else None,
                "resposta": r.answer.answer if r.answer else None,
                "sql_agente": [q.sql for q in r.answer.queries] if r.answer else [],
            }
            for r in results
        ],
    }
    (RESULTS_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    return md_path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    parser = argparse.ArgumentParser(description="Avaliação do agente CineData.")
    parser.add_argument("--dry-run", action="store_true", help="só valida os SQLs de referência, sem chamar o modelo")
    parser.add_argument("--ids", help="ids separados por vírgula (ex.: fin-01,ele-02)")
    parser.add_argument("--categoria", help="filtra por trecho do nome da categoria")
    parser.add_argument("--sem-cache", action="store_true", help="ignora respostas guardadas")
    parser.add_argument("--pausa", type=float, default=3.0, help="segundos entre perguntas (limite: 20 req/min)")
    parser.add_argument("--forcar", action="store_true", help="roda mesmo se a cota estimada não for suficiente")
    args = parser.parse_args()

    cases = select_cases(load_cases(CASES_PATH), args.ids, args.categoria)
    if not cases:
        print("Nenhum caso selecionado.")
        return 1
    if args.dry_run:
        return dry_run(cases)

    settings = get_settings()
    print("Carregando o banco...", end=" ")
    service = CineDataService.from_settings(settings)
    print("pronto.")

    with service:  # fecha banco e conexões HTTP explicitamente ao sair
        use_cache = not args.sem_cache
        pending = [c for c in cases if not (use_cache and service.cache and service.cache.get(c.pergunta))]
        estimate = sum(ESTIMATED_REQUESTS[c.tipo] for c in pending)
        quota = fetch_quota(settings.openrouter_api_key.get_secret_value())
        print(f"{len(cases)} casos, {len(cases) - len(pending)} no cache. Estimativa: ~{estimate} requisições "
              f"(cota: {quota.remaining} restantes).")
        if estimate > quota.remaining and not args.forcar:
            print("Cota insuficiente para a estimativa. Rode menos casos (--ids/--categoria) ou use --forcar.")
            return 1

        results: list[CaseResult] = []
        for i, case in enumerate(cases):
            result = run_case(case, lambda q: service.ask(q, use_cache=use_cache), service.db, describe_error)
            results.append(result)
            a = result.answer
            origin = ("cache" if a.cached else f"{a.requests} req · {a.model_name}") if a else "-"
            detail = f" · {result.motivo}" if result.motivo else ""
            print(f"[{case.id}] {STATUS_ICON[result.status]} {result.status:<6} {origin} · {result.elapsed_seconds:.1f} s{detail}")
            if is_quota_exhausted(result):
                print("Cota diária esgotada: interrompendo. Os casos restantes não foram executados.")
                break
            if a and not a.cached and i < len(cases) - 1:
                time.sleep(args.pausa)

        summary = summarize(results)
        print(f"\nAcertos: {summary['acertos']}/{summary['total']} · requisições gastas: {summary['requisicoes']}"
              f" · do cache: {summary['do_cache']}")
        for category, (hits, total) in summary["por_categoria"].items():
            print(f"  {category}: {hits}/{total}")
        if summary["do_cache"] == len(results):
            # Sem nenhuma chamada real, tempos e requisições sairiam zerados: mantém o último relatório real.
            print("Todas as respostas vieram do cache: o relatório da última execução real foi mantido.")
        else:
            # Execução parcial (--ids/--categoria) não sobrescreve o relatório completo.
            name = "relatorio-parcial" if args.ids or args.categoria else "relatorio"
            print(f"Relatório: {write_reports(results, name).relative_to(PROJECT_ROOT)}")
        return 0 if summary["acertos"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
