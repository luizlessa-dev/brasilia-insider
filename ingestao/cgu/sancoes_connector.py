"""
Sanções — CEIS e CNEP · The Brasilia Insider
Cadastro de Empresas Inidôneas e Suspensas (CEIS) e
Cadastro Nacional de Empresas Punidas (CNEP).

API: GET https://api.portaldatransparencia.gov.br/api-de-dados/ceis
     GET https://api.portaldatransparencia.gov.br/api-de-dados/cnep

Autenticação: header "chave-api-dados" com chave gratuita.
  Registro: https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email

Parâmetros da API:
  codigoSancionado  — CPF/CNPJ (opcional, para consulta pontual)
  nomeSancionado    — nome (opcional)
  orgaoSancionador  — código do órgão (opcional)
  dataInicialSancao — DD/MM/AAAA (opcional)
  dataFinalSancao   — DD/MM/AAAA (opcional)
  pagina            — 1-based (OBRIGATÓRIO)

Campos do CeisDTO / CnepDTO (idênticos, exceto valorMulta em CNEP):
  id                  — PK da API (uso como chave de upsert)
  sancionado.codigoFormatado — CPF/CNPJ formatado (chave cruzamento)
  sancionado.nome
  pessoa.{cpfFormatado, cnpjFormatado, tipo, razaoSocialReceita, nomeFantasiaReceita}
  tipoSancao.{descricaoResumida, descricaoPortal}
  orgaoSancionador.{nome, siglaUf, poder, esfera}
  dataInicioSancao, dataFimSancao, dataPublicacaoSancao
  dataTransitadoJulgado, dataReferencia
  fundamentacao[]
  numeroProcesso
  valorMulta         — apenas CNEP
  textoPublicacao, linkPublicacao
  abrangenciaDefinidaDecisaoJudicial
  informacoesAdicionaisDoOrgaoSancionador

Estratégia de ingestão:
  - Full reload por janelas anuais (2013→ano atual)
    para contornar limite de paginação sem tamanhoPagina
  - IDs da API são PKs → upsert idempotente

Cruzamento estratégico:
  sancoes.cpf_cnpj × emendas_favorecidos.codigo_favorecido
  → empresa recebeu emenda E está sancionada (CEIS/CNEP)
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator, Literal, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("cgu.sancoes")

BASE_CEIS = "https://api.portaldatransparencia.gov.br/api-de-dados/ceis"
BASE_CNEP = "https://api.portaldatransparencia.gov.br/api-de-dados/cnep"
FIRST_YEAR = 2013        # CEIS/CNEP começam a ter dados consistentes a partir de 2013
PAGE_DELAY = 0.5         # segundos entre páginas (rate limit conservador)

Cadastro = Literal["CEIS", "CNEP"]


# ─── Modelos ──────────────────────────────────────────────────────────────────

@dataclass
class Sancao:
    # identificação
    id: int
    cadastro: Cadastro               # "CEIS" ou "CNEP"

    # sancionado
    cpf_cnpj: Optional[str]          # apenas dígitos — chave de cruzamento
    cpf_cnpj_formatado: Optional[str]
    tipo_pessoa: Optional[str]       # "PF" ou "PJ"
    nome: Optional[str]
    razao_social: Optional[str]
    nome_fantasia: Optional[str]

    # sanção
    tipo_sancao: Optional[str]       # descrição resumida
    descricao_sancao: Optional[str]  # descrição portal
    data_inicio: Optional[date]
    data_fim: Optional[date]
    data_publicacao: Optional[date]
    data_transitado: Optional[date]  # trânsito em julgado
    data_referencia: Optional[date]

    # órgão sancionador
    orgao_nome: Optional[str]
    orgao_uf: Optional[str]
    orgao_poder: Optional[str]       # Executivo, Judiciário…
    orgao_esfera: Optional[str]      # Federal, Estadual, Municipal

    # detalhes
    numero_processo: Optional[str]
    fundamentacao: list[str] = field(default_factory=list)
    valor_multa: Optional[str] = None   # apenas CNEP, mantido como str (formato variável)
    abrangencia: Optional[str] = None
    informacoes_adicionais: Optional[str] = None
    link_publicacao: Optional[str] = None


# ─── Utilitários ──────────────────────────────────────────────────────────────

def _strip_cpf_cnpj(v: str | None) -> Optional[str]:
    """Remove formatação (pontos, traços, barras) e retorna só dígitos."""
    if not v:
        return None
    digits = re.sub(r"\D", "", v.strip())
    return digits or None


def _parse_date(v: str | None) -> Optional[date]:
    if not v:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _build_session(api_key: str) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=2.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "chave-api-dados": api_key,
        "Accept": "application/json",
        "User-Agent": "BRInsider/1.0 (contato@thebrinsider.com)",
    })
    return session


def _parse_record(raw: dict, cadastro: Cadastro) -> Sancao:
    sancionado = raw.get("sancionado") or {}
    pessoa     = raw.get("pessoa") or {}
    tipo       = raw.get("tipoSancao") or {}
    orgao      = raw.get("orgaoSancionador") or {}
    fundam     = [
        f.get("descricao") or f.get("textoFormatado") or str(f)
        for f in (raw.get("fundamentacao") or [])
        if f
    ]

    # CPF/CNPJ: tenta sancionado.codigoFormatado > pessoa.cnpjFormatado > pessoa.cpfFormatado
    cpf_cnpj_fmt = (
        sancionado.get("codigoFormatado")
        or pessoa.get("cnpjFormatado")
        or pessoa.get("cpfFormatado")
    )
    # Tipo de pessoa baseado no comprimento do CNPJ (14 dígitos = PJ, 11 = PF)
    digits = _strip_cpf_cnpj(cpf_cnpj_fmt)
    if digits:
        tipo_pessoa = "PJ" if len(digits) == 14 else "PF"
    else:
        tipo_pessoa = pessoa.get("tipo")

    return Sancao(
        id=raw.get("id"),
        cadastro=cadastro,
        cpf_cnpj=digits,
        cpf_cnpj_formatado=cpf_cnpj_fmt,
        tipo_pessoa=tipo_pessoa,
        nome=sancionado.get("nome") or pessoa.get("nome"),
        razao_social=pessoa.get("razaoSocialReceita"),
        nome_fantasia=pessoa.get("nomeFantasiaReceita"),
        tipo_sancao=tipo.get("descricaoResumida"),
        descricao_sancao=tipo.get("descricaoPortal"),
        data_inicio=_parse_date(raw.get("dataInicioSancao")),
        data_fim=_parse_date(raw.get("dataFimSancao")),
        data_publicacao=_parse_date(raw.get("dataPublicacaoSancao")),
        data_transitado=_parse_date(raw.get("dataTransitadoJulgado")),
        data_referencia=_parse_date(raw.get("dataReferencia")),
        orgao_nome=orgao.get("nome"),
        orgao_uf=orgao.get("siglaUf"),
        orgao_poder=orgao.get("poder"),
        orgao_esfera=orgao.get("esfera"),
        numero_processo=raw.get("numeroProcesso"),
        fundamentacao=[str(f) for f in fundam if f],
        valor_multa=raw.get("valorMulta"),
        abrangencia=raw.get("abrangenciaDefinidaDecisaoJudicial"),
        informacoes_adicionais=raw.get("informacoesAdicionaisDoOrgaoSancionador"),
        link_publicacao=raw.get("linkPublicacao"),
    )


# ─── Conector ─────────────────────────────────────────────────────────────────

# Nomes dos parâmetros de data variam por endpoint (inconsistência da API):
#   CEIS → dataInicialSancao / dataFinalSancao
#   CNEP → dataInicioSancao  / dataFimSancao
DATE_PARAMS: dict[str, tuple[str, str]] = {
    "CEIS": ("dataInicialSancao", "dataFinalSancao"),
    "CNEP": ("dataInicioSancao",  "dataFimSancao"),
}


class SancoesConnector:
    """
    Itera sobre todos os registros do CEIS ou CNEP via API paginada.
    Usa janelas anuais para cobrir todo o histórico sem depender de
    tamanhoPagina (não documentado pela API).

    Atenção: os parâmetros de data têm nomes DIFERENTES por endpoint:
      CEIS → dataInicialSancao / dataFinalSancao
      CNEP → dataInicioSancao  / dataFimSancao
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError(
                "PORTAL_TRANSPARENCIA_API_KEY é obrigatória.\n"
                "Registre-se em: https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email"
            )
        self.api_key = api_key
        self.session = _build_session(api_key)
        self._last_req: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < PAGE_DELAY:
            time.sleep(PAGE_DELAY - elapsed)
        self._last_req = time.monotonic()

    def _fetch_page(self, url: str, cadastro: Cadastro,
                    pagina: int, ini: str, fim: str) -> list[dict]:
        self._throttle()
        param_ini, param_fim = DATE_PARAMS[cadastro]
        params = {
            param_ini: ini,
            param_fim: fim,
            "pagina":  pagina,
        }
        resp = self.session.get(url, params=params, timeout=45)
        if resp.status_code == 401:
            raise PermissionError("Chave da API inválida ou expirada.")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def _iter_year(self, url: str, cadastro: Cadastro, ano: int) -> Iterator[Sancao]:
        ini = f"01/01/{ano}"
        fim = f"31/12/{ano}"
        pagina = 1
        total = 0
        while True:
            records = self._fetch_page(url, cadastro, pagina, ini, fim)
            if not records:
                break
            for r in records:
                yield _parse_record(r, cadastro)
                total += 1
            logger.debug("%s %d: pág %d → %d acumulados", cadastro, ano, pagina, total)
            pagina += 1
        if total:
            logger.info("%s %d: %d registros", cadastro, ano, total)

    def iter_ceis(self, ano_inicio: int = FIRST_YEAR) -> Iterator[Sancao]:
        """Itera sobre todos os registros do CEIS (2013 → ano atual)."""
        ano_fim = datetime.utcnow().year
        for ano in range(ano_inicio, ano_fim + 1):
            yield from self._iter_year(BASE_CEIS, "CEIS", ano)

    def iter_cnep(self, ano_inicio: int = FIRST_YEAR) -> Iterator[Sancao]:
        """Itera sobre todos os registros do CNEP (2013 → ano atual)."""
        ano_fim = datetime.utcnow().year
        for ano in range(ano_inicio, ano_fim + 1):
            yield from self._iter_year(BASE_CNEP, "CNEP", ano)

    def iter_incremental_ceis(self, desde: date) -> Iterator[Sancao]:
        """Ingestão incremental do CEIS a partir de uma data."""
        ini = desde.strftime("%d/%m/%Y")
        fim = datetime.utcnow().strftime("%d/%m/%Y")
        pagina = 1
        while True:
            records = self._fetch_page(BASE_CEIS, "CEIS", pagina, ini, fim)
            if not records:
                break
            for r in records:
                yield _parse_record(r, "CEIS")
            pagina += 1

    def iter_incremental_cnep(self, desde: date) -> Iterator[Sancao]:
        """Ingestão incremental do CNEP a partir de uma data."""
        ini = desde.strftime("%d/%m/%Y")
        fim = datetime.utcnow().strftime("%d/%m/%Y")
        pagina = 1
        while True:
            records = self._fetch_page(BASE_CNEP, "CNEP", pagina, ini, fim)
            if not records:
                break
            for r in records:
                yield _parse_record(r, "CNEP")
            pagina += 1
