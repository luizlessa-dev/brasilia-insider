"""
Runner Sanções (CEIS/CNEP) — The Brasilia Insider

Uso:
  python -m ingestao.cgu.sancoes_runner --dataset ceis
  python -m ingestao.cgu.sancoes_runner --dataset cnep
  python -m ingestao.cgu.sancoes_runner --dataset todos
  python -m ingestao.cgu.sancoes_runner --dataset ceis --desde 2024-01-01

Variáveis de ambiente necessárias:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  PORTAL_TRANSPARENCIA_API_KEY   — registre em portaldatransparencia.gov.br/api-de-dados/cadastrar-email
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

from .sancoes_connector import FIRST_YEAR, SancoesConnector
from .sancoes_persistence import SancoesWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cgu.sancoes.runner")


def run_ceis(connector: SancoesConnector, writer: SancoesWriter,
             desde: date | None = None) -> None:
    log_id = writer.start_log("ceis")
    try:
        if desde:
            gen = connector.iter_incremental_ceis(desde)
            desc = f"CEIS incremental (desde {desde})"
        else:
            gen = connector.iter_ceis(ano_inicio=FIRST_YEAR)
            desc = "CEIS full"
        n = writer.upsert_sancoes(gen)
        writer.finish_log(log_id, "ok", n_novos=n)
        logger.info("%s: %d registros upsertados", desc, n)
    except Exception as exc:
        writer.finish_log(log_id, "erro", erro=str(exc))
        logger.error("CEIS falhou: %s", exc)
        raise


def run_cnep(connector: SancoesConnector, writer: SancoesWriter,
             desde: date | None = None) -> None:
    log_id = writer.start_log("cnep")
    try:
        if desde:
            gen = connector.iter_incremental_cnep(desde)
            desc = f"CNEP incremental (desde {desde})"
        else:
            gen = connector.iter_cnep(ano_inicio=FIRST_YEAR)
            desc = "CNEP full"
        n = writer.upsert_sancoes(gen)
        writer.finish_log(log_id, "ok", n_novos=n)
        logger.info("%s: %d registros upsertados", desc, n)
    except Exception as exc:
        writer.finish_log(log_id, "erro", erro=str(exc))
        logger.error("CNEP falhou: %s", exc)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestão CEIS/CNEP → Supabase")
    parser.add_argument(
        "--dataset",
        choices=["ceis", "cnep", "todos"],
        required=True,
    )
    parser.add_argument(
        "--desde",
        type=lambda s: date.fromisoformat(s),
        default=None,
        metavar="AAAA-MM-DD",
        help="Se fornecido, faz ingestão incremental a partir desta data",
    )
    args = parser.parse_args()

    api_key = os.environ.get("PORTAL_TRANSPARENCIA_API_KEY")
    if not api_key:
        logger.error(
            "PORTAL_TRANSPARENCIA_API_KEY não configurada.\n"
            "Registre-se em: https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email\n"
            "E adicione a chave como secret PORTAL_TRANSPARENCIA_API_KEY no GitHub."
        )
        sys.exit(1)

    connector = SancoesConnector(api_key)

    writer = SancoesWriter.from_env()
    if not writer:
        logger.error("Credenciais Supabase ausentes. Abortando.")
        sys.exit(1)

    erros = 0
    if args.dataset in ("ceis", "todos"):
        try:
            run_ceis(connector, writer, desde=args.desde)
        except Exception:
            erros += 1

    if args.dataset in ("cnep", "todos"):
        try:
            run_cnep(connector, writer, desde=args.desde)
        except Exception:
            erros += 1

    if erros:
        logger.error("%d dataset(s) falharam.", erros)
        sys.exit(1)
    logger.info("Ingestão de sanções concluída.")


if __name__ == "__main__":
    main()
