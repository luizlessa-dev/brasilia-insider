"""
Runner TSE — executa ingestão de candidatos, receitas e/ou despesas.

Uso:
  python -m ingestao.tse.runner --dataset candidatos --ano 2024
  python -m ingestao.tse.runner --dataset receitas   --ano 2022
  python -m ingestao.tse.runner --dataset despesas   --ano 2024
  python -m ingestao.tse.runner --dataset todos      --ano 2022 --ano 2024

Variáveis de ambiente necessárias:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY  (ou INTERNAL_SUPABASE_SERVICE_ROLE_KEY)
"""
from __future__ import annotations

import argparse
import logging
import sys

from .connector import get_candidatos, iter_despesas, iter_receitas
from .persistence import TSEWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tse.runner")


def run_candidatos(writer: TSEWriter, ano: int) -> None:
    dataset = f"candidatos_{ano}"
    log_id = writer.start_log(dataset)
    try:
        candidatos = get_candidatos(ano)
        n = writer.upsert_candidatos(candidatos)
        writer.finish_log(log_id, "ok", n_processados=len(candidatos), n_novos=n)
        logger.info("candidatos %d: %d processados, %d gravados", ano, len(candidatos), n)
    except Exception as exc:
        writer.finish_log(log_id, "erro", erro=str(exc))
        logger.error("candidatos %d falhou: %s", ano, exc)
        raise


def run_receitas(writer: TSEWriter, ano: int) -> None:
    dataset = f"receitas_{ano}"
    log_id = writer.start_log(dataset)
    try:
        # iter_receitas é um generator — processa UF a UF sem acumular tudo na RAM
        n = writer.upsert_receitas(iter_receitas(ano), ano=ano)
        writer.finish_log(log_id, "ok", n_novos=n)
        logger.info("receitas %d: %d gravadas", ano, n)
    except Exception as exc:
        writer.finish_log(log_id, "erro", erro=str(exc))
        logger.error("receitas %d falhou: %s", ano, exc)
        raise


def run_despesas(writer: TSEWriter, ano: int) -> None:
    dataset = f"despesas_{ano}"
    log_id = writer.start_log(dataset)
    try:
        # iter_despesas é um generator — processa UF a UF sem acumular tudo na RAM
        n = writer.upsert_despesas(iter_despesas(ano), ano=ano)
        writer.finish_log(log_id, "ok", n_novos=n)
        logger.info("despesas %d: %d gravadas", ano, n)
    except Exception as exc:
        writer.finish_log(log_id, "erro", erro=str(exc))
        logger.error("despesas %d falhou: %s", ano, exc)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestão TSE → Supabase")
    parser.add_argument(
        "--dataset",
        choices=["candidatos", "receitas", "despesas", "todos"],
        required=True,
    )
    parser.add_argument(
        "--ano",
        type=int,
        action="append",
        dest="anos",
        required=True,
        help="Ano da eleição (pode repetir: --ano 2022 --ano 2024)",
    )
    args = parser.parse_args()

    writer = TSEWriter.from_env()
    if not writer:
        logger.error("Credenciais Supabase ausentes. Abortando.")
        sys.exit(1)

    writer.cleanup_stuck_logs()

    erros = 0
    for ano in args.anos:
        if args.dataset in ("candidatos", "todos"):
            try:
                run_candidatos(writer, ano)
            except Exception:
                erros += 1
        if args.dataset in ("receitas", "todos"):
            try:
                run_receitas(writer, ano)
            except Exception:
                erros += 1
        if args.dataset in ("despesas", "todos"):
            try:
                run_despesas(writer, ano)
            except Exception:
                erros += 1

    if erros:
        logger.error("%d dataset(s) falharam.", erros)
        sys.exit(1)
    logger.info("Ingestão TSE concluída.")


if __name__ == "__main__":
    main()
