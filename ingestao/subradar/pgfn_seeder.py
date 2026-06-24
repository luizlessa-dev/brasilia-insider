"""
Seeder PGFN Dívida Ativa — bulk trimestral

Fonte: https://dadosabertos.pgfn.gov.br/
Arquivos por trimestre: Previdenciário, Não Previdenciário, FGTS

Uso:
  # Semeia o trimestre mais recente
  python3 -m ingestao.subradar.pgfn_seeder

  # Ciclo específico
  python3 -m ingestao.subradar.pgfn_seeder --ciclo 2026_trimestre_01

  # Só um tipo de arquivo (mais rápido para teste)
  python3 -m ingestao.subradar.pgfn_seeder --tipo previdenciario

Estratégia:
  - Baixa ZIP diretamente em memória (streaming para o menor, em disco para o grande)
  - Lê CSV com latin-1, normaliza CNPJ, converte datas e valores
  - Upsert em batches de 1000 linhas via Supabase REST
  - O arquivo Não Previdenciário tem ~1.2GB — processado em chunks sem carregar tudo
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import re
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ingestao.subradar.base import SUPABASE_URL, SUPABASE_KEY, _supabase_headers, _jsonable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("pgfn_seeder")

BASE_URL = "https://dadosabertos.pgfn.gov.br"
BATCH_SIZE = 300  # menor batch evita timeout de 60s do Supabase REST


def _upsert_pgfn(rows: list[dict]) -> None:
    """Upsert específico para pgfn_divida_ativa com coluna de conflito explícita."""
    if not rows or not SUPABASE_URL or not SUPABASE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/pgfn_divida_ativa"
    # on_conflict instrui o PostgREST a fazer DO NOTHING na constraint unique
    params = {"on_conflict": "numero_inscricao,ciclo"}
    headers = {
        **_supabase_headers(),
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    }
    batch = [_jsonable(r) for r in rows]
    for attempt in range(5):
        try:
            resp = requests.post(url, json=batch, headers=headers, params=params, timeout=60)
            if resp.ok:
                return
            if resp.status_code in (409, 429, 503):
                import time
                wait = 2 ** attempt
                logger.warning("upsert pgfn: %s — retry em %ds", resp.status_code, wait)
                time.sleep(wait)
                continue
            logger.error("upsert pgfn falhou: %s %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            import time
            wait = 2 ** attempt
            logger.warning("upsert pgfn: conexão perdida — retry em %ds", wait)
            time.sleep(wait)
    raise RuntimeError("upsert pgfn_divida_ativa: falhou após 5 tentativas")

ARQUIVOS = {
    "previdenciario":    "Dados_abertos_Previdenciario.zip",
    "nao_previdenciario": "Dados_abertos_Nao_Previdenciario.zip",
    "fgts":              "Dados_abertos_FGTS.zip",
}


def _ciclo_mais_recente() -> str:
    r = requests.get(BASE_URL + "/", timeout=20)
    trimestres = re.findall(r'(\d{4}_trimestre_\d{2})/', r.text)
    return sorted(trimestres)[-1] if trimestres else "2026_trimestre_01"


def _strip_cnpj(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def _parse_date(v: str) -> str | None:
    v = (v or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(v, fmt).date()
            if d.year < 1900 or d.year > 2100:
                return None
            return d.isoformat()
        except ValueError:
            pass
    return None


def _parse_valor(v: str) -> float | None:
    v = (v or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _row_to_dict(row: dict, arquivo: str, ciclo: str) -> dict | None:
    cnpj = _strip_cnpj(row.get("CPF_CNPJ", ""))
    # Filtra CPFs (11 dígitos) — só CNPJ (14 dígitos)
    if len(cnpj) != 14:
        return None
    num_inscricao = (row.get("NUMERO_INSCRICAO") or "").strip()
    if not num_inscricao:
        return None
    return {
        "cpf_cnpj":           cnpj,
        "tipo_pessoa":         (row.get("TIPO_PESSOA") or "").strip() or None,
        "tipo_devedor":        (row.get("TIPO_DEVEDOR") or "").strip() or None,
        "nome_devedor":        (row.get("NOME_DEVEDOR") or "").strip() or None,
        "uf_devedor":          (row.get("UF_DEVEDOR") or "").strip() or None,
        "unidade_responsavel": (row.get("UNIDADE_RESPONSAVEL") or "").strip() or None,
        "numero_inscricao":    num_inscricao,
        "tipo_situacao":       (row.get("TIPO_SITUACAO_INSCRICAO") or "").strip() or None,
        "situacao":            (row.get("SITUACAO_INSCRICAO") or "").strip() or None,
        "tipo_credito":        (row.get("TIPO_CREDITO") or "").strip() or None,
        "data_inscricao":      _parse_date(row.get("DATA_INSCRICAO", "")),
        "indicador_ajuizado":  (row.get("INDICADOR_AJUIZADO") or "").strip() or None,
        "valor_consolidado":   _parse_valor(row.get("VALOR_CONSOLIDADO", "")),
        "arquivo":             arquivo,
        "ciclo":               ciclo,
    }


def seed_arquivo(ciclo: str, tipo: str) -> int:
    nome_zip = ARQUIVOS[tipo]
    url = f"{BASE_URL}/{ciclo}/{nome_zip}"
    logger.info("Baixando %s ...", url)

    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()

    # Armazena em disco temporário para ZIPs grandes
    tmp_path = Path(f"/tmp/pgfn_{tipo}_{ciclo}.zip")
    total = 0
    with open(tmp_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            total += len(chunk)
    logger.info("Download concluído: %.1f MB", total / 1e6)

    linhas_inseridas = 0
    with zipfile.ZipFile(tmp_path) as z:
        for nome_csv in z.namelist():
            if not nome_csv.endswith(".csv"):
                continue
            logger.info("Processando %s ...", nome_csv)
            with z.open(nome_csv) as raw:
                reader = csv.DictReader(
                    io.TextIOWrapper(raw, encoding="latin-1"),
                    delimiter=";",
                )
                batch: list[dict] = []
                for row in reader:
                    d = _row_to_dict(row, tipo, ciclo)
                    if d:
                        batch.append(d)
                    if len(batch) >= BATCH_SIZE:
                        _upsert_pgfn(batch)
                        linhas_inseridas += len(batch)
                        batch = []
                        if linhas_inseridas % 50_000 == 0:
                            logger.info("  ... %d linhas inseridas", linhas_inseridas)
                if batch:
                    _upsert_pgfn(batch)
                    linhas_inseridas += len(batch)

    tmp_path.unlink(missing_ok=True)
    logger.info("Concluído %s: %d linhas", tipo, linhas_inseridas)
    return linhas_inseridas


def main() -> None:
    parser = argparse.ArgumentParser(description="Seeder PGFN Dívida Ativa")
    parser.add_argument("--ciclo", help="Ex: 2026_trimestre_01 (padrão: mais recente)")
    parser.add_argument(
        "--tipo",
        choices=list(ARQUIVOS.keys()) + ["todos"],
        default="todos",
        help="Tipo de arquivo a processar (padrão: todos)",
    )
    args = parser.parse_args()

    ciclo = args.ciclo or _ciclo_mais_recente()
    logger.info("Ciclo: %s", ciclo)

    tipos = list(ARQUIVOS.keys()) if args.tipo == "todos" else [args.tipo]

    total = 0
    for tipo in tipos:
        total += seed_arquivo(ciclo, tipo)

    logger.info("TOTAL GERAL: %d linhas inseridas", total)


if __name__ == "__main__":
    main()
