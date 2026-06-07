"""
Runner — Agenda Legislativa (Câmara + Senado)

Uso via CLI:
  python -m ingestao.agenda.runner [--dias N] [--data-inicio YYYY-MM-DD] [--data-fim YYYY-MM-DD]
  python -m ingestao.agenda.runner --backfill --ano 2025
  python -m ingestao.agenda.runner --camara-only
  python -m ingestao.agenda.runner --senado-only

Variáveis de ambiente obrigatórias:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta

# Supabase client
try:
    from supabase import create_client
except ImportError:
    create_client = None

from . import camara_connector, senado_connector, eagendas_connector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("agenda.runner")


def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórias")
        sys.exit(1)
    if create_client is None:
        logger.error("supabase-py não instalado. Execute: pip install supabase")
        sys.exit(1)
    return create_client(url, key)


def log_ingest(supabase, fonte: str, data_inicio: date, data_fim: date, resultado: dict):
    """Registra execução na tabela agenda_ingest_log."""
    status = "ok"
    n_ok = resultado.get("n_ok") or sum(
        v.get("n_ok", 0) for v in resultado.values() if isinstance(v, dict)
    )
    n_err = resultado.get("n_erros") or sum(
        v.get("n_erros", 0) for v in resultado.values() if isinstance(v, dict)
    )
    if n_err and not n_ok:
        status = "erro"
    elif n_err:
        status = "parcial"

    try:
        supabase.table("agenda_ingest_log").insert({
            "fonte": fonte,
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
            "status": status,
            "n_inseridos": n_ok,
            "n_atualizados": 0,
            "n_erros": n_err,
        }).execute()
    except Exception as e:
        logger.warning("Falha ao gravar log: %s", e)


def run_camara(supabase, data_inicio: date, data_fim: date) -> dict:
    logger.info("=== Câmara dos Deputados ===")
    resultado = camara_connector.run(supabase, data_inicio, data_fim)
    log_ingest(supabase, "camara", data_inicio, data_fim, resultado)
    return resultado


def run_senado(supabase, data_inicio: date, data_fim: date) -> dict:
    logger.info("=== Senado Federal ===")
    resultado = senado_connector.run(supabase, data_inicio, data_fim)
    log_ingest(supabase, "senado", data_inicio, data_fim, resultado)
    return resultado


def run_backfill(supabase, ano: int, fontes: list[str]):
    """Ingestão histórica por ano inteiro (em janelas mensais)."""
    logger.info("Backfill %d — fontes: %s", ano, fontes)
    from calendar import monthrange

    for mes in range(1, 13):
        _, ultimo_dia = monthrange(ano, mes)
        ini = date(ano, mes, 1)
        fim = date(ano, mes, ultimo_dia)

        if "camara" in fontes:
            run_camara(supabase, ini, fim)
        if "senado" in fontes:
            run_senado(supabase, ini, fim)


def main():
    parser = argparse.ArgumentParser(description="Ingestão de agenda legislativa")
    parser.add_argument("--dias", type=int, default=2, help="Janela em dias (padrão: 2)")
    parser.add_argument("--data-inicio", help="Data de início YYYY-MM-DD")
    parser.add_argument("--data-fim", help="Data de fim YYYY-MM-DD")
    parser.add_argument("--camara-only", action="store_true")
    parser.add_argument("--senado-only", action="store_true")
    parser.add_argument("--eagendas-only", action="store_true")
    parser.add_argument("--sem-eagendas", action="store_true", help="Pular e-Agendas (sem token)")
    parser.add_argument("--backfill", action="store_true", help="Ingestão histórica por ano")
    parser.add_argument("--ano", type=int, help="Ano para backfill (ex: 2024)")
    args = parser.parse_args()

    supabase = get_supabase()

    # Fontes ativas
    if args.camara_only:
        fontes = ["camara"]
    elif args.senado_only:
        fontes = ["senado"]
    elif args.eagendas_only:
        fontes = ["eagendas"]
    elif args.sem_eagendas:
        fontes = ["camara", "senado"]
    else:
        fontes = ["camara", "senado", "eagendas"]

    # Backfill histórico
    if args.backfill:
        if not args.ano:
            logger.error("--backfill requer --ano")
            sys.exit(1)
        run_backfill(supabase, args.ano, fontes)
        return

    # Período incremental
    hoje = date.today()
    if args.data_fim:
        data_fim = date.fromisoformat(args.data_fim)
    else:
        data_fim = hoje

    if args.data_inicio:
        data_inicio = date.fromisoformat(args.data_inicio)
    else:
        data_inicio = data_fim - timedelta(days=args.dias - 1)

    resultados = {}
    if "camara" in fontes:
        resultados["camara"] = run_camara(supabase, data_inicio, data_fim)
    if "senado" in fontes:
        resultados["senado"] = run_senado(supabase, data_inicio, data_fim)
    if "eagendas" in fontes:
        token = os.environ.get("EAGENDAS_TOKEN", "")
        if not token:
            logger.warning("EAGENDAS_TOKEN não definido — pulando e-Agendas")
        else:
            logger.info("=== e-Agendas (Executivo Federal) ===")
            resultado_ea = eagendas_connector.run(supabase, data_inicio, data_fim, token=token)
            log_ingest(supabase, "eagendas", data_inicio, data_fim, resultado_ea)
            resultados["eagendas"] = resultado_ea

    # Resumo final
    print("\n=== RESUMO ===")
    print(json.dumps(resultados, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
