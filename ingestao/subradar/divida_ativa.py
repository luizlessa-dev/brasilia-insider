"""
Conector: Dívida Ativa da União — Portal da Transparência

API: https://api.portaldatransparencia.gov.br/api-de-dados/devedores-uniao
Auth: chave-api-dados header (nível 2 do Portal da Transparência)

ATENÇÃO: o endpoint devedores-uniao requer chave com acesso nível 2.
Solicite em https://portaldatransparencia.gov.br/api-de-dados/cadastro-usuario
e defina PORTAL_TP_KEY_NIVEL2 no .env quando receber.

Enquanto a chave não estiver disponível, o conector retorna lista vazia
(não falha o pipeline).

Tabela: sub_alertas (categoria='divida', fonte='pgfn')
"""
from __future__ import annotations

import logging
import os
import re

from .base import SubradarSource, snapshot_changed, upsert, _ciclo_atual

logger = logging.getLogger("subradar.divida_ativa")

PT_BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"
# Usa key nível 2 se disponível, senão tenta a key padrão do pipeline
PT_KEY = (
    os.environ.get("PORTAL_TP_KEY_NIVEL2")
    or os.environ.get("PORTAL_TRANSPARENCIA_API_KEY")
    or ""
)


def _strip_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)


def _fmt_cnpj(cnpj: str) -> str:
    c = _strip_cnpj(cnpj)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}" if len(c) == 14 else cnpj


class DividaAtivaConnector(SubradarSource):
    fonte = "pgfn"
    base_url = PT_BASE

    def _headers(self) -> dict:
        return {"chave-api-dados": PT_KEY, "Accept": "application/json"}

    def consultar_cnpj(self, cnpj: str) -> list[dict]:
        cnpj_limpo = _strip_cnpj(cnpj)
        cnpj_fmt = _fmt_cnpj(cnpj_limpo)
        ciclo = _ciclo_atual()

        if not PT_KEY:
            logger.warning("PGFN: PORTAL_TP_KEY_NIVEL2 não configurada — pulando %s", cnpj_fmt)
            return []

        try:
            url = f"{PT_BASE}/devedores-uniao"
            data = self._get(url, params={"cnpj": cnpj_limpo, "pagina": 1}, headers=self._headers())
        except Exception as e:
            logger.warning("PGFN: erro ao consultar %s — %s", cnpj_fmt, e)
            return []

        registros = data if isinstance(data, list) else data.get("data", [])
        mudou, hash_novo = snapshot_changed(cnpj_fmt, self.fonte, ciclo, registros)
        if not mudou:
            return []

        upsert("sub_snapshots", [{
            "cnpj": cnpj_fmt, "fonte": self.fonte, "ciclo": ciclo,
            "hash_dados": hash_novo, "dados": {"total": len(registros)},
        }])

        alertas = []
        if not registros:
            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                "categoria": "divida", "severidade": "ok",
                "titulo": "Sem dívida ativa registrada (PGFN)",
                "descricao": "CNPJ não encontrado na base de devedores da União.",
                "is_novo": True,
            })
        else:
            valor_total = sum(float(r.get("valorConsolidado") or 0) for r in registros)
            severidade = "critico" if valor_total > 1_000_000 else "atencao"
            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                "categoria": "divida", "severidade": severidade,
                "titulo": f"Dívida ativa na União — R$ {valor_total:,.2f} ({len(registros)} débito(s))",
                "descricao": (
                    f"CNPJ inscrito em dívida ativa da União (PGFN). "
                    f"{len(registros)} débito(s) ativo(s), total R$ {valor_total:,.2f}."
                ),
                "valor_brl": valor_total,
                "contraparte": "PGFN / Receita Federal",
                "url_fonte": f"https://www.regularize.pgfn.gov.br/situacao/{cnpj_limpo}",
                "is_novo": True,
            })

        logger.info("PGFN: %d alertas para %s", len(alertas), cnpj_fmt)
        return alertas
