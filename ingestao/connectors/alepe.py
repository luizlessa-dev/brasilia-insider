"""
ALEPE — Assembleia Legislativa do Estado de Pernambuco
Tier 1 — API REST de dados abertos em https://dadosabertos.alepe.pe.gov.br/api/v1

Endpoints verificados live (2026-05-29):
  GET /api/v1/parlamentares/              → JSON [{nomeParlamentar, partido}] (49) [MAGRO: só nome+partido]
  GET /api/v1/proposicoes/projetos/       → XML <projetos><projeto .../></projetos> (453, atual 2026)
  GET /api/v1/proposicoes/indicacoes/     → XML idem
  GET /api/v1/proposicoes/requerimentos/  → XML idem

Notas:
- Todas as rotas exigem barra final (sem ela → 301). A sessão segue redirect.
- parlamentares NÃO traz id estável nem partido por mandato — só nome+partido
  atual. id é derivado de slug do nome. Sem email/foto/datas.
- Votações: NÃO publicadas em dados abertos. get_votacoes retorna [].
- proposições vêm em XML com campos em ATRIBUTOS (docid, numero, ano, tipo,
  ementa, dataPublicacao DD/MM/YYYY) + filho <autores><autor nome tipo/></autores>.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from xml.etree import ElementTree as ET

from ..base_connector import BaseConnector
from ..models import Deputado, Proposicao, Votacao


API = "https://dadosabertos.alepe.pe.gov.br/api/v1"
TIPOS_PROPOSICAO = ["projetos", "indicacoes", "requerimentos"]


def _slug(texto: str) -> str:
    s = unicodedata.normalize("NFD", (texto or "").lower())
    s = s.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


class ALEPEConnector(BaseConnector):
    assembly_id = "alepe"
    assembly_name = "Assembleia Legislativa de Pernambuco"
    uf = "PE"
    base_url = "https://dadosabertos.alepe.pe.gov.br"

    request_delay = 0.5
    timeout = 60

    # ── Deputados ─────────────────────────────────────────────────────────
    def get_deputados(self) -> list[Deputado]:
        data = self._get(f"{API}/parlamentares/")
        itens = data if isinstance(data, list) else data.get("data", [])
        deputados: list[Deputado] = []
        for d in itens:
            nome = (d.get("nomeParlamentar") or "").strip()
            if not nome:
                continue
            deputados.append(Deputado(
                id=self._prefix_id(_slug(nome)),   # API não traz id estável
                nome=nome,
                partido=(d.get("partido") or "").strip(),
                uf="PE",
                assembly_id=self.assembly_id,
                raw=d,
            ))
        self.logger.info("ALEPE: %d deputados carregados", len(deputados))
        return deputados

    # ── Proposições (3 tipos, XML com campos em atributos) ────────────────
    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        proposicoes: list[Proposicao] = []
        for tipo in TIPOS_PROPOSICAO:
            try:
                raw = self._get_xml(f"{API}/proposicoes/{tipo}/")
                root = ET.fromstring(raw)
            except Exception as e:
                self.logger.warning("ALEPE: erro ao buscar %s: %s", tipo, e)
                continue

            for p in root:
                a = p.attrib
                dt = self._parse_br(a.get("dataPublicacao"))
                if dt and not (data_inicio <= dt <= data_fim):
                    continue
                docid = a.get("docid")
                if not docid:
                    continue
                autores_el = p.find("autores")
                autores = [au.get("nome", "") for au in autores_el] if autores_el is not None else []
                ano = a.get("ano")
                proposicoes.append(Proposicao(
                    id=self._prefix_id(docid),
                    numero=str(a.get("numero", "")),
                    ano=int(ano) if ano and ano.isdigit() else data_inicio.year,
                    tipo=(a.get("tipo") or tipo).strip(),
                    ementa=a.get("ementa", ""),
                    assembly_id=self.assembly_id,
                    autor="; ".join(x for x in autores if x) or None,
                    data_apresentacao=dt,
                    raw=dict(a),
                ))
        self.logger.info(
            "ALEPE: %d proposições carregadas (%s → %s)",
            len(proposicoes), data_inicio, data_fim,
        )
        return proposicoes

    # ── Votações: não publicadas em dados abertos ─────────────────────────
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        self.logger.info("ALEPE: votações não disponíveis em dados abertos — vazio.")
        return []

    # ── Helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _parse_br(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return datetime.strptime(value.strip()[:10], "%d/%m/%Y").date()
        except ValueError:
            return None

    def health_check(self) -> bool:
        try:
            resp = self.session.get(f"{API}/parlamentares/", timeout=15)
            return resp.status_code < 400
        except Exception as e:
            self.logger.warning("health_check erro: %s", e)
            return False
