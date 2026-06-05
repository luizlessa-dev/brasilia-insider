"""
CEAF Connector — The Brasilia Insider
Cadastro de Expulsões da Administração Federal.

API: GET https://api.portaldatransparencia.gov.br/api-de-dados/ceaf
Autenticação: header "chave-api-dados" com chave registrada em
  https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email

Parâmetros de busca (todos opcionais, exceto pagina):
  cpfSancionado, nomeSancionado, orgaoLotacao,
  dataPublicacaoInicio, dataPublicacaoFim (DD/MM/AAAA), pagina

Estratégia de ingestão full:
  - Percorre ano a ano de 2003 até o ano atual para evitar timeouts
    em janelas grandes (a API não documenta tamanhoPagina)
  - Página por página até receber lista vazia

Campos retornados (CeafDTO):
  id, dataPublicacao, dataReferencia,
  punicao.{cpfPunidoFormatado, nomePunido, portaria, processo, paginaDOU, secaoDOU}
  tipoPunicao.descricao,
  pessoa.{cpfFormatado, nome},
  orgaoLotacao.{siglaDaPasta, sigla, nome},
  ufLotacaoPessoa.uf,
  cargoEfetivo, cargoComissao,
  fundamentacao[].{codigo, descricao}
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("cgu.ceaf")

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados/ceaf"
FIRST_YEAR = 2003
PAGE_DELAY = 0.4   # segundos entre páginas (respeitar rate limit)


@dataclass
class Expulsao:
    id: int
    data_publicacao: Optional[date]
    data_referencia: Optional[date]
    cpf_punido: Optional[str]
    nome_punido: Optional[str]
    tipo_punicao: Optional[str]
    cargo_efetivo: Optional[str]
    cargo_comissao: Optional[str]
    orgao_sigla: Optional[str]
    orgao_pasta_sigla: Optional[str]
    orgao_nome: Optional[str]
    uf_lotacao: Optional[str]
    portaria: Optional[str]
    numero_processo: Optional[str]
    pagina_dou: Optional[str]
    secao_dou: Optional[str]
    fundamentacao: list[str] = field(default_factory=list)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:10], fmt[:len(value[:10].replace("-","/").split("/")[0])*3+2]).date()
        except Exception:
            pass
    # Fallback simples
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except Exception:
            continue
    return None


_SEM_INFO = {"sem informação", "sem informacao", "sem info", "", "-1", "none", "null"}


def _is_valid_uf(val: str) -> bool:
    """Retorna True se val parece um código de UF válido (2 letras, não é placeholder)."""
    v = val.strip().upper()
    return len(v) == 2 and v.isalpha() and v.lower() not in _SEM_INFO


def _extract_uf(uf_obj: dict) -> str | None:
    """
    Extrai o código de 2 letras da UF a partir de UFLotacaoDTO.
    Estrutura real da API (confirmada):
      ufLotacaoPessoa.uf.sigla = "SP"  (ou "Sem informação" quando ausente)

    Também trata variantes onde uf é string direta.
    """
    if not uf_obj:
        return None

    uf = uf_obj.get("uf")

    # Estrutura confirmada: uf é dict com campo "sigla"
    if isinstance(uf, dict):
        sigla = uf.get("sigla") or ""
        if _is_valid_uf(sigla):
            return sigla.strip().upper()
        # Fallback: tentar "nome" (às vezes contém a sigla)
        nome = uf.get("nome") or ""
        if _is_valid_uf(nome):
            return nome.strip().upper()
        return None

    # Variante: uf é string direta
    if isinstance(uf, str) and _is_valid_uf(uf):
        return uf.strip().upper()

    # Variante: sigla direta no uf_obj
    sigla_direta = uf_obj.get("sigla") or ""
    if _is_valid_uf(sigla_direta):
        return sigla_direta.strip().upper()

    return None


def _build_session(api_key: str) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=2.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({
        "chave-api-dados": api_key,
        "Accept": "application/json",
        "User-Agent": "BRInsider/1.0 (contato@thebrinsider.com)",
    })
    return session


def _parse_record(r: dict) -> Expulsao:
    punicao = r.get("punicao") or {}
    tipo    = r.get("tipoPunicao") or {}
    pessoa  = r.get("pessoa") or {}
    orgao   = r.get("orgaoLotacao") or {}
    uf_obj  = r.get("ufLotacaoPessoa") or {}
    fundam  = [
        f.get("descricao", "")
        for f in (r.get("fundamentacao") or [])
        if f.get("descricao")
    ]
    return Expulsao(
        id=r.get("id"),
        data_publicacao=_parse_date(r.get("dataPublicacao")),
        data_referencia=_parse_date(r.get("dataReferencia")),
        cpf_punido=punicao.get("cpfPunidoFormatado") or pessoa.get("cpfFormatado"),
        nome_punido=punicao.get("nomePunido") or pessoa.get("nome"),
        tipo_punicao=tipo.get("descricao"),
        cargo_efetivo=r.get("cargoEfetivo"),
        cargo_comissao=r.get("cargoComissao"),
        orgao_sigla=orgao.get("sigla"),
        orgao_pasta_sigla=orgao.get("siglaDaPasta"),
        orgao_nome=orgao.get("nome"),
        uf_lotacao=_extract_uf(uf_obj),
        portaria=punicao.get("portaria"),
        numero_processo=punicao.get("processo"),
        pagina_dou=punicao.get("paginaDOU"),
        secao_dou=punicao.get("secaoDOU"),
        fundamentacao=fundam,
    )


class CEAFConnector:
    """
    Itera sobre todos os registros do CEAF via API paginada.
    Estratégia: janela anual (dataPublicacaoInicio / dataPublicacaoFim)
    para garantir que nenhum registro seja pulado por limite de página.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("PORTAL_TRANSPARENCIA_API_KEY é obrigatória.")
        self.api_key = api_key
        self.session = _build_session(api_key)

    def _fetch_page(
        self,
        pagina: int,
        ini: str,
        fim: str,
    ) -> list[dict]:
        params = {
            "dataPublicacaoInicio": ini,
            "dataPublicacaoFim": fim,
            "pagina": pagina,
        }
        self._throttle()
        resp = self.session.get(BASE_URL, params=params, timeout=45)
        if resp.status_code == 401:
            raise PermissionError("Chave da API inválida ou expirada.")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    _last_req: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < PAGE_DELAY:
            time.sleep(PAGE_DELAY - elapsed)
        self._last_req = time.monotonic()

    def iter_year(self, ano: int) -> Iterator[Expulsao]:
        """Itera sobre todas as expulsões publicadas num determinado ano."""
        ini = f"01/01/{ano}"
        fim = f"31/12/{ano}"
        pagina = 1
        total = 0
        while True:
            records = self._fetch_page(pagina, ini, fim)
            if not records:
                break
            for r in records:
                yield _parse_record(r)
                total += 1
            logger.debug("CEAF %d: pág %d → %d registros acumulados", ano, pagina, total)
            pagina += 1
        if total:
            logger.info("CEAF %d: %d registros", ano, total)

    def load_full(self, ano_inicio: int = FIRST_YEAR) -> list[Expulsao]:
        """Carga completa desde ano_inicio até o ano atual."""
        ano_fim = datetime.utcnow().year
        resultado: list[Expulsao] = []
        for ano in range(ano_inicio, ano_fim + 1):
            resultado.extend(self.iter_year(ano))
            logger.info("CEAF: acumulado %d registros (até %d)", len(resultado), ano)
        return resultado

    def load_incremental(self, desde: date) -> list[Expulsao]:
        """Carga incremental a partir de uma data."""
        ini = desde.strftime("%d/%m/%Y")
        fim = datetime.utcnow().strftime("%d/%m/%Y")
        resultado: list[Expulsao] = []
        pagina = 1
        while True:
            records = self._fetch_page(pagina, ini, fim)
            if not records:
                break
            resultado.extend(_parse_record(r) for r in records)
            pagina += 1
        logger.info("CEAF incremental (%s→%s): %d registros", ini, fim, len(resultado))
        return resultado
