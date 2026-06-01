"""
ALMT — Assembleia Legislativa do Estado de Mato Grosso
Site server-rendered (Symfony, www.al.mt.gov.br). Sem API JSON de atividade
(o /api/v1 só serve imagens). Scraping HTML, mas estruturado e SEM ViewState.

Verificado live 2026-05-31:
  Deputados   : GET /parlamento/deputados — cards (a /parlamento/deputados/{id}/
                perfil, img alt=nome, span.badge=partido). ~24.
  Proposições : GET /proposicao (form Symfony, GET) com filtros
                almt_form_proposicao_search_proposicao[dataPublicacaoInicio|Fim|ano|
                tipoPropositura] + &page=N. Cada item: h3=ementa,
                "Projeto de ... nº N/AAAA", "<Autor> - Protocolo nº...",
                link /proposicao/cpdoc/{id}/visualizar.
  Votações    : GET /parlamento/ordem-do-dia (paginado, tabela de sessões com
                resultados). Nível sessão (best-effort); votos nominais não
                confirmados → detalhes vazios.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from ..base_connector import BaseConnector
from ..models import Deputado, Proposicao, Votacao


BASE = "https://www.al.mt.gov.br"
PFX = "almt_form_proposicao_search_proposicao"
MAX_PAGINAS = 60


class ALMTConnector(BaseConnector):
    assembly_id = "almt"
    assembly_name = "Assembleia Legislativa de Mato Grosso"
    uf = "MT"
    base_url = "https://www.al.mt.gov.br"

    request_delay = 0.5
    timeout = 45

    def _html(self, url: str, params=None) -> str:
        # site responde HTML; a sessão manda Accept: json. Pede HTML.
        return self._get_text(url, params=params, headers={"Accept": "text/html,*/*"})

    # ── Deputados ─────────────────────────────────────────────────────────
    def get_deputados(self) -> list[Deputado]:
        soup = BeautifulSoup(self._html(f"{BASE}/parlamento/deputados"), "html.parser")
        deputados: list[Deputado] = []
        vistos: set[str] = set()
        # Cada deputado é um div.card contendo o link /perfil, a img (alt=nome) e
        # um badge (partido). Iterar pelo card evita pegar a foto do vizinho.
        for card in soup.select("div.card"):
            a = card.find("a", href=re.compile(r"/parlamento/deputados/(\d+)"))
            if not a:
                continue
            m = re.search(r"/parlamento/deputados/(\d+)", a.get("href", ""))
            if not m or m.group(1) in vistos:
                continue
            img = card.find("img")
            nome = (img.get("alt").strip() if img and img.get("alt") else "")
            if not nome:
                h = card.find(["h5", "h6"])
                nome = h.get_text(strip=True) if h else ""
            if not nome:
                continue
            badge = card.find("span", class_=re.compile(r"badge"))
            vistos.add(m.group(1))
            deputados.append(Deputado(
                id=self._prefix_id(m.group(1)),
                nome=nome,
                partido=(badge.get_text(strip=True) if badge else ""),
                uf="MT",
                assembly_id=self.assembly_id,
                foto_url=img.get("src") if img else None,
                raw={"perfil": a.get("href")},
            ))
        self.logger.info("ALMT: %d deputados carregados", len(deputados))
        return deputados

    # ── Proposições (busca por range de data + paginação) ─────────────────
    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        proposicoes: list[Proposicao] = []
        vistos: set[str] = set()
        pagina = 1
        while pagina <= MAX_PAGINAS:
            params = {
                f"{PFX}[dataPublicacaoInicio]": data_inicio.strftime("%d/%m/%Y"),
                f"{PFX}[dataPublicacaoFim]": data_fim.strftime("%d/%m/%Y"),
                "page": pagina,
            }
            try:
                html = self._html(f"{BASE}/proposicao", params=params)
            except Exception as e:
                self.logger.warning("ALMT prop p%d: %s", pagina, e)
                break
            soup = BeautifulSoup(html, "html.parser")
            achou = 0
            for a in soup.select('a[href*="/proposicao/cpdoc/"]'):
                m = re.search(r"/proposicao/cpdoc/(\d+)", a.get("href", ""))
                if not m or m.group(1) in vistos:
                    continue
                card = a
                for _ in range(5):
                    if card.parent:
                        card = card.parent
                    txt = re.sub(r"\s+", " ", card.get_text(" "))
                    if re.search(r"n[ºo°]\s*\d+\s*/\s*\d{4}", txt, re.I):
                        break
                h3 = card.find(["h3", "h4"])
                ementa = h3.get_text(strip=True) if h3 else ""
                txt = re.sub(r"\s+", " ", card.get_text(" ")).strip()
                # remove a ementa do texto p/ não poluir a captura de tipo/número
                resto = txt.replace(ementa, " ") if ementa else txt
                m_num = re.search(
                    r"([A-Za-zçãõáéíóúâêô ]{4,45}?)\s*n[ºo°]\s*(\d+)\s*/\s*(\d{4})",
                    resto, re.I,
                )
                m_aut = re.search(r"/\s*\d{4}\s*(.*?)\s*-?\s*Protocolo", resto, re.I)
                vistos.add(m.group(1))
                achou += 1
                ano = int(m_num.group(3)) if m_num else data_inicio.year
                proposicoes.append(Proposicao(
                    id=self._prefix_id(m.group(1)),
                    numero=m_num.group(2) if m_num else "",
                    ano=ano,
                    tipo=(m_num.group(1).strip()[-40:] if m_num else "").strip() or "PROPOSITURA",
                    ementa=ementa,
                    assembly_id=self.assembly_id,
                    autor=(m_aut.group(1).strip() if m_aut else None),
                    url=f"{BASE}{a.get('href')}",
                    raw={"cpdoc": m.group(1)},
                ))
            # filtro de data é server-side → todo resultado está na janela;
            # pagina enquanto a página trouxer cpdoc novo (dedup via `vistos`).
            if achou == 0:
                break
            pagina += 1
        if pagina >= MAX_PAGINAS:
            self.logger.warning("ALMT prop: cap de %d páginas atingido.", MAX_PAGINAS)
        self.logger.info(
            "ALMT: %d proposições carregadas (%s → %s)",
            len(proposicoes), data_inicio, data_fim,
        )
        return proposicoes

    # ── Votações: não há fonte de RESULTADOS ──────────────────────────────
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        # /parlamento/ordem-do-dia é a AGENDA (pauta) das sessões — o que está
        # marcado pra votar, com o conteúdo em PDF. Não traz resultado nem voto
        # nominal. Nenhuma outra fonte de votação foi encontrada. → vazio.
        self.logger.info("ALMT: votações (resultados) não publicadas — só agenda/PDF. Vazio.")
        return []

    # ── Helpers ───────────────────────────────────────────────────────────
    def health_check(self) -> bool:
        try:
            return self.session.get(f"{BASE}/parlamento/deputados", timeout=15).status_code < 400
        except Exception:
            return False
