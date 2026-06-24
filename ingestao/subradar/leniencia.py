"""
Conector: Acordos de Leniência — Portal da Transparência

API: https://api.portaldatransparencia.gov.br/api-de-dados/acordos-leniencia
Auth: chave-api-dados (chave padrão do pipeline)

Cobre acordos firmados pela CGU, MPF e AGU com empresas que cometeram
atos ilícitos e colaboraram com as investigações (Lei Anticorrupção 12.846/2013).

Fonte histórica importante: Lava Jato, Carne Fraca, etc.
Severidade sempre CRITICO — empresa assinou acordo de leniência = infração grave confirmada.
"""
from __future__ import annotations

import logging
import os
import re

from .base import SubradarSource, snapshot_changed, upsert, _ciclo_atual

logger = logging.getLogger("subradar.leniencia")

PT_BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"
PT_KEY = os.environ.get("PORTAL_TRANSPARENCIA_API_KEY", "")


def _strip_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)


def _fmt_cnpj(cnpj: str) -> str:
    c = _strip_cnpj(cnpj)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}" if len(c) == 14 else cnpj


class LenienciaConnector(SubradarSource):
    fonte = "leniencia"
    base_url = PT_BASE
    request_delay = 0.3

    def _headers(self) -> dict:
        return {"chave-api-dados": PT_KEY, "Accept": "application/json"}

    def _buscar_por_cnpj(self, cnpj: str) -> list[dict]:
        """Percorre todas as páginas de acordos e filtra por CNPJ."""
        encontrados = []
        pagina = 1
        while True:
            try:
                data = self._get(
                    f"{PT_BASE}/acordos-leniencia",
                    params={"cnpj": cnpj, "pagina": pagina},
                    headers=self._headers(),
                )
            except Exception as e:
                logger.warning("Leniência API erro p.%d: %s", pagina, e)
                break

            items = data if isinstance(data, list) else []
            if not items:
                break

            for item in items:
                sancoes = item.get("sancoes") or []
                for s in sancoes:
                    cnpj_item = _strip_cnpj(s.get("cnpj") or s.get("cnpjFormatado") or "")
                    if cnpj_item == cnpj or not cnpj_item:
                        encontrados.append({**item, "_sancao": s})

            if len(items) < 10:
                break
            pagina += 1

        return encontrados

    def consultar_cnpj(self, cnpj: str) -> list[dict]:
        if not PT_KEY:
            logger.warning("Leniência: PORTAL_TRANSPARENCIA_API_KEY ausente")
            return []

        cnpj_limpo = _strip_cnpj(cnpj)
        cnpj_fmt = _fmt_cnpj(cnpj_limpo)
        ciclo = _ciclo_atual()

        registros = self._buscar_por_cnpj(cnpj_limpo)

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
                "categoria": "sancao", "severidade": "ok",
                "titulo": "Sem acordos de leniência",
                "descricao": "CNPJ não encontrado na base de acordos de leniência da CGU/MPF/AGU.",
                "is_novo": True,
            })
        else:
            for r in registros:
                s = r.get("_sancao", {})
                nome = s.get("nomeInformadoOrgaoResponsavel") or s.get("razaoSocial") or cnpj_fmt
                orgao = r.get("orgaoResponsavel", "N/D")
                situacao = r.get("situacaoAcordo", "N/D")
                data_inicio = r.get("dataInicioAcordo", "")
                data_fim = r.get("dataFimAcordo", "")

                alertas.append({
                    "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                    "categoria": "sancao", "severidade": "critico",
                    "titulo": f"Acordo de leniência — {orgao} ({situacao})",
                    "descricao": (
                        f"Empresa '{nome}' firmou acordo de leniência com {orgao}. "
                        f"Situação: {situacao}. Vigência: {data_inicio} a {data_fim}."
                    ),
                    "contraparte": orgao,
                    "referencia_id": str(r.get("id", "")),
                    "data_evento": _parse_data(data_inicio),
                    "url_fonte": "https://www.portaldatransparencia.gov.br/leniencia",
                    "is_novo": True,
                })

        logger.info("Leniência: %d alertas para %s", len(alertas), cnpj_fmt)
        return alertas


def _parse_data(s: str) -> str | None:
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(s, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None
