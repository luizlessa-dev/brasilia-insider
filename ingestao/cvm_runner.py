"""
Runner de ingestão CVM — Processos Sancionadores
Uso:
  python -m ingestao.cvm_runner
  python -m ingestao.cvm_runner --dry-run      # só mostra contagens, não grava
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

import requests

from .cvm_connector import Acusado, Processo, load_all
from .persistence import SupabaseWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cvm_runner")

CHUNK = 500


def _jsonable(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _row_processo(p: Processo) -> dict:
    return {k: _jsonable(v) for k, v in asdict(p).items()}


def _row_acusado(a: Acusado) -> dict:
    d = asdict(a)
    return {k: _jsonable(v) for k, v in d.items()}


def upsert_processos(writer: SupabaseWriter, processos: list[Processo]) -> int:
    rows = [_row_processo(p) for p in processos]
    total = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        resp = writer.session.post(
            f"{writer.url}/rest/v1/cvm_processos",
            json=chunk,
            headers={
                "Prefer": "resolution=merge-duplicates,return=representation",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code not in (200, 201):
            logger.error("Erro upsert processos: %s %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        total += len(chunk)
        logger.info("  processos: %d/%d", total, len(rows))
    return total


def upsert_acusados(writer: SupabaseWriter, acusados: list[Acusado]) -> int:
    rows = [_row_acusado(a) for a in acusados]
    total = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        resp = writer.session.post(
            f"{writer.url}/rest/v1/cvm_acusados",
            json=chunk,
            headers={
                "Prefer": "resolution=merge-duplicates,return=representation",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code not in (200, 201):
            logger.error("Erro upsert acusados: %s %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        total += len(chunk)
        logger.info("  acusados: %d/%d", total, len(rows))
    return total


def log_ingest(writer: SupabaseWriter, status: str, n_proc: int, n_acus: int, erro: str | None) -> None:
    writer.session.post(
        f"{writer.url}/rest/v1/cvm_ingest_log",
        json={
            "dataset": "processos_sancionadores",
            "status": status,
            "n_processos": n_proc,
            "n_acusados": n_acus,
            "erro": erro,
            "finished_at": datetime.utcnow().isoformat(),
        },
        headers={"Content-Type": "application/json"},
    )


def main(dry_run: bool = False) -> None:
    processos, acusados = load_all()

    logger.info("Total: %d processos | %d acusados", len(processos), len(acusados))

    if dry_run:
        logger.info("--dry-run: nada gravado.")
        # mostra amostra de cruzamento possível
        nomes = {a.nome_acusado for a in acusados if "LTDA" in a.nome_acusado or "S.A" in a.nome_acusado}
        logger.info("Amostra PJ acusadas (%d total PJ):", len(nomes))
        for n in sorted(nomes)[:10]:
            print(" ", n)
        return

    writer = SupabaseWriter.from_env()
    if not writer:
        logger.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configurados.")
        sys.exit(1)

    try:
        n_proc = upsert_processos(writer, processos)
        n_acus = upsert_acusados(writer, acusados)
        log_ingest(writer, "ok", n_proc, n_acus, None)
        logger.info("Concluído: %d processos, %d acusados gravados.", n_proc, n_acus)
    except Exception as e:
        log_ingest(writer, "erro", 0, 0, str(e))
        logger.exception("Falha na ingestão CVM")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
