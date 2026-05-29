"""
ALEP — Assembleia Legislativa do Paraná
Tier 1 — REST API.

⚠️ QUEBRADO — precisa de remapeamento (diagnóstico 2026-05-29):
- O domínio MIGROU: `www.alep.pr.gov.br` agora serve cert válido só para a
  família `assembleia.pr.leg.br` → toda requisição estoura SSL hostname mismatch.
- O host vivo é `https://www.assembleia.pr.leg.br` (HTTP 200, cert OK), e a home
  linka "Dados Abertos" (/dados-abertos). MAS os paths abaixo são especulativos
  (este conector nunca foi validado contra a API real) e retornam 404 JSON no
  host novo (`/api/deputados/legislatura-atual`, `/api/deputados`, etc.).
- Fix = mapear a API de dados abertos em assembleia.pr.leg.br (mesmo método
  usado em ALMG/ALESP: achar o spec/Swagger, confirmar rotas e campos live).
  Sub-projeto deferido — ALEP não está no caminho crítico.

Endpoints atuais (ESPECULATIVOS, não funcionam):
  GET /api/deputados/legislatura-atual   → deputados
  GET /api/proposicoes                   → proposições (filtro por data)
  GET /api/votacoes                      → votações (filtro por data)
  GET /api/votacoes/{id}/votos           → votos nominais
"""
from __future__ import annotations

from datetime import date

from ..base_connector import BaseConnector
from ..models import Deputado, Proposicao, Votacao, VotoDeputado


class ALEPConnector(BaseConnector):
    assembly_id = "alep"
    assembly_name = "Assembleia Legislativa do Paraná"
    uf = "PR"
    base_url = "https://www.alep.pr.gov.br"
    api_url = "https://www.alep.pr.gov.br/api"

    request_delay = 0.5

    # ── Deputados ─────────────────────────────────────────────────────────
    def get_deputados(self) -> list[Deputado]:
        data = self._get(f"{self.api_url}/deputados/legislatura-atual")
        itens = data if isinstance(data, list) else data.get("data", data.get("items", []))
        deputados = []
        for d in itens:
            deputados.append(Deputado(
                id=self._prefix_id(d.get("id") or d.get("idDeputado")),
                nome=d.get("nome") or d.get("nomeDeputado", ""),
                partido=d.get("partido") or d.get("siglaPartido", ""),
                uf="PR",
                assembly_id=self.assembly_id,
                foto_url=d.get("foto") or d.get("urlFoto"),
                email=d.get("email"),
                raw=d,
            ))
        self.logger.info("ALEP: %d deputados carregados", len(deputados))
        return deputados

    # ── Proposições ───────────────────────────────────────────────────────
    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        proposicoes: list[Proposicao] = []
        page = 1

        while True:
            try:
                data = self._get(
                    f"{self.api_url}/proposicoes",
                    params={
                        "dataInicio": data_inicio.isoformat(),
                        "dataFim": data_fim.isoformat(),
                        "page": page,
                        "size": 100,
                    },
                )
            except Exception as e:
                self.logger.warning("ALEP: erro proposições p%d: %s", page, e)
                break

            itens = data if isinstance(data, list) else data.get("data", data.get("items", []))
            if not itens:
                break

            for p in itens:
                dt = self.parse_date(p.get("dataApresentacao") or p.get("data"))
                proposicoes.append(Proposicao(
                    id=self._prefix_id(p.get("id") or p.get("idProposicao")),
                    numero=str(p.get("numero", "")),
                    ano=int(p.get("ano", data_inicio.year)),
                    tipo=p.get("tipo") or p.get("siglaTipo", ""),
                    ementa=p.get("ementa") or p.get("descricao", ""),
                    assembly_id=self.assembly_id,
                    autor=p.get("autor") or p.get("nomeAutor"),
                    data_apresentacao=dt,
                    situacao=p.get("situacao") or p.get("descricaoSituacao"),
                    url=p.get("url") or p.get("urlDetalhe"),
                    raw=p,
                ))

            # paginação — para se retornou menos que o page size
            if isinstance(data, list) or len(itens) < 100:
                break
            page += 1

        self.logger.info("ALEP: %d proposições carregadas", len(proposicoes))
        return proposicoes

    # ── Votações ──────────────────────────────────────────────────────────
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        votacoes: list[Votacao] = []
        page = 1

        while True:
            try:
                data = self._get(
                    f"{self.api_url}/votacoes",
                    params={
                        "dataInicio": data_inicio.isoformat(),
                        "dataFim": data_fim.isoformat(),
                        "page": page,
                        "size": 100,
                    },
                )
            except Exception as e:
                self.logger.warning("ALEP: erro votações p%d: %s", page, e)
                break

            itens = data if isinstance(data, list) else data.get("data", data.get("items", []))
            if not itens:
                break

            for v in itens:
                vid = v.get("id") or v.get("idVotacao")
                dt = self.parse_date(v.get("data") or v.get("dataVotacao"))
                detalhes = self._get_votos_nominais(vid)

                votacoes.append(Votacao(
                    id=self._prefix_id(vid),
                    proposicao_id=self._prefix_id(v.get("idProposicao") or v.get("proposicaoId", "")),
                    assembly_id=self.assembly_id,
                    data=dt,
                    resultado=(v.get("resultado") or "").lower() or None,
                    votos_sim=v.get("sim") or v.get("votosSim", 0),
                    votos_nao=v.get("nao") or v.get("votosNao", 0),
                    votos_abstencao=v.get("abstencao") or v.get("votosAbstencao", 0),
                    detalhes=detalhes,
                    raw=v,
                ))

            if isinstance(data, list) or len(itens) < 100:
                break
            page += 1

        self.logger.info("ALEP: %d votações carregadas", len(votacoes))
        return votacoes

    def _get_votos_nominais(self, votacao_id: str | int) -> list[VotoDeputado]:
        try:
            data = self._get(f"{self.api_url}/votacoes/{votacao_id}/votos")
            itens = data if isinstance(data, list) else data.get("data", [])
            return [
                VotoDeputado(
                    deputado_id=self._prefix_id(i.get("idDeputado") or i.get("deputadoId", "")),
                    deputado_nome=i.get("nomeDeputado") or i.get("nome", ""),
                    voto=(i.get("voto") or i.get("tipoVoto", "")).lower(),
                    partido=i.get("partido") or i.get("siglaPartido"),
                )
                for i in itens
            ]
        except Exception:
            return []
