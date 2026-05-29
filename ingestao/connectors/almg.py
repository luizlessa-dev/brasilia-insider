"""
ALMG — Assembleia Legislativa de Minas Gerais
Tier 1 — REST API documentada em https://dadosabertos.almg.gov.br/

Endpoints:
  GET /api/v2/deputados/em_exercicio     → lista de deputados  [VERIFICADO 200, 77 dep]
  GET /api/v2/proposicoes/...            → proposições         [NÃO VERIFICADO — ver nota]
  GET /api/v2/proposicoes/{id}/votacoes  → votações            [NÃO VERIFICADO — ver nota]

NOTA (2026-05-28): a API de busca de proposições da ALMG v2 não responde nas
rotas óbvias (/proposicoes 404, /proposicoes/pesquisa 403,
/proposicoes/pesquisa/direcionada 400). get_proposicoes/get_votacoes ainda usam
rotas especulativas e falham graciosamente (log warning, retorna []). Precisam
ser mapeados contra a doc oficial da v2 antes de valerem como fonte. Só
get_deputados está validado contra a API real.
"""
from __future__ import annotations

from datetime import date

from ..base_connector import BaseConnector
from ..models import Deputado, Proposicao, Votacao, VotoDeputado


class ALMGConnector(BaseConnector):
    assembly_id = "almg"
    assembly_name = "Assembleia Legislativa de Minas Gerais"
    uf = "MG"
    base_url = "https://dadosabertos.almg.gov.br"
    api_url = "https://dadosabertos.almg.gov.br/api/v2"

    # Tipos de proposição relevantes
    TIPOS_PROPOSICAO = ["PL", "PEC", "PLO", "PDL", "PDC", "PLN"]

    request_delay = 0.4

    # ── Deputados ─────────────────────────────────────────────────────────
    def get_deputados(self) -> list[Deputado]:
        data = self._get(f"{self.api_url}/deputados/em_exercicio", params={"formato": "json"})
        lista = data.get("list", [])
        deputados = []
        for d in lista:
            deputados.append(Deputado(
                id=self._prefix_id(d["id"]),
                nome=d.get("nome") or d.get("nomeCompleto", ""),
                partido=d.get("partido", ""),
                uf="MG",
                assembly_id=self.assembly_id,
                foto_url=d.get("urlFoto"),
                email=d.get("email"),
                raw=d,
            ))
        self.logger.info("ALMG: %d deputados carregados", len(deputados))
        return deputados

    # ── Proposições ───────────────────────────────────────────────────────
    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        anos = list(range(data_inicio.year, data_fim.year + 1))
        proposicoes: list[Proposicao] = []

        for ano in anos:
            for tipo in self.TIPOS_PROPOSICAO:
                try:
                    data = self._get(
                        f"{self.api_url}/proposicoes",
                        params={"tp": tipo, "ano": ano, "formato": "json"},
                    )
                    for p in data.get("list", []):
                        dt = self.parse_date(p.get("dataApresentacao"))
                        if dt and not (data_inicio <= dt <= data_fim):
                            continue
                        proposicoes.append(Proposicao(
                            id=self._prefix_id(p["id"]),
                            numero=str(p.get("numero", "")),
                            ano=ano,
                            tipo=tipo,
                            ementa=p.get("ementa", ""),
                            assembly_id=self.assembly_id,
                            autor=p.get("autor", {}).get("nome") if isinstance(p.get("autor"), dict) else p.get("autor"),
                            data_apresentacao=dt,
                            situacao=p.get("situacao", {}).get("descricao") if isinstance(p.get("situacao"), dict) else None,
                            url=f"https://www.almg.gov.br/legislacao_normas/leis_decretos_normas/norma/{p['id']}/",
                            raw=p,
                        ))
                except Exception as e:
                    self.logger.warning("ALMG: erro ao buscar %s/%d: %s", tipo, ano, e)

        self.logger.info("ALMG: %d proposições carregadas (%s → %s)", len(proposicoes), data_inicio, data_fim)
        return proposicoes

    # ── Votações ──────────────────────────────────────────────────────────
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        proposicoes = self.get_proposicoes(data_inicio, data_fim)
        votacoes: list[Votacao] = []

        for prop in proposicoes:
            raw_id = prop.id.replace(f"{self.assembly_id}_", "")
            try:
                data = self._get(
                    f"{self.api_url}/proposicoes/{raw_id}/votacoes",
                    params={"formato": "json"},
                )
                for v in data.get("list", []):
                    dt = self.parse_date(v.get("data"))
                    if dt and not (data_inicio <= dt <= data_fim):
                        continue

                    detalhes = self._get_detalhes_votacao(raw_id, v["id"])
                    votacoes.append(Votacao(
                        id=self._prefix_id(v["id"]),
                        proposicao_id=prop.id,
                        assembly_id=self.assembly_id,
                        data=dt,
                        resultado=v.get("resultado", "").lower() or None,
                        votos_sim=v.get("sim", 0),
                        votos_nao=v.get("nao", 0),
                        votos_abstencao=v.get("abstencao", 0),
                        detalhes=detalhes,
                        raw=v,
                    ))
            except Exception as e:
                self.logger.debug("ALMG: sem votações para %s: %s", prop.id, e)

        self.logger.info("ALMG: %d votações carregadas", len(votacoes))
        return votacoes

    def _get_detalhes_votacao(self, proposicao_id: str, votacao_id: str) -> list[VotoDeputado]:
        try:
            data = self._get(
                f"{self.api_url}/proposicoes/{proposicao_id}/votacoes/{votacao_id}",
                params={"formato": "json"},
            )
            votos = []
            for item in data.get("listaVotos", []):
                votos.append(VotoDeputado(
                    deputado_id=self._prefix_id(item.get("deputado", {}).get("id", "")),
                    deputado_nome=item.get("deputado", {}).get("nome", ""),
                    voto=item.get("voto", "").lower(),
                    partido=item.get("deputado", {}).get("partido"),
                ))
            return votos
        except Exception:
            return []
