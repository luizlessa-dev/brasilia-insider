"""
Conector: OpenSanctions — base global de sanções internacionais

API: https://api.opensanctions.org/
Docs: https://www.opensanctions.org/docs/api/

Cobre: OFAC (EUA), UE, ONU, INTERPOL, UK FCDO, e ~100 outras listas.
Relevante para: clientes com operações internacionais ou sócios estrangeiros.

Alertas gerados: categoria='internacional', fonte='opensanctions'

Env var opcional: OPENSANCTIONS_API_KEY
  - Sem key: funciona com rate limit (5 req/s)
  - Com key: rate limit mais alto
"""
from __future__ import annotations

import logging
import os
import re

from .base import SubradarSource, snapshot_changed, upsert, _ciclo_atual

logger = logging.getLogger("subradar.opensanctions")

OS_BASE = "https://api.opensanctions.org"
OS_KEY = os.environ.get("OPENSANCTIONS_API_KEY", "")

# Datasets de maior relevância para compliance BR
DATASETS_PRIORITARIOS = [
    "us_ofac_sdn",       # OFAC Specially Designated Nationals (EUA)
    "eu_sanctions",      # União Europeia
    "un_sc_sanctions",   # Nações Unidas
    "gb_hmt_sanctions",  # Reino Unido
    "interpol_red_notices",  # INTERPOL
    "br_tcu_inabilitados",   # TCU — inabilitados para cargo público
    "br_ceis",           # CEIS Brasil (duplicado intencional para cross-check)
]

# Mapeamento dataset → nome legível
DATASET_LABELS = {
    "us_ofac_sdn": "OFAC/EUA",
    "eu_sanctions": "Sanções UE",
    "un_sc_sanctions": "Sanções ONU",
    "gb_hmt_sanctions": "Sanções Reino Unido",
    "interpol_red_notices": "INTERPOL",
    "br_tcu_inabilitados": "TCU Inabilitados",
    "br_ceis": "CEIS/BR (OpenSanctions)",
}


def _strip_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)


def _fmt_cnpj(cnpj: str) -> str:
    c = _strip_cnpj(cnpj)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}" if len(c) == 14 else cnpj


class OpenSanctionsConnector(SubradarSource):
    fonte = "opensanctions"
    base_url = OS_BASE
    request_delay = 0.3

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if OS_KEY:
            h["Authorization"] = f"ApiKey {OS_KEY}"
        return h

    def _search(self, query: str, schema: str = "Company") -> list[dict]:
        """Busca entidade por nome/número na base global."""
        try:
            r = self._session.get(
                f"{OS_BASE}/entities/",
                params={"q": query, "schema": schema, "limit": 10},
                headers=self._headers(),
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json().get("results", [])
        except Exception as e:
            logger.warning("OpenSanctions search '%s' falhou: %s", query, e)
            return []

    def _get_entity(self, entity_id: str) -> dict | None:
        """Busca entidade por ID direto."""
        try:
            r = self._session.get(
                f"{OS_BASE}/entities/{entity_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("OpenSanctions entity %s falhou: %s", entity_id, e)
            return None

    def consultar_cnpj(self, cnpj: str) -> list[dict]:
        """
        Consulta CNPJ e razão social contra as bases internacionais.
        Retorna alertas de sanções internacionais encontradas.

        Requer OPENSANCTIONS_API_KEY no .env.
        Registre em https://www.opensanctions.org/api/ (gratuito para projetos abertos).
        """
        if not OS_KEY:
            logger.warning("OpenSanctions: OPENSANCTIONS_API_KEY não configurada — pulando %s", cnpj)
            return []

        cnpj_limpo = _strip_cnpj(cnpj)
        cnpj_fmt = _fmt_cnpj(cnpj_limpo)
        ciclo = _ciclo_atual()
        alertas = []

        # Busca pelo número do CNPJ diretamente
        resultados = self._search(cnpj_limpo, schema="Company")

        # Se não achou pelo número, pode-se buscar pela razão social
        # (razão social precisaria vir do chamador — extensão futura)

        if not resultados:
            logger.info("OpenSanctions: sem resultados para %s", cnpj_fmt)
            return []

        mudou, hash_novo = snapshot_changed(cnpj_fmt, self.fonte, ciclo, resultados)
        if not mudou:
            return []

        upsert("sub_snapshots", [{
            "cnpj": cnpj_fmt,
            "fonte": self.fonte,
            "ciclo": ciclo,
            "hash_dados": hash_novo,
            "dados": {"total": len(resultados)},
        }])

        for entidade in resultados:
            datasets = entidade.get("datasets", [])
            properties = entidade.get("properties", {})
            nome = (properties.get("name") or [entidade.get("caption", "")])[0]

            # Filtra só os datasets prioritários para reduzir ruído
            hits = [d for d in datasets if d in DATASETS_PRIORITARIOS]
            if not hits:
                continue

            # Sanções internacionais são sempre crítico
            lista_labels = ", ".join(DATASET_LABELS.get(d, d) for d in hits)
            alertas.append({
                "cnpj": cnpj_fmt,
                "ciclo": ciclo,
                "fonte": self.fonte,
                "categoria": "internacional",
                "severidade": "critico",
                "titulo": f"Sanção internacional — {lista_labels}",
                "descricao": (
                    f"Entidade '{nome}' encontrada em lista(s) de sanção: {lista_labels}. "
                    f"Verificar antes de qualquer operação com contrapartes no exterior."
                ),
                "contraparte": lista_labels,
                "referencia_id": entidade.get("id"),
                "url_fonte": f"https://www.opensanctions.org/entities/{entidade.get('id')}/",
                "is_novo": True,
            })

        logger.info("OpenSanctions: %d alertas para %s", len(alertas), cnpj_fmt)
        return alertas

    def consultar_pessoa(self, nome: str, cpf: str | None = None) -> list[dict]:
        """
        Consulta pessoa física (sócios PEP) contra bases internacionais.
        Útil para cruzar sócios identificados com sanções globais.
        """
        ciclo = _ciclo_atual()
        alertas = []
        query = cpf or nome

        resultados = self._search(query, schema="Person")
        if not resultados and cpf:
            resultados = self._search(nome, schema="Person")

        for entidade in resultados:
            datasets = entidade.get("datasets", [])
            properties = entidade.get("properties", {})
            nome_entidade = (properties.get("name") or [entidade.get("caption", "")])[0]

            hits = [d for d in datasets if d in DATASETS_PRIORITARIOS]
            if not hits:
                continue

            lista_labels = ", ".join(DATASET_LABELS.get(d, d) for d in hits)
            alertas.append({
                "cnpj": None,
                "ciclo": ciclo,
                "fonte": self.fonte,
                "categoria": "internacional",
                "severidade": "critico",
                "titulo": f"Pessoa em sanção internacional — {lista_labels}",
                "descricao": (
                    f"Pessoa '{nome_entidade}' (buscada como '{nome}') "
                    f"encontrada em: {lista_labels}."
                ),
                "contraparte": lista_labels,
                "referencia_id": entidade.get("id"),
                "url_fonte": f"https://www.opensanctions.org/entities/{entidade.get('id')}/",
                "is_novo": True,
            })

        return alertas
