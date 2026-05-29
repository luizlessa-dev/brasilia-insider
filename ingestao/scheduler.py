"""
Scheduler de ingestão — The Brasilia Insider
Roda todos os conectores implementados e reporta status.

Uso:
  python -m ingestao.scheduler --dias 7
  python -m ingestao.scheduler --assembly almg --dias 30
  python -m ingestao.scheduler --health-check
  python -m ingestao.scheduler --assembly alesp --no-persist   # fetch-only

Persistência: grava no Supabase se SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
estiverem no ambiente. Sem elas (ou com --no-persist), roda em modo fetch-only.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

from .base_connector import NotImplementedConnector, ConnectorError
from .connectors import REGISTRY, get_connector, all_connectors
from .persistence import SupabaseWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")


def run_health_checks() -> None:
    print(f"\n{'ASSEMBLEIA':<50} {'ID':<8} {'STATUS'}")
    print("─" * 75)
    ok = 0
    for connector in all_connectors():
        status = "✅ online" if connector.health_check() else "❌ offline"
        print(f"{connector.assembly_name:<50} {connector.assembly_id:<8} {status}")
        if "✅" in status:
            ok += 1
    print(f"\n{ok}/27 assembleias acessíveis\n")


ENTIDADES_DEFAULT = ("deputados", "proposicoes", "votacoes")


def run_ingestion(
    assembly_ids: list[str] | None,
    data_inicio: date,
    data_fim: date,
    persist: bool = True,
    entidades: tuple[str, ...] = ENTIDADES_DEFAULT,
) -> None:
    targets = assembly_ids or list(REGISTRY.keys())
    entidades = tuple(entidades)
    resultados = {"ok": [], "stub": [], "erro": []}
    logger.info("Entidades: %s", ", ".join(entidades))

    writer = SupabaseWriter.from_env() if persist else None
    if persist and writer is None:
        logger.warning("Sem credenciais Supabase — rodando em modo fetch-only.")
    elif not persist:
        logger.info("Modo fetch-only (--no-persist): nada será gravado.")

    for aid in targets:
        try:
            connector = get_connector(aid)
        except KeyError:
            logger.error("Assembly não encontrado: %s", aid)
            continue

        logger.info("▶ %s (%s)", connector.assembly_name, aid)
        run_id = None
        if writer:
            writer.upsert_casa(connector)
            run_id = writer.start_run(aid, data_inicio, data_fim)

        try:
            deps = connector.get_deputados() if "deputados" in entidades else []
            props = connector.get_proposicoes(data_inicio, data_fim) if "proposicoes" in entidades else []
            vots = connector.get_votacoes(data_inicio, data_fim) if "votacoes" in entidades else []
            logger.info(
                "  ✅ %d deputados | %d proposições | %d votações",
                len(deps), len(props), len(vots),
            )

            if writer:
                if "deputados" in entidades:
                    writer.upsert_deputados(deps)
                if "proposicoes" in entidades:
                    writer.upsert_proposicoes(props)
                if "votacoes" in entidades:
                    writer.upsert_votacoes(vots)
                writer.finish_run(run_id, "ok", {
                    "deputados": len(deps),
                    "proposicoes": len(props),
                    "votacoes": len(vots),
                })
                logger.info("  💾 gravado no Supabase")

            resultados["ok"].append(aid)

        except NotImplementedConnector:
            logger.info("  ⏳ %s — ainda não implementado (stub)", aid)
            if writer:
                writer.finish_run(run_id, "stub", {})
            resultados["stub"].append(aid)

        except ConnectorError as e:
            logger.warning("  ❌ %s — erro de conexão: %s", aid, e)
            if writer:
                writer.finish_run(run_id, "erro", {}, erro=str(e))
            resultados["erro"].append(aid)

        except Exception as e:
            logger.exception("  💥 %s — erro inesperado: %s", aid, e)
            if writer:
                writer.finish_run(run_id, "erro", {}, erro=str(e))
            resultados["erro"].append(aid)

    print(f"\n── Resumo ──────────────────────────────")
    print(f"  ✅ OK:    {len(resultados['ok'])} ({', '.join(resultados['ok']) or '—'})")
    print(f"  ⏳ Stubs: {len(resultados['stub'])}")
    print(f"  ❌ Erros: {len(resultados['erro'])} ({', '.join(resultados['erro']) or '—'})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestão de dados das assembleias estaduais")
    parser.add_argument("--dias", type=int, default=7, help="Janela de ingestão em dias (default: 7)")
    parser.add_argument("--assembly", nargs="*", help="IDs específicos (ex: almg alep). Default: todos.")
    parser.add_argument("--health-check", action="store_true", help="Apenas verifica conectividade")
    parser.add_argument("--no-persist", action="store_true", help="Fetch-only: não grava no banco")
    parser.add_argument(
        "--entidades", nargs="+", choices=list(ENTIDADES_DEFAULT), default=list(ENTIDADES_DEFAULT),
        help="Entidades a ingerir. Default: todas. Ex: --entidades deputados proposicoes "
             "(deixa votações, que é pesada, para um cron menos frequente).",
    )
    args = parser.parse_args()

    if args.health_check:
        run_health_checks()
        sys.exit(0)

    data_fim = date.today()
    data_inicio = data_fim - timedelta(days=args.dias)
    logger.info("Período: %s → %s", data_inicio, data_fim)

    run_ingestion(
        args.assembly, data_inicio, data_fim,
        persist=not args.no_persist, entidades=tuple(args.entidades),
    )


if __name__ == "__main__":
    main()
