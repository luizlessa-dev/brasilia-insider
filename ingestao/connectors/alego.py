"""
ALEGO — Assembleia Legislativa do Estado de Goiás
Tier 1/2 — API JSON pública (descoberta no bundle JS do portal de transparência)
+ scraping leve da tabela de deputados.

Endpoints verificados live (2026-05-31), host https://transparencia.al.go.leg.br:
  GET /api/transparencia/votacoes.json?ano={A}        → 1 linha POR VOTO NOMINAL
       (data_sessao, pauta_id, parlamentar, tipo_voto, partido_sigla,
        ementa_projeto, resultado_votacao, ...). ~12 MB/ano, ~20k linhas.
  GET /api/transparencia/processos/recentes           → proposições recentes
       (numero, ementa, autores, situacao, data_autuacao, data_publicacao,
        a_favor, contra). Lista curta (recentes).
  Deputados: https://portal.al.go.leg.br/deputados/em-exercicio (tabela HTML:
        td[data-title=Nome] > a[/deputados/perfil/{id}], td[data-title=Partido]).

NOTAS:
- votacoes.json só aceita ?ano= (params vazios → erro). get_votacoes baixa o
  ano e agrupa por pauta_id → uma Votacao com votos nominais. RICO (raro em ALEs).
- Proposições: `processos/recentes` cobre só o recente. Histórico completo está
  em `processos/busca-avancada` (paginado), mas os params obrigatórios não foram
  decifrados (retorna 0) — backfill histórico deferido.
- A API /api/* respondida pelo host www/portal dá 500/404; usar SEMPRE o host
  transparencia.al.go.leg.br.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from ..base_connector import BaseConnector
from ..models import Deputado, Proposicao, Votacao, VotoDeputado


PORTAL = "https://portal.al.go.leg.br"
API = "https://transparencia.al.go.leg.br/api/transparencia"

_VOTO_MAP = {
    "SIM": "sim",
    "NÃO": "não", "NAO": "não",
    "ABSTENÇÃO": "abstenção", "ABSTENCAO": "abstenção",
}


def _slug(t: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", (t or "").lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


class ALEGOConnector(BaseConnector):
    assembly_id = "alego"
    assembly_name = "Assembleia Legislativa de Goiás"
    uf = "GO"
    base_url = "https://portal.al.go.leg.br"

    request_delay = 0.5
    timeout = 120  # votacoes.json é grande (~12 MB/ano)

    # ── Deputados (tabela HTML) ───────────────────────────────────────────
    def get_deputados(self) -> list[Deputado]:
        # A sessão manda Accept: application/json por padrão; o portal Rails
        # devolve 500 pra esse Accept numa rota HTML. Pede HTML explicitamente.
        html = self._get_text(
            f"{PORTAL}/deputados/em-exercicio",
            headers={"Accept": "text/html,application/xhtml+xml,*/*"},
        )
        soup = BeautifulSoup(html, "html.parser")
        deputados: list[Deputado] = []
        for a in soup.select('td[data-title="Nome"] a[href*="/deputados/perfil/"]'):
            nome = a.get_text(strip=True)
            href = a.get("href", "")
            m = re.search(r"/perfil/(\d+)", href)
            if not nome or not m:
                continue
            tr = a.find_parent("tr")
            partido_td = tr.find("td", attrs={"data-title": "Partido"}) if tr else None
            partido_txt = partido_td.get_text(" ", strip=True) if partido_td else ""
            m_sig = re.search(r"\(([^)]+)\)\s*$", partido_txt)
            deputados.append(Deputado(
                id=self._prefix_id(m.group(1)),
                nome=nome,
                partido=(m_sig.group(1) if m_sig else partido_txt).strip(),
                uf="GO",
                assembly_id=self.assembly_id,
                raw={"perfil": href, "partido_nome": partido_txt},
            ))
        self.logger.info("ALEGO: %d deputados carregados", len(deputados))
        return deputados

    # ── Proposições (processos/recentes) ──────────────────────────────────
    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        try:
            data = self._get(f"{API}/processos/recentes")
        except Exception as e:
            self.logger.warning("ALEGO: processos/recentes falhou: %s", e)
            return []
        # resposta pode vir aninhada [[...]]
        while isinstance(data, list) and data and isinstance(data[0], list):
            data = data[0]
        if not isinstance(data, list):
            data = next((v for v in data.values() if isinstance(v, list)), []) if isinstance(data, dict) else []

        proposicoes: list[Proposicao] = []
        for p in data:
            dt = self._parse_iso(p.get("data_publicacao") or p.get("data_autuacao"))
            if dt and not (data_inicio <= dt <= data_fim):
                continue
            numero = str(p.get("numero", "")) or _slug((p.get("assunto") or "")[:20])
            autores = p.get("autores") or []
            proposicoes.append(Proposicao(
                id=self._prefix_id(numero),
                numero=str(p.get("numero", "")),
                ano=dt.year if dt else data_inicio.year,
                tipo="PROCESSO",
                ementa=p.get("ementa") or p.get("assunto") or "",
                assembly_id=self.assembly_id,
                autor="; ".join(autores) if isinstance(autores, list) else (autores or None),
                data_apresentacao=dt,
                situacao=p.get("situacao"),
                raw=p,
            ))
        self.logger.info(
            "ALEGO: %d proposições carregadas (recentes, %s → %s)",
            len(proposicoes), data_inicio, data_fim,
        )
        return proposicoes

    # ── Votações (votacoes.json?ano → agrupa por pauta_id) ────────────────
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        votacoes: list[Votacao] = []
        for ano in range(data_inicio.year, data_fim.year + 1):
            try:
                linhas = self._get(f"{API}/votacoes.json", params={"ano": ano})
            except Exception as e:
                self.logger.warning("ALEGO: votacoes.json?ano=%d falhou: %s", ano, e)
                continue
            if not isinstance(linhas, list):
                continue

            grupos: dict[str, dict] = {}
            for r in linhas:
                ds = self._parse_iso(r.get("data_sessao"))
                if not ds or not (data_inicio <= ds <= data_fim):
                    continue
                pauta = str(r.get("pauta_id") or "")
                key = f"{pauta}-{r.get('data_sessao')}"
                g = grupos.setdefault(key, {
                    "data": ds,
                    "pauta": pauta,
                    "ementa": r.get("ementa_projeto", ""),
                    "resultado": r.get("resultado_votacao"),
                    "votos": [],
                })
                tipo = (r.get("tipo_voto") or "").strip().upper()
                g["votos"].append(VotoDeputado(
                    deputado_id=self._prefix_id(_slug(r.get("parlamentar", ""))),
                    deputado_nome=r.get("parlamentar", ""),
                    voto=_VOTO_MAP.get(tipo, tipo.lower()),
                    partido=r.get("partido_sigla"),
                ))

            for key, g in grupos.items():
                det = g["votos"]
                votacoes.append(Votacao(
                    id=self._prefix_id(key),
                    proposicao_id="",
                    assembly_id=self.assembly_id,
                    data=g["data"],
                    resultado=(g["resultado"] or "").strip()[:120] or None,
                    votos_sim=sum(1 for v in det if v.voto == "sim"),
                    votos_nao=sum(1 for v in det if v.voto == "não"),
                    votos_abstencao=sum(1 for v in det if v.voto == "abstenção"),
                    detalhes=det,
                    raw={"pauta_id": g["pauta"], "ementa_projeto": g["ementa"][:300]},
                ))
        self.logger.info("ALEGO: %d votações carregadas", len(votacoes))
        return votacoes

    # ── Helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _parse_iso(value: str | None) -> date | None:
        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(str(value)[:19] if "T" in str(value) else str(value)[:10], fmt).date()
            except ValueError:
                continue
        return None

    def health_check(self) -> bool:
        try:
            return self.session.get(f"{PORTAL}/deputados/em-exercicio", timeout=15).status_code < 400
        except Exception:
            return False
