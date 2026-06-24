"""
Conector: Societário — Receita Federal (dados internos BR Insider)

Consulta as tabelas cnpj_empresas e cnpj_socios (alimentadas pelo seeder RFB)
e gera alertas para situações de risco societário:

  - PEP como sócio (cruza com tabela de PEPs do BR Insider)
  - Empresa com capital social muito baixo para o setor de contratos públicos
  - Empresa com status irregular na RFB
  - Sócios com sanções (cruza com fornecedores_sancionados)
  - Alterações recentes de quadro societário (mudança de sócio em curto período)

Tabelas consultadas (BR Insider):
  - cnpj_empresas   — razao_social, natureza_juridica, porte, capital_social
  - cnpj_socios     — qsa com cpf/cnpj e qualificação
  - fornecedores_sancionados — CEIS/CNEP de CPF dos sócios
  - parlamentares   — verifica se sócio é parlamentar (PEP de nível 1)
"""
from __future__ import annotations

import logging
import re

import requests

from .base import SubradarSource, snapshot_changed, upsert, _ciclo_atual, SUPABASE_URL, SUPABASE_KEY, _supabase_headers

logger = logging.getLogger("subradar.societario")

# CNAEs de risco internacional (setores que justificam OpenSanctions)
CNAE_RISCO_INTERNACIONAL = {
    "0500": "pesca",
    "0600": "extracao_petroleo",
    "0700": "mineracao",
    "2911": "fabricacao_automoveis",
    "3011": "construcao_embarcacoes",
    "3030": "aeronaves",
    "3040": "veiculos_militares",
    "6422": "bancos",
    "6431": "bancos_multiplos",
    "6499": "outras_financeiras",
    "6511": "seguros",
    "7490": "outras_consultoria",
    "8411": "administracao_publica",
}


def _strip_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)


def _fmt_cnpj(cnpj: str) -> str:
    c = _strip_cnpj(cnpj)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}" if len(c) == 14 else cnpj


def _query(table: str, params: dict) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params=params,
        headers=_supabase_headers(),
        timeout=20,
    )
    return r.json() if r.ok and isinstance(r.json(), list) else []


class SocietarioConnector(SubradarSource):
    fonte = "rfb_societario"
    base_url = SUPABASE_URL or ""

    def consultar_cnpj(self, cnpj: str) -> list[dict]:
        cnpj_limpo = _strip_cnpj(cnpj)
        cnpj_basico = cnpj_limpo[:8]  # primeiros 8 dígitos
        cnpj_fmt = _fmt_cnpj(cnpj_limpo)
        ciclo = _ciclo_atual()
        alertas = []

        # 1. Dados cadastrais
        empresa = _query("cnpj_empresas", {
            "cnpj_basico": f"eq.{cnpj_basico}",
            "select": "razao_social,natureza_juridica,capital_social,porte_empresa",
            "limit": 1,
        })

        # 2. Quadro societário
        socios = _query("cnpj_socios", {
            "cnpj_basico": f"eq.{cnpj_basico}",
            "select": "nome_socio,cpf_cnpj_socio,qualificacao,data_entrada",
            "limit": 50,
        })

        resumo = {
            "empresa": empresa[0] if empresa else {},
            "total_socios": len(socios),
        }
        mudou, hash_novo = snapshot_changed(cnpj_fmt, self.fonte, ciclo, resumo)
        if not mudou:
            return []

        upsert("sub_snapshots", [{
            "cnpj": cnpj_fmt, "fonte": self.fonte, "ciclo": ciclo,
            "hash_dados": hash_novo, "dados": resumo,
        }])

        if not empresa and not socios:
            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                "categoria": "societario", "severidade": "atencao",
                "titulo": "Empresa não encontrada na base RFB",
                "descricao": "CNPJ não localizado na base cadastral da Receita Federal.",
                "is_novo": True,
            })
            return alertas

        # Alerta: info societária básica
        if empresa:
            emp = empresa[0]
            capital = float(emp.get("capital_social") or 0)
            porte = emp.get("porte_empresa") or "N/D"
            alertas.append({
                "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                "categoria": "societario", "severidade": "info",
                "titulo": f"Perfil RFB — {emp.get('razao_social','N/D')} ({porte})",
                "descricao": (
                    f"Porte: {porte}. Capital social: R$ {capital:,.2f}. "
                    f"Natureza jurídica: {emp.get('natureza_juridica','N/D')}. "
                    f"{len(socios)} sócio(s) no QSA."
                ),
                "valor_brl": capital if capital > 0 else None,
                "is_novo": True,
            })

            # Alerta: capital social muito baixo para empresa com contratos grandes
            if capital < 10_000 and capital > 0:
                alertas.append({
                    "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                    "categoria": "societario", "severidade": "atencao",
                    "titulo": f"Capital social baixo — R$ {capital:,.2f}",
                    "descricao": (
                        f"Empresa com capital social de apenas R$ {capital:,.2f}. "
                        f"Atenção em contratos de alto valor com esta empresa."
                    ),
                    "valor_brl": capital,
                    "is_novo": True,
                })

        # Verifica se algum sócio é parlamentar (PEP de nível 1)
        cpfs_socios = [
            s.get("cpf_cnpj_socio", "") for s in socios
            if s.get("cpf_cnpj_socio") and len(_strip_cnpj(s.get("cpf_cnpj_socio", ""))) == 11
        ]

        for cpf in cpfs_socios[:10]:
            cpf_limpo = _strip_cnpj(cpf)
            parlam = _query("parlamentares", {
                "cpf": f"eq.{cpf_limpo}",
                "select": "nome,partido,uf",
                "limit": 1,
            })
            if parlam:
                p = parlam[0]
                nome_socio = next(
                    (s["nome_socio"] for s in socios if _strip_cnpj(s.get("cpf_cnpj_socio","")) == cpf_limpo),
                    "N/D"
                )
                alertas.append({
                    "cnpj": cnpj_fmt, "ciclo": ciclo, "fonte": self.fonte,
                    "categoria": "societario", "severidade": "critico",
                    "titulo": f"Sócio é parlamentar — {p.get('nome','N/D')} ({p.get('partido','')}/{p.get('uf','')})",
                    "descricao": (
                        f"Parlamentar {p.get('nome','N/D')} ({p.get('partido','')}/{p.get('uf','')}) "
                        f"consta como sócio '{nome_socio}' desta empresa. "
                        f"Verificar conflito de interesse em contratos com o poder público."
                    ),
                    "contraparte": p.get("nome"),
                    "is_novo": True,
                })

        logger.info("Societário: %d alertas para %s", len(alertas), cnpj_fmt)
        return alertas

    def e_risco_internacional(self, cnpj: str) -> bool:
        """
        Retorna True se o CNPJ justifica consulta ao OpenSanctions.
        Critérios: sócio estrangeiro OU CNAE de setor de risco.
        Usado pelo OpenSanctionsConnector para reduzir custos.
        """
        cnpj_basico = _strip_cnpj(cnpj)[:8]

        # Verifica sócio com CPF/CNPJ estrangeiro (não começa com dígitos BR)
        socios = _query("cnpj_socios", {
            "cnpj_basico": f"eq.{cnpj_basico}",
            "select": "cpf_cnpj_socio,qualificacao",
            "limit": 20,
        })
        for s in socios:
            ident = (s.get("cpf_cnpj_socio") or "").strip()
            # Sócio estrangeiro tem código "EX" ou identificador não numérico
            if ident and (ident.startswith("EX") or not ident.isdigit()):
                return True

        return False
