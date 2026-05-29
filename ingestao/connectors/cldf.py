"""
CLDF — Câmara Legislativa do Distrito Federal
Tier 1 — Portal de Dados Abertos CKAN em https://dados.cl.df.gov.br

Cobertura dos dados abertos (verificado 2026-05-29):
  - Deputados: dataset `relacao-nominal-de-deputados-e-servidores` — CSV mensal
    atual (deputados misturados com servidores; filtra CargoFuncao=DEPUTADO
    DISTRITAL). [ATUAL]
  - Proposições: dataset `proposicoes` — 1 JSON por ano, 1991→2020. [CONGELADO
    EM 2020 no CKAN]. As proposições recentes vivem no SPA ple.cl.df.gov.br
    (API no bundle JS — não mapeada; sub-tarefa futura). get_proposicoes pega o
    que o CKAN tiver na janela; janelas pós-2020 retornam vazio até a CLDF
    atualizar o dataset (ou até mapearmos o PLE).
  - Votações: NÃO publicadas em dados abertos. get_votacoes retorna [].

Downloads dos recursos redirecionam pra MinIO (object storage) — a sessão segue
redirect automaticamente.
"""
from __future__ import annotations

import csv
import io
import json as jsonlib
from datetime import date

from ..base_connector import BaseConnector, ConnectorError
from ..models import Deputado, Proposicao, Votacao


CKAN = "https://dados.cl.df.gov.br/api/3/action"


class CLDFConnector(BaseConnector):
    assembly_id = "cldf"
    assembly_name = "Câmara Legislativa do Distrito Federal"
    uf = "DF"
    base_url = "https://dados.cl.df.gov.br"

    request_delay = 0.5
    timeout = 90

    # ── CKAN helpers ──────────────────────────────────────────────────────
    def _ckan(self, action: str, **params) -> dict | list:
        data = self._get(f"{CKAN}/{action}", params=params or None)
        if isinstance(data, dict) and not data.get("success", True):
            raise ConnectorError(f"CKAN {action} falhou: {data.get('error')}")
        return data.get("result", data) if isinstance(data, dict) else data

    def _resources(self, package_id: str) -> list[dict]:
        return self._ckan("package_show", id=package_id).get("resources", [])

    def _download_bytes(self, url: str) -> bytes:
        self._throttle()
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            raise ConnectorError(f"Falha ao baixar {url}: {e}") from e

    # ── Deputados (CSV mensal mais recente) ───────────────────────────────
    def get_deputados(self) -> list[Deputado]:
        recursos = [r for r in self._resources("relacao-nominal-de-deputados-e-servidores")
                    if (r.get("format") or "").upper() == "CSV" and r.get("name")]
        if not recursos:
            raise ConnectorError("CLDF: nenhum CSV de deputados encontrado no CKAN")
        # nome no padrão YYYY-MM → o mais recente é o maior lexicograficamente
        recente = max(recursos, key=lambda r: r["name"])
        raw = self._download_bytes(recente["url"])
        texto = raw.decode("latin-1", errors="replace")
        leitor = csv.DictReader(io.StringIO(texto))

        deputados: list[Deputado] = []
        for linha in leitor:
            if (linha.get("CargoFuncao") or "").strip().upper() != "DEPUTADO DISTRITAL":
                continue
            if (linha.get("Desligamento") or "").strip():  # só mandato vigente
                continue
            matricula = (linha.get("Matricula") or "").strip()
            if not matricula:
                continue
            deputados.append(Deputado(
                id=self._prefix_id(matricula),
                nome=(linha.get("Nome") or "").strip(),
                partido="",  # CSV não traz partido; enriquecer depois via PLE
                uf="DF",
                assembly_id=self.assembly_id,
                mandato_inicio=self.parse_date(linha.get("Admissao")),
                raw=dict(linha),
            ))
        self.logger.info("CLDF: %d deputados carregados (recurso %s)", len(deputados), recente["name"])
        return deputados

    # ── Proposições (JSON por ano; CKAN congelado em 2020) ────────────────
    def get_proposicoes(self, data_inicio: date, data_fim: date) -> list[Proposicao]:
        recursos = {r["name"]: r["url"] for r in self._resources("proposicoes")
                    if (r.get("format") or "").upper() == "JSON" and r.get("name")}
        anos = [str(a) for a in range(data_inicio.year, data_fim.year + 1)]
        disponiveis = [a for a in anos if a in recursos]
        if not disponiveis:
            self.logger.warning(
                "CLDF: dados abertos de proposições não cobrem %s–%s "
                "(CKAN vai até %s). Vazio.",
                data_inicio.year, data_fim.year, max(recursos, default="?"),
            )
            return []

        proposicoes: list[Proposicao] = []
        for ano in disponiveis:
            raw = self._download_bytes(recursos[ano])
            try:
                registros = jsonlib.loads(raw.decode("utf-8", errors="replace"))
            except ValueError as e:
                self.logger.warning("CLDF: JSON %s ilegível: %s", ano, e)
                continue
            if not isinstance(registros, list):
                registros = next((v for v in registros.values() if isinstance(v, list)), [])

            for p in registros:
                prop = p.get("proposicao", {}) or {}
                dt = self.parse_date(p.get("dataLeitura"))
                if dt and not (data_inicio <= dt <= data_fim):
                    continue
                tipo = prop.get("tipo", "")
                numero = prop.get("numero", "")
                pano = prop.get("ano", ano)
                autores = p.get("autores") or []
                proposicoes.append(Proposicao(
                    id=self._prefix_id(f"{tipo}-{numero}-{pano}"),
                    numero=str(numero),
                    ano=int(pano) if str(pano).isdigit() else int(ano),
                    tipo=tipo,
                    ementa=p.get("ementa", ""),
                    assembly_id=self.assembly_id,
                    autor="; ".join(autores) if autores else None,
                    data_apresentacao=dt,
                    situacao=p.get("situacao"),
                    url=p.get("urlRedacaoInicial"),
                    raw=p,
                ))
        self.logger.info(
            "CLDF: %d proposições carregadas (anos %s)", len(proposicoes), ",".join(disponiveis)
        )
        return proposicoes

    # ── Votações: não publicadas em dados abertos ─────────────────────────
    def get_votacoes(self, data_inicio: date, data_fim: date) -> list[Votacao]:
        self.logger.info("CLDF: votações não disponíveis em dados abertos — vazio.")
        return []

    # ── Health check: API CKAN ────────────────────────────────────────────
    def health_check(self) -> bool:
        try:
            resp = self.session.get(f"{CKAN}/status_show", timeout=15)
            return resp.status_code < 400
        except Exception as e:
            self.logger.warning("health_check erro: %s", e)
            return False
