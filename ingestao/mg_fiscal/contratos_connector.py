"""
MG Contratos Connector — The BR Insider
Fonte: Portal de Dados Abertos de MG — dados.mg.gov.br
Dataset: portal_contratos (Secretaria de Estado de Fazenda / SEF-MG)

Dois arquivos por ano:
  contratos<ano>.csv — um contrato por linha
  itens<ano>.csv     — itens de cada contrato (relacionado via numero_contrato)

Encoding: utf-8-sig (BOM)  |  Delimitador: ;  |  Atualização: semanal

URLs de download (resource IDs mapeados em 2026-06-05):
  Dataset ID: b27999c9-6151-4b86-8327-baa40b6d8983
"""
from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("mg_fiscal.contratos")

ENCODING  = "utf-8-sig"
DELIMITER = ";"
PAGE_DELAY = 0.5

_BASE = "https://dados.mg.gov.br/dataset/b27999c9-6151-4b86-8327-baa40b6d8983/resource"

_CONTRATOS_RESOURCES: dict[int, str] = {
    2022: f"{_BASE}/3031f542-1258-4ab0-9bdc-4aecdfe2f2b8/download/contratos2022.csv",
    2023: f"{_BASE}/cf997493-2d75-4857-a790-6a5a838c1af0/download/contratos2023.csv",
    2024: f"{_BASE}/b3707a04-5423-443e-83e2-2b4be69f4f36/download/contratos2024.csv",
    2025: f"{_BASE}/5b41c9b8-d168-41d5-b62b-3ee37ed458c5/download/contratos2025.csv",
    2026: f"{_BASE}/624696c2-0d55-496c-b6ca-87a97e6236c4/download/contratos2026.csv",
}

_ITENS_RESOURCES: dict[int, str] = {
    2022: f"{_BASE}/f7d72b21-d776-44cc-8f79-e99367de17c5/download/itens2022.csv",
    2023: f"{_BASE}/b2d8272e-f61d-4762-817a-85fa8d585083/download/itens2023.csv",
    2024: f"{_BASE}/2f86481f-b926-480a-8e46-436d843c8a40/download/itens2024.csv",
    2025: f"{_BASE}/090c8d43-3d75-4993-87d9-d20bf29093ad/download/itens2025.csv",
    2026: f"{_BASE}/dca8c5d4-3d78-48ab-9bbd-ecb30c12f00a/download/itens2026.csv",
}


# ── Modelos ────────────────────────────────────────────────────────────────────

@dataclass
class ContratoMG:
    id: str                          # "mg_ct_<ano>_<numero_contrato>"
    ano_assinatura: Optional[int]
    codigo_orgao: Optional[str]
    nome_orgao: Optional[str]
    cnpj_cpf_fornecedor: Optional[str]   # chave de cruzamento
    nome_fornecedor: Optional[str]
    tipo_pessoa: Optional[str]
    numero_processo: Optional[str]
    numero_contrato: Optional[str]
    situacao: Optional[str]
    tipo_contrato: Optional[str]
    objeto: Optional[str]
    data_assinatura: Optional[date]
    data_inicio_vigencia: Optional[date]
    data_termino_vigencia: Optional[date]
    procedimento_contratacao: Optional[str]
    procedimento_detalhamento: Optional[str]
    valor_total: Optional[float]
    valor_empenhado: Optional[float]
    valor_liquidado: Optional[float]


# ── Session ────────────────────────────────────────────────────────────────────

def _build_session() -> requests.Session:
    from ingestao.mg_fiscal.connector import _build_session as _base
    return _base()

_session = _build_session()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_date(value: str | None) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(value: str | None) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value.strip().replace(",", "."))
    except ValueError:
        return None


def _stream_csv(url: str) -> Iterator[dict]:
    logger.info("Baixando %s", url)
    time.sleep(PAGE_DELAY)
    resp = _session.get(url, timeout=120)
    resp.raise_for_status()
    try:
        text = resp.content.decode(ENCODING)
    except UnicodeDecodeError:
        text = resp.content.decode("latin-1")
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text), delimiter=DELIMITER)
    yield from reader


# ── Contratos ─────────────────────────────────────────────────────────────────

def iter_contratos(ano: int) -> Iterator[ContratoMG]:
    url = _CONTRATOS_RESOURCES.get(ano)
    if not url:
        raise ValueError(f"Ano {ano} não mapeado em _CONTRATOS_RESOURCES")

    seen: set[str] = set()
    for row in _stream_csv(url):
        row = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}

        num_ct = row.get("numero_contrato", "")
        ct_id = f"mg_ct_{ano}_{num_ct}"
        if ct_id in seen:
            continue
        seen.add(ct_id)

        yield ContratoMG(
            id=ct_id,
            ano_assinatura=int(row["ano_assinatura_contrato"]) if row.get("ano_assinatura_contrato", "").isdigit() else ano,
            codigo_orgao=row.get("codigo_orgao_entidade_contratante") or None,
            nome_orgao=row.get("nome_orgao_entidade_contratante") or None,
            cnpj_cpf_fornecedor=row.get("cnpj_cpf_fornecedor_formatado") or None,
            nome_fornecedor=row.get("nome_empresarial_nome_fornecedor") or None,
            tipo_pessoa=row.get("tipo_pessoa_fornecedor") or None,
            numero_processo=row.get("numero_processo_formatado") or None,
            numero_contrato=num_ct or None,
            situacao=row.get("situacao_contrato") or None,
            tipo_contrato=row.get("descricao_tipo_de_contrato") or None,
            objeto=row.get("objeto_contrato") or None,
            data_assinatura=_parse_date(row.get("data_assinatura_contrato")),
            data_inicio_vigencia=_parse_date(row.get("data_inicio_vigencia_contrato")),
            data_termino_vigencia=_parse_date(row.get("data_termino_vigencia_contrato")),
            procedimento_contratacao=row.get("procedimento_contratacao_grupo") or None,
            procedimento_detalhamento=row.get("procedimento_contratacao_detalhamento_1") or None,
            valor_total=_parse_float(row.get("valor_total_atualizado")),
            valor_empenhado=_parse_float(row.get("valor_despesa_empenhada")),
            valor_liquidado=_parse_float(row.get("valor_despesa_liquidada")),
        )


def anos_contratos_disponiveis() -> list[int]:
    return sorted(_CONTRATOS_RESOURCES.keys())
