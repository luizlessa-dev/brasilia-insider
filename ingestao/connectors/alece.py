"""
ALECE — Assembleia Legislativa do Estado do Ceará
Tier 3 — SCRAPING HTML. Não há API JSON de atividade legislativa (a API
www2.al.ce.gov.br/api é só de gastos). Três fontes distintas, todas HTML:

  Deputados  : https://www.al.ce.gov.br/deputados
               cards .deputado_card (nome, partido, foto, link, licenciado).
  Proposições: http://www2.al.ce.gov.br/legislativo/proposicoes/numero.php
               ?nome={N}_legislatura&tabela={projeto_lei|projeto_indi|...}
               &opcao={D|T}&absolutepage={p}
               Uma <table> por proposição, campos rotulados em latin-1
               (Nº do Proj.:1/19, Autor:, Entrada:DD.MM.YY, Ementa:). 20/página.
  Votações   : https://transparencia.al.ce.gov.br/consultas-gerais/votacao-nominal
               ?legislatura={id}&sessao={id}  → tabela (DATA, MATÉRIA, DETALHE).
               DETALHE = PDF (uploads/votacao_nominal_materia/*.pdf). Capturamos
               a votação em nível de SESSÃO (data + matéria + link do PDF no raw);
               os votos NOMINAIS ficam no PDF e sua extração é sub-tarefa futura
               (detalhes=[] por ora).

ESCOPO (modelo de cron por janela de data):
- get_proposicoes/get_votacoes focam na LEGISLATURA ATUAL (31ª) e filtram por
  data. Backfill histórico (26ª–30ª) = estender LEGISLATURAS; é pesado
  (milhares de páginas) e deve rodar manual, não no cron diário.
- Scraping é frágil por natureza: quebra se o CMS mudar. Há cap de páginas com
  log (sem truncagem silenciosa).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..base_connector import BaseConnector, ConnectorError
from ..models import Deputado, Proposicao, Votacao


DEP_URL = "https://www.al.ce.gov.br/deputados"
PROP_BASE = "http://www2.al.ce.gov.br/legislativo/proposicoes/numero.php"
VOT_BASE = "https://transparencia.al.ce.gov.br/consultas-gerais/votacao-nominal"

# Legislatura atual no path de proposições e no id do form de votação.
LEGISLATURAS_PROP = ["31"]            # estender p/ ["31","30",...] em backfill
LEGISLATURA_VOT = "1"                 # id do <select legislatura> = 31ª
TABELAS = ["projeto_lei", "projeto_indi"]
OPCOES = ["T", "D"]                   # Tramitando, Deliberados
MAX_PAGINAS = 80                      # cap de segurança por (tabela,opcao)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(t: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", (t or "").lower()).encode("ascii", "ignore").decode()
    return _SLUG_RE.sub("-", s).strip("-")


class ALECEConnector(BaseConnector):
    assembly_id = "alece"
    assembly_name = "Assembleia Legislativa do Ceará"
    uf = "CE"
    base_url = "https://www.al.ce.gov.br"

    request_delay = 0.6
    timeout = 45

    # ── Deputados (scrape de cards) ───────────────────────────────────────
    def get_deputados(self) -> list[Deputado]:
        html = self._get_text(DEP_URL)
        soup = BeautifulSoup(html, "html.parser")
        deputados: list[Deputado] = []
        for card in soup.select(".deputado_card"):
            link_el = card.select_one(".deputado_card--nome a")
            nome = (link_el.get_text(strip=True) if link_el else "").strip()
            if not nome:
                continue
            href = link_el.get("href") if link_el else None
            partido_el = card.select_one(".deputado_card--partido")
            partido = (partido_el.get_text(strip=True) if partido_el else "").strip()
            img = card.find("img")
            classes = card.get("class", [])
            # id estável a partir do slug do link, senão do nome
            raw_id = href.rstrip("/").rsplit("/", 1)[-1] if href else _slug(nome)
            deputados.append(Deputado(
                id=self._prefix_id(raw_id),
                nome=nome,
                partido=partido,
                uf="CE",
                assembly_id=self.assembly_id,
                foto_url=img.get("src") if img else None,
                raw={
                    "url": href,
                    "licenciado": "licenciado" in classes,
                },
            ))
        self.logger.info("ALECE: %d deputados carregados", len(deputados))
        return deputados

    # ── Proposições (scrape paginado, latin-1) ────────────────────────────
    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        proposicoes: list[Proposicao] = []
        vistos: set[str] = set()
        for leg in LEGISLATURAS_PROP:
            for tabela in TABELAS:
                for opcao in OPCOES:
                    proposicoes.extend(
                        self._scrape_proposicoes(leg, tabela, opcao, data_inicio, data_fim, vistos)
                    )
        self.logger.info(
            "ALECE: %d proposições carregadas (%s → %s)",
            len(proposicoes), data_inicio, data_fim,
        )
        return proposicoes

    def _fetch_prop_page(self, leg, tabela, opcao, pagina) -> str | None:
        params = {
            "nome": f"{leg}_legislatura",
            "tabela": tabela,
            "opcao": opcao,
            "absolutepage": pagina,
        }
        try:
            raw = self.session.get(PROP_BASE, params=params, timeout=self.timeout)
            raw.raise_for_status()
            return raw.content.decode("utf-8", errors="replace")
        except Exception as e:
            self.logger.warning("ALECE prop %s/%s p%d: %s", tabela, opcao, pagina, e)
            return None

    @staticmethod
    def _parse_props_da_pagina(html):
        """Retorna [(numero, ano, dt, autor, ementa, texto), ...] da página."""
        soup = BeautifulSoup(html, "html.parser")
        itens = []
        for tab in soup.find_all("table"):
            texto = re.sub(r"\s+", " ", tab.get_text(" "))
            m_num = re.search(r"N[ºo°]?\s*do\s*Proj\.?:\s*(\d+)\s*/\s*(\d+)", texto)
            if not m_num:
                continue
            numero, ano2 = m_num.group(1), m_num.group(2)
            ano = 2000 + int(ano2) if len(ano2) == 2 else int(ano2)
            m_ent = re.search(r"Entrada:\s*([\d./]+)", texto)
            dt = ALECEConnector._parse_ddmmyy(m_ent.group(1)) if m_ent else None
            m_aut = re.search(r"Autor:\s*(.*?)\s*(?:Entrada:|Expediente:|Ementa:|$)", texto)
            m_eme = re.search(r"Ementa:\s*(.*?)\s*(?:Descri|Diss|$)", texto)
            itens.append((
                numero, ano, dt,
                m_aut.group(1).strip() if m_aut else None,
                m_eme.group(1).strip() if m_eme else "",
                texto[:500],
            ))
        return itens

    def _scrape_proposicoes(self, leg, tabela, opcao, data_inicio, data_fim, vistos) -> list[Proposicao]:
        # Página 1 informa o total. A lista é por número crescente (≈ cronológica),
        # então itens recentes ficam nas ÚLTIMAS páginas. Varremos de trás pra
        # frente e paramos quando uma página inteira é anterior à janela — assim
        # janelas recentes leem poucas páginas em vez de toda a legislatura.
        html1 = self._fetch_prop_page(leg, tabela, opcao, 1)
        if not html1:
            return []
        ultimas = [int(m) for m in re.findall(r"absolutepage=(\d+)", html1)]
        total = max(ultimas) if ultimas else 1
        total = min(total, MAX_PAGINAS)

        out: list[Proposicao] = []
        paginas_lidas = 0
        for pagina in range(total, 0, -1):
            html = html1 if pagina == 1 else self._fetch_prop_page(leg, tabela, opcao, pagina)
            if not html:
                continue
            paginas_lidas += 1
            itens = self._parse_props_da_pagina(html)
            datas_validas = [it[2] for it in itens if it[2]]
            for numero, ano, dt, autor, ementa, texto in itens:
                if dt and not (data_inicio <= dt <= data_fim):
                    continue
                pid = f"{tabela}-{numero}-{ano}"
                if pid in vistos:
                    continue
                vistos.add(pid)
                out.append(Proposicao(
                    id=self._prefix_id(pid),
                    numero=numero,
                    ano=ano,
                    tipo="PL" if tabela == "projeto_lei" else "INDICACAO",
                    ementa=ementa,
                    assembly_id=self.assembly_id,
                    autor=autor,
                    data_apresentacao=dt,
                    raw={"texto": texto, "leg": leg, "opcao": opcao},
                ))
            # parada antecipada: se a página toda já é anterior à janela, as
            # anteriores (mais antigas ainda) também serão — pode parar.
            if datas_validas and max(datas_validas) < data_inicio:
                break
            if paginas_lidas >= MAX_PAGINAS:
                self.logger.warning(
                    "ALECE prop %s/%s/%s: cap de %d páginas — pode faltar histórico.",
                    leg, tabela, opcao, MAX_PAGINAS,
                )
                break
        return out

    # ── Votações (form legislatura+sessao → tabela de sessões) ────────────
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        # descobre as sessões disponíveis na página base
        try:
            base_html = self._get_text(VOT_BASE)
        except Exception as e:
            self.logger.warning("ALECE votações: base inacessível: %s", e)
            return []
        soup = BeautifulSoup(base_html, "html.parser")
        sessoes = []
        for sel in soup.find_all("select"):
            if sel.get("name") == "sessao":
                sessoes = [o.get("value") for o in sel.find_all("option") if o.get("value")]
                break

        votacoes: list[Votacao] = []
        for sessao in sessoes:
            try:
                html = self._get_text(f"{VOT_BASE}?legislatura={LEGISLATURA_VOT}&sessao={sessao}")
            except Exception as e:
                self.logger.debug("ALECE votação sessão %s: %s", sessao, e)
                continue
            s = BeautifulSoup(html, "html.parser")
            for tr in s.find_all("tr"):
                cells = tr.find_all("td")
                if len(cells) < 5:
                    continue
                txt = [re.sub(r"\s+", " ", c.get_text(" ")).strip() for c in cells]
                dt = self._parse_data(txt[3]) if len(txt) > 3 else None
                if not dt or not (data_inicio <= dt <= data_fim):
                    continue
                materia = txt[4] if len(txt) > 4 else ""
                detalhe_a = cells[-1].find("a")
                detalhe_url = urljoin(VOT_BASE, detalhe_a.get("href")) if detalhe_a and detalhe_a.get("href") else None
                m_prop = re.match(r"\s*(\d+)\s*/\s*(\d+)", materia)
                vid = _slug(f"{sessao}-{m_prop.group(0).strip() if m_prop else materia[:20]}")
                # Os votos nominais da ALECE são PDFs (uploads/votacao_nominal_materia/*.pdf),
                # não HTML. Guardamos a sessão + link do PDF; extração do PDF é sub-tarefa.
                votacoes.append(Votacao(
                    id=self._prefix_id(vid),
                    proposicao_id="",
                    assembly_id=self.assembly_id,
                    data=dt,
                    detalhes=[],
                    raw={"materia": materia, "sessao": sessao, "pdf_votos": detalhe_url},
                ))
        self.logger.info("ALECE: %d votações carregadas", len(votacoes))
        return votacoes

    # ── Helpers de data ───────────────────────────────────────────────────
    @staticmethod
    def _parse_ddmmyy(value: str | None) -> date | None:
        if not value:
            return None
        for fmt in ("%d.%m.%y", "%d.%m.%Y", "%d/%m/%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_data(value: str | None) -> date | None:
        if not value:
            return None
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", value)
        if not m:
            return None
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None

    def health_check(self) -> bool:
        try:
            return self.session.get(DEP_URL, timeout=15).status_code < 400
        except Exception:
            return False
