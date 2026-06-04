"""
CVM — Processos Sancionadores
Fonte: dados.cvm.gov.br
Dois CSVs dentro de um ZIP:
  - processo_sancionador.csv       (cabeçalho do processo)
  - processo_sancionador_acusado.csv (uma linha por acusado)
"""
from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger("cvm_connector")

ZIP_URL = "https://dados.cvm.gov.br/dados/PROCESSO/SANCIONADOR/DADOS/processo_sancionador.zip"
ENCODING = "latin-1"
SEP = ";"


@dataclass
class Processo:
    nup: str
    objeto: Optional[str]
    ementa: Optional[str]
    data_abertura: Optional[date]
    componente_instrucao: Optional[str]
    fase_atual: Optional[str]
    subfase_atual: Optional[str]
    local_atual: Optional[str]
    data_ultima_movimentacao: Optional[date]


@dataclass
class Acusado:
    id: str          # "<nup>_<idx>"
    nup: str
    nome_acusado: str
    situacao: Optional[str]
    data_situacao: Optional[date]


def _parse_date(val: str) -> Optional[date]:
    if not val or val.strip() == "":
        return None
    try:
        return datetime.strptime(val.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^A-Za-zÀ-ÿ0-9 ]", "", text.upper()).strip()


def fetch_zip(timeout: int = 60) -> bytes:
    session = requests.Session()
    session.headers["User-Agent"] = (
        "BRInsider/1.0 (bot de dados públicos; contato@thebrinsider.com)"
    )
    resp = session.get(ZIP_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def parse_processos(raw: bytes) -> list[Processo]:
    import csv
    reader = csv.DictReader(
        io.StringIO(raw.decode(ENCODING, errors="replace")),
        delimiter=SEP,
    )
    out = []
    for row in reader:
        out.append(Processo(
            nup=row["NUP"].strip(),
            objeto=row.get("Objeto", "").strip() or None,
            ementa=row.get("Ementa", "").strip() or None,
            data_abertura=_parse_date(row.get("Data_Abertura", "")),
            componente_instrucao=row.get("Componente_Organizacional_Instrucao", "").strip() or None,
            fase_atual=row.get("Fase_Atual", "").strip() or None,
            subfase_atual=row.get("Subfase_Atual", "").strip() or None,
            local_atual=row.get("Local_Atual", "").strip() or None,
            data_ultima_movimentacao=_parse_date(row.get("Data_Ultima_Movimentacao", "")),
        ))
    return out


def parse_acusados(raw: bytes) -> list[Acusado]:
    import csv
    reader = csv.DictReader(
        io.StringIO(raw.decode(ENCODING, errors="replace")),
        delimiter=SEP,
    )
    # conta por nup pra gerar id único
    contagem: dict[str, int] = {}
    out = []
    for row in reader:
        nup = row["NUP"].strip()
        nome = row.get("Nome_Acusado", "").strip()
        if not nup or not nome:
            continue
        contagem[nup] = contagem.get(nup, 0) + 1
        acusado_id = f"{nup}_{contagem[nup]:03d}"
        out.append(Acusado(
            id=acusado_id,
            nup=nup,
            nome_acusado=nome,
            situacao=row.get("Situacao", "").strip() or None,
            data_situacao=_parse_date(row.get("Data_Situacao", "")),
        ))
    return out


def load_all() -> tuple[list[Processo], list[Acusado]]:
    """Baixa o ZIP e retorna (processos, acusados)."""
    logger.info("Baixando ZIP de processos sancionadores…")
    raw_zip = fetch_zip()
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        processos = parse_processos(zf.read("processo_sancionador.csv"))
        acusados = parse_acusados(zf.read("processo_sancionador_acusado.csv"))
    logger.info("Parsed: %d processos, %d acusados", len(processos), len(acusados))
    return processos, acusados
