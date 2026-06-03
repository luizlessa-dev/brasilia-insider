"""
ALERGS — Assembleia Legislativa do Estado do Rio Grande do Sul
Tier 2 — API JSON REST na porta 5000 do portal ww4.al.rs.gov.br

Arquitetura: o ww4 é um Drupal com módulo custom `alergs_deputados`.
O módulo JS (`/modules/custom/alergs_deputados/js/deputados.js`) chama
`${window.location.origin}:5000/listarDestaqueDeputados` — uma API Node.js
(Express) na porta 5000 do mesmo host.

Endpoints verificados live (2026-06-03):
  GET https://ww4.al.rs.gov.br:5000/listarDestaqueDeputados
    → JSON {"lista":[{idDeputado, nomeDeputado, emailDeputado,
              telefoneDeputado, siglaPartido, nomePartido, codStatus,
              fotoGrandeDeputado, codigoPro}]}  → 55 deputados.

Nota: codStatus=1 = em exercício. Outros endpoints na porta 5000 retornam
404 (só o de deputados está exposto publicamente).

Proposições / Votações: o portal de transparência (`transparencia.al.rs.gov.br`)
tem `/parlamentares/votos-plenario` com endpoints AJAX POST em
`/ajax-votosPlenarioModal*`, mas os votos são por matéria individual (sem
enumeração por data/período). A API do consultas (api/v1/*) retorna 401.
Ambas fontes são deferidas — sem enumeração estruturada acessível.
"""
from __future__ import annotations

from datetime import date

from ..base_connector import BaseConnector
from ..models import Deputado, Proposicao, Votacao


API_URL = "https://ww4.al.rs.gov.br:5000"


class ALERGSConnector(BaseConnector):
    assembly_id = "alergs"
    assembly_name = "Assembleia Legislativa do Rio Grande do Sul"
    uf = "RS"
    base_url = "https://ww4.al.rs.gov.br"

    request_delay = 0.5
    timeout = 30

    # ── Deputados (API porta 5000) ────────────────────────────────────────
    def get_deputados(self) -> list[Deputado]:
        data = self._get(f"{API_URL}/listarDestaqueDeputados")
        lista = data.get("lista", []) if isinstance(data, dict) else data
        deputados: list[Deputado] = []
        for d in lista:
            idd = d.get("idDeputado")
            nome = (d.get("nomeDeputado") or "").strip()
            if not idd or not nome:
                continue
            # codStatus=1 → em exercício; outros podem ser licenciados/suplentes
            deputados.append(Deputado(
                id=self._prefix_id(str(idd)),
                nome=nome,
                partido=(d.get("siglaPartido") or "").strip(),
                uf="RS",
                assembly_id=self.assembly_id,
                email=(d.get("emailDeputado") or "").strip() or None,
                foto_url=d.get("fotoGrandeDeputado") or None,
                raw=d,
            ))
        self.logger.info("ALERGS: %d deputados carregados", len(deputados))
        return deputados

    # ── Proposições: sem enumeração pública acessível ─────────────────────
    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        self.logger.info(
            "ALERGS: proposições — API consultas requer auth (401); "
            "votos-plenario não enumera por data. Deferido. Vazio."
        )
        return []

    # ── Votações: endpoints AJAX por matéria individual (sem enumeração) ──
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        self.logger.info(
            "ALERGS: votações — endpoints AJAX por matéria individual "
            "(sem enumeração por data/período). Deferido. Vazio."
        )
        return []

    def health_check(self) -> bool:
        try:
            resp = self.session.get(f"{API_URL}/listarDestaqueDeputados", timeout=15)
            return resp.status_code < 400
        except Exception as e:
            self.logger.warning("health_check erro: %s", e)
            return False
