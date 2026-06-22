"""
TSE — Tribunal Superior Eleitoral
Fonte: https://dadosabertos.tse.jus.br/
Datasets: candidatos, receitas e despesas de campanha

Encoding dos CSVs: latin-1
Delimitador: ponto-e-vírgula

URLs de download (ZIP contendo CSVs por UF + Brasil):
  Candidatos:
    https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_<ano>.zip
  Receitas (prestação de contas — candidatos):
    https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/
        prestacao_de_contas_eleitorais_candidatos_<ano>.zip
  Despesas (mesmo ZIP das receitas, arquivo diferente):
    mesma URL acima, arquivo interno: despesas_candidatos_<ano>_BRASIL.csv

Filtro de cargos ingeridos (CD_CARGO):
  Eleições gerais (anos pares terminados em 2, ex: 2022):
    1  → Presidente
    3  → Governador
    5  → Senador
    6  → Deputado Federal
    7  → Deputado Estadual / Distrital

  Eleições municipais (anos pares terminados em 4, ex: 2024):
    11 → Prefeito
    12 → Vice-Prefeito
    (13 = Vereador omitido — >500k registros, baixo valor pra cruzamento federal)
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("tse")

ENCODING = "latin-1"
DELIMITER = ";"

# Eleições gerais (2018, 2022, 2026…): federal + estadual
CARGOS_GERAIS = {"1", "3", "5", "6", "7"}
# Eleições municipais (2020, 2024…): prefeito + vice (omite vereador >500k registros)
CARGOS_MUNICIPAIS = {"11", "12"}
CARGOS_ALVO = CARGOS_GERAIS | CARGOS_MUNICIPAIS

CANDIDATOS_URL = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/"
    "consulta_cand_{ano}.zip"
)
# Receitas e despesas estão no mesmo ZIP
PRESTACAO_URL = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/"
    "prestacao_de_contas_eleitorais_candidatos_{ano}.zip"
)
RECEITAS_URL = PRESTACAO_URL  # alias mantido para compatibilidade


# ─── Modelos ──────────────────────────────────────────────────────────────────

@dataclass
class Candidato:
    id: str                          # "<ano>_<sq_candidato>"
    ano_eleicao: int
    sq_candidato: str
    cpf: Optional[str]
    nome: str
    nome_urna: Optional[str]
    data_nascimento: Optional[date]
    genero: Optional[str]
    cor_raca: Optional[str]
    grau_instrucao: Optional[str]
    ocupacao: Optional[str]
    estado_civil: Optional[str]
    email: Optional[str]
    cd_cargo: Optional[int]
    cargo: Optional[str]
    uf: Optional[str]
    municipio_nascimento: Optional[str]
    nr_partido: Optional[int]
    sigla_partido: Optional[str]
    nome_partido: Optional[str]
    situacao_candidatura: Optional[str]
    situacao_turno: Optional[str]
    reeleicao: Optional[bool]
    limite_despesa: Optional[float]


@dataclass
class Receita:
    ano_eleicao: int
    numero_recibo: Optional[str]
    cpf_candidato: Optional[str]
    nome_candidato: Optional[str]
    cargo: Optional[str]
    sigla_partido: Optional[str]
    uf: Optional[str]
    cpf_cnpj_doador: Optional[str]
    nome_doador: Optional[str]
    tipo_doador: Optional[str]
    setor_economico_doador: Optional[str]
    cpf_cnpj_doador_originario: Optional[str]
    nome_doador_originario: Optional[str]
    natureza_receita: Optional[str]
    origem_receita: Optional[str]
    especie_recurso: Optional[str]
    fonte_recurso: Optional[str]
    valor: float
    data_receita: Optional[date]
    data_prestacao_contas: Optional[date]


@dataclass
class Despesa:
    ano_eleicao: int
    numero_documento: Optional[str]
    cpf_candidato: Optional[str]
    nome_candidato: Optional[str]
    cargo: Optional[str]
    sigla_partido: Optional[str]
    uf: Optional[str]
    # fornecedor
    cpf_cnpj_fornecedor: Optional[str]
    nome_fornecedor: Optional[str]
    # classificação
    tipo_despesa: Optional[str]
    descricao_despesa: Optional[str]
    origem_despesa: Optional[str]
    especie_recurso: Optional[str]
    fonte_recurso: Optional[str]
    # valores
    valor_despesa: float
    valor_prestado: Optional[float]       # valor pago até a prestação
    # datas
    data_despesa: Optional[date]


# ─── Utilitários ──────────────────────────────────────────────────────────────

_DATE_BR = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _parse_date(v: str | None) -> Optional[date]:
    if not v or not v.strip():
        return None
    m = _DATE_BR.match(v.strip())
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _parse_float(v: str | None) -> Optional[float]:
    if not v or not v.strip():
        return None
    v = v.strip().replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _cpf(v: str | None) -> Optional[str]:
    """Remove formatação e retorna só dígitos; None se vazio, #NE#/#NULO# ou
    sentinela negativo do TSE (-1/-3/-4 = não divulgável/não aplicável).

    ATENÇÃO: `re.sub(r"\\D", "", "-4")` retornaria "4" — sem a guarda abaixo,
    todos os CPFs não-divulgados colapsariam no mesmo valor "4" e formariam um
    supergrupo falso ao agregar por CPF. Por isso negativos e comprimentos não
    canônicos (≠ 11 dígitos CPF / 14 CNPJ) viram None.
    """
    if not v or not v.strip() or v.strip() in ("#NE#", "#NULO#", ""):
        return None
    s = v.strip()
    if s.startswith("-"):
        return None
    d = re.sub(r"\D", "", s)
    return d if len(d) >= 11 else None   # CPF=11, CNPJ=14; < 11 é sentinela/lixo


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=4, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _download_zip(url: str, session: requests.Session) -> zipfile.ZipFile:
    logger.info("Baixando %s", url)
    resp = session.get(url, timeout=600, stream=True)
    resp.raise_for_status()
    buf = io.BytesIO()
    for chunk in resp.iter_content(chunk_size=4 << 20):  # 4 MB chunks
        buf.write(chunk)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def _csv_reader(zf: zipfile.ZipFile, filename: str) -> csv.DictReader:
    raw = zf.read(filename).decode(ENCODING, errors="replace")
    return csv.DictReader(io.StringIO(raw), delimiter=DELIMITER)


def _select_uf_files(zf: zipfile.ZipFile, prefix: str) -> list[str]:
    """Retorna arquivos por UF (ex: _MG.csv), excluindo BRASIL.
    Usar arquivos UF (~50 MB cada) em vez do BRASIL (~1-2 GB) para economizar memória.
    Fallback para BRASIL só se não houver arquivos por UF.
    """
    all_names = zf.namelist()
    logger.debug("_select_uf_files prefix=%r | total arquivos no ZIP: %d", prefix, len(all_names))

    # Arquivos por UF: <prefixo>_<UF>.csv  OU  <prefixo>_<ano>_<UF>.csv
    # (o ano já está no prefixo, então o padrão é prefixo_UF.csv)
    uf_files = sorted([
        n for n in all_names
        if re.search(rf"{re.escape(prefix)}_[A-Z]{{2}}\.csv$", n, re.IGNORECASE)
        and "BRASIL" not in n.upper()
    ])

    # Também aceitar padrão antigo: prefixo_\d+_UF.csv (ano no meio — não deveria ocorrer
    # quando passamos o ano no prefixo, mas cobre casos de mudança de estrutura do TSE)
    if not uf_files:
        uf_files = sorted([
            n for n in all_names
            if re.search(rf"{re.escape(prefix)}_\d+_[A-Z]{{2}}\.csv$", n, re.IGNORECASE)
            and "BRASIL" not in n.upper()
        ])

    if uf_files:
        logger.debug("Usando %d arquivos por UF (primeiro: %s)", len(uf_files), uf_files[0])
        return uf_files

    # Fallback: BRASIL (arquivo único, pode ser >1 GB — avisa explicitamente)
    brasil = sorted([n for n in all_names
                     if re.search(rf"{re.escape(prefix)}.*BRASIL.*\.csv$", n, re.IGNORECASE)])
    if brasil:
        logger.warning(
            "ATENÇÃO: nenhum arquivo por UF encontrado para prefix=%r. "
            "Usando arquivo BRASIL (%s) — pode ser muito grande (>1 GB).",
            prefix, brasil[0]
        )
        return brasil

    # Último fallback: qualquer CSV com o prefixo (excluindo BRASIL)
    fallback = sorted([
        n for n in all_names
        if re.search(rf"{re.escape(prefix)}.*\.csv$", n, re.IGNORECASE)
        and "BRASIL" not in n.upper()
    ])
    if fallback:
        return fallback

    logger.error("Nenhum arquivo CSV encontrado para prefix=%r. Arquivos disponíveis: %s",
                 prefix, all_names[:15])
    return []


# ─── Parser: Candidatos ───────────────────────────────────────────────────────

def _parse_candidatos_file(reader: csv.DictReader, ano: int) -> Iterator[Candidato]:
    for row in reader:
        if row.get("CD_CARGO", "").strip() not in CARGOS_ALVO:
            continue
        sq = row.get("SQ_CANDIDATO", "").strip()
        if not sq:
            continue
        yield Candidato(
            id=f"{ano}_{sq}",
            ano_eleicao=ano,
            sq_candidato=sq,
            cpf=_cpf(row.get("NR_CPF_CANDIDATO")),
            nome=row.get("NM_CANDIDATO", "").strip(),
            nome_urna=row.get("NM_URNA_CANDIDATO", "").strip() or None,
            data_nascimento=_parse_date(row.get("DT_NASCIMENTO")),
            genero=row.get("DS_GENERO", "").strip() or None,
            cor_raca=row.get("DS_COR_RACA", "").strip() or None,
            grau_instrucao=row.get("DS_GRAU_INSTRUCAO", "").strip() or None,
            ocupacao=row.get("DS_OCUPACAO", "").strip() or None,
            estado_civil=row.get("DS_ESTADO_CIVIL", "").strip() or None,
            email=row.get("NM_EMAIL", "").strip().lower() or None,
            cd_cargo=int(row["CD_CARGO"].strip()),
            cargo=row.get("DS_CARGO", "").strip() or None,
            uf=row.get("SG_UF", "").strip() or None,
            municipio_nascimento=row.get("NM_MUNICIPIO_NASCIMENTO", "").strip() or None,
            nr_partido=int(row["NR_PARTIDO"].strip()) if row.get("NR_PARTIDO", "").strip().isdigit() else None,
            sigla_partido=row.get("SG_PARTIDO", "").strip() or None,
            nome_partido=row.get("NM_PARTIDO", "").strip() or None,
            situacao_candidatura=row.get("DS_SITUACAO_CANDIDATURA", "").strip() or None,
            situacao_turno=row.get("DS_SIT_TOT_TURNO", "").strip() or None,
            reeleicao=row.get("ST_REELEICAO", "").strip().upper() == "S",
            limite_despesa=_parse_float(row.get("VR_DESPESA_MAX_CAMPANHA")),
        )


def get_candidatos(ano: int) -> list[Candidato]:
    """Baixa e parseia candidatos do TSE para o ano indicado."""
    session = _build_session()
    url = CANDIDATOS_URL.format(ano=ano)
    zf = _download_zip(url, session)

    # O ZIP contém um arquivo Brasil: consulta_cand_<ano>_BRASIL.csv
    brasil_files = [n for n in zf.namelist()
                    if re.search(r"BRASIL\.csv$", n, re.IGNORECASE)]
    if not brasil_files:
        # Fallback: arquivo sem sufixo de UF
        brasil_files = [n for n in zf.namelist() if n.endswith(".csv")]

    candidatos: list[Candidato] = []
    for fname in brasil_files:
        reader = _csv_reader(zf, fname)
        candidatos.extend(_parse_candidatos_file(reader, ano))
        logger.info("TSE candidatos %d — %s: %d registros", ano, fname, len(candidatos))

    logger.info("TSE candidatos %d: total %d (cargos alvo)", ano, len(candidatos))
    return candidatos


# ─── Parser: Receitas ─────────────────────────────────────────────────────────

def _parse_receitas_file(reader: csv.DictReader, ano: int) -> Iterator[Receita]:
    for row in reader:
        valor = _parse_float(row.get("valor") or row.get("VR_RECEITA"))
        if valor is None:
            continue
        yield Receita(
            ano_eleicao=ano,
            numero_recibo=(row.get("numero_recibo") or row.get("NR_RECIBO_ELEITORAL", "")).strip() or None,
            cpf_candidato=_cpf(row.get("cpf") or row.get("NR_CPF_CANDIDATO")),
            nome_candidato=(row.get("nome") or row.get("NM_CANDIDATO", "")).strip() or None,
            cargo=(row.get("cargo") or row.get("DS_CARGO", "")).strip() or None,
            sigla_partido=(row.get("sigla_partido") or row.get("SG_PARTIDO", "")).strip() or None,
            uf=(row.get("sigla_unidade_federativa") or row.get("SG_UF", "")).strip() or None,
            cpf_cnpj_doador=_cpf(row.get("cpf_cnpj_doador") or row.get("NR_CPF_CNPJ_DOADOR")),
            nome_doador=(row.get("doador") or row.get("NM_DOADOR", "")).strip() or None,
            tipo_doador=(row.get("tipo_doador_originario") or row.get("DS_ORIGEM_RECEITA", "")).strip() or None,
            setor_economico_doador=(row.get("setor_economico_doador") or "").strip() or None,
            cpf_cnpj_doador_originario=_cpf(row.get("cpf_cnpj_doador_originario")),
            nome_doador_originario=(row.get("doador_originario") or "").strip() or None,
            natureza_receita=(row.get("natureza_receita") or row.get("DS_NATUREZA_RECEITA", "")).strip() or None,
            origem_receita=(row.get("origem_receita") or row.get("DS_ORIGEM_RECEITA", "")).strip() or None,
            especie_recurso=(row.get("especie_recurso") or row.get("DS_ESPECIE_RECURSO", "")).strip() or None,
            fonte_recurso=(row.get("fonte_recurso") or row.get("DS_FONTE_RECURSO", "")).strip() or None,
            valor=valor,
            data_receita=_parse_date(row.get("data") or row.get("DT_RECEITA")),
            data_prestacao_contas=_parse_date(row.get("data_prestacao_contas")),
        )


def _open_prestacao_zip(ano: int) -> zipfile.ZipFile:
    """Baixa o ZIP de prestação de contas (receitas + despesas) para o ano."""
    session = _build_session()
    url = PRESTACAO_URL.format(ano=ano)
    return _download_zip(url, session)


def iter_receitas(ano: int, zf: zipfile.ZipFile | None = None) -> Iterator[Receita]:
    """Generator: itera receitas por UF, mantendo memória baixa (~50 MB por estado)."""
    if zf is None:
        zf = _open_prestacao_zip(ano)
    files = _select_uf_files(zf, f"receitas_candidatos_{ano}")
    if not files:
        logger.warning("TSE receitas %d: nenhum arquivo encontrado no ZIP. "
                       "Arquivos disponíveis: %s", ano, zf.namelist()[:10])
        return
    for fname in files:
        uf = re.search(r"_([A-Z]{2})\.csv$", fname)
        logger.info("TSE receitas %d — %s", ano, uf.group(1) if uf else fname)
        reader = _csv_reader(zf, fname)
        yield from _parse_receitas_file(reader, ano)


def get_receitas(ano: int) -> list[Receita]:
    """Baixa e retorna lista completa de receitas. Use iter_receitas() para datasets grandes."""
    return list(iter_receitas(ano))


# ─── Parser: Despesas ─────────────────────────────────────────────────────────

def _parse_despesas_file(reader: csv.DictReader, ano: int) -> Iterator[Despesa]:
    for row in reader:
        valor = _parse_float(
            row.get("vr_despesa_contratada") or row.get("VR_DESPESA_CONTRATADA")
            or row.get("valor") or row.get("VALOR")
        )
        if valor is None:
            continue
        yield Despesa(
            ano_eleicao=ano,
            numero_documento=(
                row.get("nr_documento_despesa") or row.get("NR_DOCUMENTO_DESPESA", "")
            ).strip() or None,
            cpf_candidato=_cpf(
                row.get("cpf_candidato") or row.get("NR_CPF_CANDIDATO")
                or row.get("nr_cpf_candidato")
            ),
            nome_candidato=(
                row.get("nome_candidato") or row.get("NM_CANDIDATO", "")
            ).strip() or None,
            cargo=(row.get("cargo") or row.get("DS_CARGO", "")).strip() or None,
            sigla_partido=(row.get("sigla_partido") or row.get("SG_PARTIDO", "")).strip() or None,
            uf=(
                row.get("sigla_uf") or row.get("SG_UF")
                or row.get("sg_uf", "")
            ).strip() or None,
            cpf_cnpj_fornecedor=_cpf(
                row.get("cpf_cnpj_fornecedor") or row.get("NR_CPF_CNPJ_FORNECEDOR")
            ),
            nome_fornecedor=(
                row.get("nome_fornecedor") or row.get("NM_FORNECEDOR", "")
            ).strip() or None,
            tipo_despesa=(
                row.get("tipo_despesa") or row.get("DS_TIPO_DESPESA", "")
            ).strip() or None,
            descricao_despesa=(
                row.get("descricao_despesa") or row.get("DS_DESPESA", "")
            ).strip() or None,
            origem_despesa=(
                row.get("origem_despesa") or row.get("DS_ORIGEM_DESPESA", "")
            ).strip() or None,
            especie_recurso=(
                row.get("especie_recurso") or row.get("DS_ESPECIE_RECURSO", "")
            ).strip() or None,
            fonte_recurso=(
                row.get("fonte_recurso") or row.get("DS_FONTE_RECURSO", "")
            ).strip() or None,
            valor_despesa=valor,
            valor_prestado=_parse_float(
                row.get("vr_despesa_paga") or row.get("VR_DESPESA_PAGA")
            ),
            data_despesa=_parse_date(
                row.get("dt_despesa") or row.get("DT_DESPESA")
                or row.get("data") or row.get("DATA")
            ),
        )


def iter_despesas(ano: int, zf: zipfile.ZipFile | None = None) -> Iterator[Despesa]:
    """Generator: itera despesas contratadas por UF, mantendo memória baixa.
    Usa 'despesas_contratadas_candidatos' (valor do contrato), não 'despesas_pagas'
    (valor efetivamente pago), que é mais relevante para investigações de emendas.
    """
    if zf is None:
        zf = _open_prestacao_zip(ano)
    # Prefixo real no TSE: despesas_contratadas_candidatos (não despesas_candidatos)
    files = _select_uf_files(zf, f"despesas_contratadas_candidatos_{ano}")
    if not files:
        logger.warning("TSE despesas %d: nenhum arquivo encontrado no ZIP. "
                       "Arquivos disponíveis (amostra): %s", ano, zf.namelist()[:10])
        return
    for fname in files:
        uf = re.search(r"_([A-Z]{2})\.csv$", fname)
        logger.info("TSE despesas %d — %s", ano, uf.group(1) if uf else fname)
        reader = _csv_reader(zf, fname)
        yield from _parse_despesas_file(reader, ano)


def get_despesas(ano: int) -> list[Despesa]:
    """Baixa e retorna lista completa de despesas. Use iter_despesas() para datasets grandes."""
    return list(iter_despesas(ano))
