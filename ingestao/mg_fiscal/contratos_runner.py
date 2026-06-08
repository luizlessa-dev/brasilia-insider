"""
Runner MG Contratos — ingestão de contratos estaduais de Minas Gerais.

Uso:
  python -m ingestao.mg_fiscal.contratos_runner --ano 2025
  python -m ingestao.mg_fiscal.contratos_runner --todos
  python -m ingestao.mg_fiscal.contratos_runner --ano 2026 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from typing import Iterable

import requests

from .contratos_connector import ContratoMG, anos_contratos_disponiveis, iter_contratos

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mg_fiscal.contratos_runner")

CHUNK = 500


# ── Writer inline (reutiliza padrão do projeto) ────────────────────────────────

def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


class ContratosWriter:
    def __init__(self) -> None:
        self.url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.key = (
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("INTERNAL_SUPABASE_SERVICE_ROLE_KEY")
            or ""
        )
        if not self.url or not self.key:
            raise RuntimeError("Faltando SUPABASE_URL e/ou SUPABASE_SERVICE_ROLE_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        })

    def upsert(self, contratos: Iterable[ContratoMG]) -> int:
        buffer, total = [], 0
        for ct in contratos:
            buffer.append({k: _jsonable(v) for k, v in {
                "id": ct.id,
                "ano_assinatura": ct.ano_assinatura,
                "codigo_orgao": ct.codigo_orgao,
                "nome_orgao": ct.nome_orgao,
                "cnpj_cpf_fornecedor": ct.cnpj_cpf_fornecedor,
                "nome_fornecedor": ct.nome_fornecedor,
                "tipo_pessoa": ct.tipo_pessoa,
                "numero_processo": ct.numero_processo,
                "numero_contrato": ct.numero_contrato,
                "situacao": ct.situacao,
                "tipo_contrato": ct.tipo_contrato,
                "objeto": ct.objeto,
                "data_assinatura": ct.data_assinatura,
                "data_inicio_vigencia": ct.data_inicio_vigencia,
                "data_termino_vigencia": ct.data_termino_vigencia,
                "procedimento_contratacao": ct.procedimento_contratacao,
                "procedimento_detalhamento": ct.procedimento_detalhamento,
                "valor_total": ct.valor_total,
                "valor_empenhado": ct.valor_empenhado,
                "valor_liquidado": ct.valor_liquidado,
                "updated_at": datetime.utcnow().isoformat(),
            }.items()})
            if len(buffer) >= CHUNK:
                total += self._flush(buffer)
                buffer.clear()
                logger.info("mg_contratos: %d gravados…", total)
        if buffer:
            total += self._flush(buffer)
        return total

    def _flush(self, rows: list[dict]) -> int:
        resp = self.session.post(
            f"{self.url}/rest/v1/mg_contratos",
            params={"on_conflict": "id"},
            json=rows,
            timeout=60,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Upsert mg_contratos falhou ({resp.status_code}): {resp.text[:300]}")
        return len(rows)


# ── Runner ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingere contratos estaduais MG")
    parser.add_argument("--ano", type=int, action="append", dest="anos")
    parser.add_argument("--todos", action="store_true",
                        help=f"Todos os anos: {anos_contratos_disponiveis()}")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    anos = anos_contratos_disponiveis() if args.todos else (sorted(set(args.anos)) if args.anos else None)
    if not anos:
        parser.error("Informe --ano ANO ou --todos")

    writer = None if args.dry_run else ContratosWriter()

    for ano in anos:
        logger.info("=== MG Contratos %d ===", ano)
        n = 0
        try:
            cts = iter_contratos(ano)
            if args.dry_run:
                for _ in cts:
                    n += 1
                logger.info("dry-run %d: %d contratos parseados OK", ano, n)
            else:
                n = writer.upsert(cts)
                logger.info("mg_contratos %d: %d gravados", ano, n)
        except Exception as e:
            logger.error("mg_contratos %d falhou: %s", ano, e)
            raise

    logger.info("Concluído. Anos: %s", anos)


if __name__ == "__main__":
    main()
