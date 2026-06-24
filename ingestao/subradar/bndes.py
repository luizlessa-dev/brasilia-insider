"""
Conector: Contratos Federais + Emendas (fonte interna BR Insider)

Pivot do BNDES original: o CSV do BNDES mascara o CNPJ do cliente,
tornando lookup direto impossível. Em vez disso, este conector consulta
as tabelas internas do BR Insider:

  - contratos_federais  (PNCP — Portal Nacional de Contratações Públicas)
  - emendas_favorecidos (emendas parlamentares por CNPJ beneficiado)

Alertas gerados: contratos recentes, contratos com empresas na situação
problemática, concentração de emendas. Categoria: 'contrato', 'emenda'.
Fonte: 'pncp' / 'emenda_interna'.

Para adicionar BNDES real no futuro:
  - Opção A: solicitar ao BNDES acesso à API via https://dadosabertos.bndes.gov.br/
  - Opção B: scraping da consulta pública https://www.bndes.gov.br/wps/portal/
    site/home/transparencia/consulta-operacoes-bndes/
"""
from __future__ import annotations

import logging
import os
import re

import requests

from .base import SubradarSource, snapshot_changed, upsert, _ciclo_atual, SUPABASE_URL, SUPABASE_KEY, _supabase_headers

logger = logging.getLogger("subradar.bndes")


def _strip_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)


def _fmt_cnpj(cnpj: str) -> str:
    c = _strip_cnpj(cnpj)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}" if len(c) == 14 else cnpj


def _query_supabase(table: str, params: dict) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    r = requests.get(url, params=params, headers=_supabase_headers(), timeout=20)
    if not r.ok:
        logger.warning("Query %s falhou: %s", table, r.text[:200])
        return []
    return r.json() if isinstance(r.json(), list) else []


class BNDESConnector(SubradarSource):
    """
    Consulta contratos federais e emendas parlamentares no banco BR Insider.
    Gera alertas de risco quando o CNPJ tem contratos públicos ativos ou
    é beneficiário relevante de emendas.
    """
    fonte = "pncp"
    base_url = SUPABASE_URL or ""

    def consultar_cnpj(self, cnpj: str) -> list[dict]:
        cnpj_limpo = _strip_cnpj(cnpj)
        cnpj_fmt = _fmt_cnpj(cnpj_limpo)
        ciclo = _ciclo_atual()
        alertas = []

        # 1. Contratos federais (PNCP)
        contratos = _query_supabase("contratos_federais", {
            "fornecedor_cnpj": f"eq.{cnpj_limpo}",
            "select": "numero,objeto,valor_total,data_fim_vigencia,situacao_descricao,orgao_descricao",
            "order": "data_fim_vigencia.desc",
            "limit": 50,
        })

        if contratos:
            valor_total = sum(float(c.get("valor_total") or 0) for c in contratos)
            ativos = [c for c in contratos if (c.get("situacao_descricao") or "").lower() in ("vigente", "ativo", "em execução")]
            irregulares = [c for c in contratos if "irreg" in (c.get("situacao_descricao") or "").lower()]

            # Alerta resumo de contratos
            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                "categoria": "contrato", "severidade": "info",
                "titulo": f"{len(contratos)} contrato(s) federal(is) — R$ {valor_total:,.2f}",
                "descricao": (
                    f"CNPJ possui {len(contratos)} contrato(s) no PNCP. "
                    f"{len(ativos)} ativo(s), valor total R$ {valor_total:,.2f}."
                ),
                "valor_brl": valor_total,
                "contraparte": "Governo Federal (PNCP)",
                "url_fonte": f"https://pncp.gov.br/app/fornecedor/{cnpj_limpo}/contratos",
                "is_novo": True,
            })

            if irregulares:
                alertas.append({
                    "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                    "categoria": "contrato", "severidade": "critico",
                    "titulo": f"{len(irregulares)} contrato(s) com situação irregular",
                    "descricao": (
                        f"Contratos em situação irregular: "
                        + "; ".join(c.get("numero", "N/D") for c in irregulares[:3])
                    ),
                    "is_novo": True,
                })

        # 2. Emendas parlamentares (emendas_favorecidos)
        emendas = _query_supabase("emendas_favorecidos", {
            "codigo_favorecido": f"eq.{cnpj_limpo}",
            "select": "valor_recebido,nome_autor,ano_emenda,tipo_emenda",
            "order": "valor_recebido.desc",
            "limit": 100,
        })

        if emendas:
            valor_emendas = sum(float(e.get("valor_recebido") or 0) for e in emendas)
            autores = list({e.get("nome_autor") for e in emendas if e.get("nome_autor")})
            severidade = "critico" if valor_emendas > 5_000_000 else "atencao" if valor_emendas > 500_000 else "info"

            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": "emenda_interna",
                "categoria": "emenda", "severidade": severidade,
                "titulo": f"R$ {valor_emendas:,.2f} em emendas parlamentares ({len(emendas)} transação(ões))",
                "descricao": (
                    f"CNPJ recebeu R$ {valor_emendas:,.2f} em emendas parlamentares "
                    f"de {len(autores)} parlamentar(es) diferente(s). "
                    + (f"Principais: {', '.join(autores[:3])}." if autores else "")
                ),
                "valor_brl": valor_emendas,
                "contraparte": f"{len(autores)} parlamentar(es)",
                "url_fonte": "https://www.thebrinsider.com",
                "is_novo": True,
            })

        if not alertas:
            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                "categoria": "contrato", "severidade": "ok",
                "titulo": "Sem contratos federais ou emendas identificadas",
                "descricao": "CNPJ não encontrado nas bases de contratos (PNCP) ou emendas parlamentares.",
                "is_novo": True,
            })

        # Snapshot para delta detection
        resumo = {"contratos": len(contratos), "emendas": len(emendas)}
        mudou, hash_novo = snapshot_changed(cnpj_fmt, self.fonte, ciclo, resumo)
        if mudou:
            upsert("sub_snapshots", [{
                "cnpj": cnpj_fmt, "fonte": self.fonte, "ciclo": ciclo,
                "hash_dados": hash_novo, "dados": resumo,
            }])
        else:
            return []  # sem mudança desde o último ciclo

        logger.info("pncp/emendas: %d alertas para %s", len(alertas), cnpj_fmt)
        return alertas
