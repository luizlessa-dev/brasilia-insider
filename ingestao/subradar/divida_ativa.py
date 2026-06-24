"""
Conector: Dívida Ativa da União — consulta na tabela interna pgfn_divida_ativa

A tabela é alimentada trimestralmente pelo pgfn_seeder.py, que baixa
os ZIPs de https://dadosabertos.pgfn.gov.br/ e os insere no Supabase.

Este conector lê localmente (sem chamadas externas) e gera alertas
quando o CNPJ aparece como devedor, com severidade por valor e ajuizamento.
"""
from __future__ import annotations

import logging
import re

import requests

from .base import SubradarSource, snapshot_changed, upsert, _ciclo_atual, SUPABASE_URL, SUPABASE_KEY, _supabase_headers

logger = logging.getLogger("subradar.divida_ativa")


def _strip_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)


def _fmt_cnpj(cnpj: str) -> str:
    c = _strip_cnpj(cnpj)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}" if len(c) == 14 else cnpj


def _query_pgfn(cnpj: str) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/pgfn_divida_ativa"
    params = {
        "cpf_cnpj": f"eq.{cnpj}",
        "select": "situacao,tipo_credito,valor_consolidado,indicador_ajuizado,arquivo,ciclo,nome_devedor",
        "order": "valor_consolidado.desc",
        "limit": 200,
    }
    r = requests.get(url, params=params, headers=_supabase_headers(), timeout=20)
    if not r.ok:
        logger.warning("Query pgfn_divida_ativa falhou: %s", r.text[:200])
        return []
    return r.json() if isinstance(r.json(), list) else []


class DividaAtivaConnector(SubradarSource):
    fonte = "pgfn"
    base_url = SUPABASE_URL or ""

    def consultar_cnpj(self, cnpj: str) -> list[dict]:
        cnpj_limpo = _strip_cnpj(cnpj)
        cnpj_fmt = _fmt_cnpj(cnpj_limpo)
        ciclo = _ciclo_atual()

        registros = _query_pgfn(cnpj_limpo)

        resumo = {"total": len(registros)}
        mudou, hash_novo = snapshot_changed(cnpj_fmt, self.fonte, ciclo, resumo)
        if not mudou:
            return []

        upsert("sub_snapshots", [{
            "cnpj": cnpj_fmt, "fonte": self.fonte, "ciclo": ciclo,
            "hash_dados": hash_novo, "dados": resumo,
        }])

        alertas = []

        if not registros:
            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                "categoria": "divida", "severidade": "ok",
                "titulo": "Sem dívida ativa na PGFN",
                "descricao": "CNPJ não encontrado na base de devedores da União (PGFN).",
                "is_novo": True,
            })
        else:
            valor_total = sum(float(r.get("valor_consolidado") or 0) for r in registros)
            ajuizados = [r for r in registros if (r.get("indicador_ajuizado") or "").upper() == "SIM"]
            ciclo_pgfn = registros[0].get("ciclo", "N/D")

            severidade = "critico" if (valor_total > 1_000_000 or ajuizados) else "atencao"

            descricao = (
                f"CNPJ inscrito em dívida ativa da União (PGFN). "
                f"{len(registros)} inscrição(ões), valor total R$ {valor_total:,.2f}."
            )
            if ajuizados:
                descricao += f" {len(ajuizados)} débito(s) ajuizado(s) (execução fiscal em andamento)."

            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                "categoria": "divida", "severidade": severidade,
                "titulo": f"Dívida ativa na União — R$ {valor_total:,.2f} ({len(registros)} inscrição(ões))",
                "descricao": descricao,
                "valor_brl": valor_total,
                "contraparte": "PGFN / Receita Federal",
                "url_fonte": f"https://www.regularize.pgfn.gov.br/",
                "is_novo": True,
            })

        logger.info("PGFN: %d alertas para %s", len(alertas), cnpj_fmt)
        return alertas
