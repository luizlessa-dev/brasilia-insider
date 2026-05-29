"""
ALMG — Assembleia Legislativa de Minas Gerais
Tier 1 — REST API documentada em https://dadosabertos.almg.gov.br/

Endpoints (mapeados contra o Swagger oficial /api/ajuda/swagger/endpoints/lastest):
  GET /api/v2/deputados/em_exercicio            → deputados   [VERIFICADO 200, 77]
  GET /api/v2/proposicoes/pesquisa/direcionada  → proposições [VERIFICADO; ini/fim yyyyMMdd]
  GET /api/v2/plenario/reunioes/pesquisa + .../resultados → votações [MAPEADO, não implementado]

NOTA (2026-05-29): proposições agora usam pesquisa/direcionada (período por
dataPublicacao em yyyyMMdd, paginado). Votações da v2 NÃO têm rota por
proposição — vivem nos resultados de reuniões de plenário/comissões; get_votacoes
está mapeado no docstring do método mas ainda retorna [] (não quebra o pipeline).
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
    # Endpoint verificado: GET /api/v2/proposicoes/pesquisa/direcionada
    #   ini, fim   → período por dataPublicacao, formato yyyyMMdd
    #   p, tp      → paginação (página, tamanho)
    # Identidade da proposição = (siglaTipoProjeto, numero, ano) — não há id
    # numérico; o id canônico é "almg_<sigla>_<num>_<ano>".
    PAGE_SIZE = 100

    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        proposicoes: list[Proposicao] = []
        ini = data_inicio.strftime("%Y%m%d")
        fim = data_fim.strftime("%Y%m%d")
        page = 1

        while True:
            try:
                data = self._get(
                    f"{self.api_url}/proposicoes/pesquisa/direcionada",
                    params={"ini": ini, "fim": fim, "p": page, "tp": self.PAGE_SIZE},
                    headers={"Accept": "application/json"},
                )
            except Exception as e:
                self.logger.warning("ALMG: erro proposições p%d: %s", page, e)
                break

            res = data.get("resultado", {})
            itens = res.get("listaItem") or []
            if not itens:
                break

            for p in itens:
                sigla = (p.get("siglaTipoProjeto") or "").strip()
                numero = str(p.get("numero", "")).strip()
                ano_str = str(p.get("ano", "")).strip()
                if not (sigla and numero and ano_str):
                    continue
                proposicoes.append(Proposicao(
                    id=self._prefix_id(f"{sigla}_{numero}_{ano_str}"),
                    numero=numero,
                    ano=int(ano_str) if ano_str.isdigit() else data_inicio.year,
                    tipo=sigla,
                    ementa=(p.get("assunto") or "").strip(),
                    assembly_id=self.assembly_id,
                    autor=(p.get("autor") or "").strip() or None,
                    data_apresentacao=self.parse_date(p.get("dataPublicacao")),
                    situacao=p.get("situacao"),
                    regime=p.get("regime"),
                    raw=p,
                ))

            total = res.get("noOcorrencias", 0)
            if len(proposicoes) >= total or len(itens) < self.PAGE_SIZE:
                break
            page += 1

        self.logger.info(
            "ALMG: %d proposições carregadas (%s → %s)",
            len(proposicoes), data_inicio, data_fim,
        )
        return proposicoes

    # ── Votações ──────────────────────────────────────────────────────────
    # MAPEADO, não implementado. A API v2 da ALMG não expõe votações por
    # proposição (o antigo /proposicoes/{id}/votacoes não existe). Votos vivem
    # nos resultados de reuniões:
    #   - Plenário: GET /api/v2/plenario/reunioes/pesquisa?ini=&fim=  (yyyyMMdd)
    #       → para cada reunião: GET /api/v2/plenario/reunioes/{ano}/{mes}/{dia}/{hora}/resultados
    #   - Comissões: GET /api/v2/comissoes/proposicao/{tipo}/{num}/{ano}/reunioes/resultados
    # Implementar = buscar reuniões na janela + drill-down nos resultados +
    # parse dos votos nominais (sub-projeto análogo ao das votações da ALESP).
    # Até lá retorna [] graciosamente — não quebra o pipeline.
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        self.logger.info(
            "ALMG: votações ainda não implementadas (ver fluxo plenario/reunioes "
            "no docstring) — retornando 0"
        )
        return []
