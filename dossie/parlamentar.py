"""
Dossiê de parlamentar — agrega emendas, cota parlamentar e financiamento de campanha.

Identificadores no banco (distintos — atenção):
  parlamentares.id_camara        → ID da API dadosabertos.camara.leg.br  (ex: 209787)
  parlamentares.cpf              → CPF do deputado (chave mestra)
  cota_deputado.id_camara        → nuDeputadoId do CSV CEAP              (ex: 3605)
  emendas_favorecidos.codigo_autor → código do sistema SIAFI/Orçamento   (ex: "4439")

O builder resolve esses três IDs automaticamente a partir do CPF ou nome.

Uso:
    from dossie.parlamentar import DossieParlamentar
    d = DossieParlamentar.from_env()

    # por CPF (mais confiável)
    rel = d.gerar(cpf="11701442680")

    # por nome (fuzzy — usa ilike)
    rel = d.gerar(nome="Nikolas Ferreira")

    # imprimir
    d.imprimir(rel)

    # salvar JSON
    import json
    with open("nikolas.json", "w") as f:
        json.dump(d.para_dict(rel), f, ensure_ascii=False, indent=2, default=str)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .client import SupabaseClient

logger = logging.getLogger("dossie.parlamentar")


# ─── Modelos ──────────────────────────────────────────────────────────────────

@dataclass
class IdentificadorParlamentar:
    """Resolve os 3 IDs distintos usados em tabelas diferentes."""
    cpf: str
    nome: str
    nome_parlamentar: str
    partido: str
    uf: str
    id_camara_api: int           # parlamentares.id_camara  (API)
    id_camara_ceap: Optional[int]  # cota_deputado.id_camara  (CSV CEAP)
    codigo_autor_emendas: Optional[str]  # emendas_favorecidos.codigo_autor


@dataclass
class ResumoEmendas:
    total_transacoes: int
    total_valor: float
    por_ano: dict[int, float]
    por_tipo: dict[str, float]
    por_uf: dict[str, float]
    top_favorecidos: list[dict]   # [{favorecido, cnpj, total, n}]
    transacoes: list[dict]        # raw (limite 500)


@dataclass
class ResumoCota:
    total_transacoes: int
    total_valor: float
    por_ano: dict[int, float]
    por_tipo_despesa: dict[str, float]
    top_fornecedores: list[dict]  # [{nome, cnpj, total, n}]
    transacoes: list[dict]        # raw (limite 500)


@dataclass
class ResumoFinanciamento:
    total_transacoes: int
    total_arrecadado: float
    por_ano: dict[int, float]
    por_tipo_doador: dict[str, float]
    top_doadores: list[dict]      # [{nome, cpf_cnpj, total, n}]


@dataclass
class RelatorioParlamentar:
    identificador: IdentificadorParlamentar
    emendas: ResumoEmendas
    cota: ResumoCota
    financiamento: ResumoFinanciamento


# ─── Builder ──────────────────────────────────────────────────────────────────

class DossieParlamentar:
    def __init__(self, client: SupabaseClient) -> None:
        self._c = client

    @classmethod
    def from_env(cls) -> "DossieParlamentar":
        return cls(SupabaseClient.from_env())

    # ── 1. Resolução de identidade ────────────────────────────────────────

    def _resolver_id(self, cpf: str | None, nome: str | None) -> IdentificadorParlamentar:
        """Busca o parlamentar e resolve os 3 IDs distintos."""
        if cpf:
            rows = self._c.get("parlamentares", {
                "cpf": f"eq.{cpf}",
                "select": "id_camara,cpf,nome,nome_parlamentar,partido,uf",
            }, limit=1)
        elif nome:
            rows = self._c.get("parlamentares", {
                "nome": f"ilike.*{nome}*",
                "select": "id_camara,cpf,nome,nome_parlamentar,partido,uf",
            }, limit=1)
        else:
            raise ValueError("Forneça cpf ou nome.")

        if not rows:
            raise LookupError(f"Parlamentar não encontrado: cpf={cpf!r} nome={nome!r}")

        p = rows[0]
        cpf_resolvido = p["cpf"]
        id_camara_api = p["id_camara"]

        # ID no CEAP (cota_deputado.id_camara = nuDeputadoId do CSV)
        ceap = self._c.get("cota_deputado", {
            "cpf": f"eq.{cpf_resolvido}",
            "select": "id_camara",
        }, limit=1)
        id_ceap = ceap[0]["id_camara"] if ceap else None

        # codigo_autor nas emendas (SIAFI — string, diferente dos demais)
        em = self._c.get("emendas_favorecidos", {
            "nome_autor": f"ilike.*{p['nome_parlamentar'] or p['nome']}*",
            "select": "codigo_autor",
            "limit": "1",
        }, limit=1)
        codigo_autor = em[0]["codigo_autor"] if em else None

        return IdentificadorParlamentar(
            cpf=cpf_resolvido,
            nome=p["nome"],
            nome_parlamentar=p.get("nome_parlamentar") or p["nome"],
            partido=p["partido"],
            uf=p["uf"],
            id_camara_api=id_camara_api,
            id_camara_ceap=id_ceap,
            codigo_autor_emendas=codigo_autor,
        )

    # ── 2. Emendas ────────────────────────────────────────────────────────

    def _buscar_emendas(self, ids: IdentificadorParlamentar) -> ResumoEmendas:
        if not ids.codigo_autor_emendas:
            logger.warning("codigo_autor não encontrado — emendas zeradas.")
            return ResumoEmendas(0, 0.0, {}, {}, {}, [], [])

        rows = self._c.get_all("emendas_favorecidos", {
            "codigo_autor": f"eq.{ids.codigo_autor_emendas}",
            "select": "ano_emenda,valor_recebido,uf_favorecido,municipio_favorecido,"
                      "tipo_emenda,subtipo,codigo_favorecido,favorecido,natureza_juridica",
        })

        total = sum(float(r.get("valor_recebido") or 0) for r in rows)

        por_ano: dict[int, float] = {}
        por_tipo: dict[str, float] = {}
        por_uf: dict[str, float] = {}
        favs: dict[str, dict] = {}

        for r in rows:
            ano = r.get("ano_emenda") or 0
            val = float(r.get("valor_recebido") or 0)
            tipo = r.get("tipo_emenda") or "N/A"
            uf = r.get("uf_favorecido") or "N/A"
            fav = (r.get("favorecido") or "").strip().lstrip(",")
            cnpj = r.get("codigo_favorecido") or ""

            por_ano[ano] = por_ano.get(ano, 0.0) + val
            por_tipo[tipo] = por_tipo.get(tipo, 0.0) + val
            por_uf[uf] = por_uf.get(uf, 0.0) + val

            if fav:
                if fav not in favs:
                    favs[fav] = {"favorecido": fav, "cnpj": cnpj, "total": 0.0, "n": 0}
                favs[fav]["total"] += val
                favs[fav]["n"] += 1

        top_favs = sorted(favs.values(), key=lambda x: -x["total"])[:20]

        return ResumoEmendas(
            total_transacoes=len(rows),
            total_valor=total,
            por_ano=dict(sorted(por_ano.items())),
            por_tipo=dict(sorted(por_tipo.items(), key=lambda x: -x[1])),
            por_uf=dict(sorted(por_uf.items(), key=lambda x: -x[1])),
            top_favorecidos=top_favs,
            transacoes=rows[:500],
        )

    # ── 3. Cota parlamentar ───────────────────────────────────────────────

    def _buscar_cota(self, ids: IdentificadorParlamentar) -> ResumoCota:
        if not ids.id_camara_ceap:
            logger.warning("id_camara_ceap não encontrado — cota zerada.")
            return ResumoCota(0, 0.0, {}, {}, [], [])

        rows = self._c.get_all("cota_despesa", {
            "id_deputado": f"eq.{ids.id_camara_ceap}",
            "select": "ano,mes,tipo_despesa,nome_fornecedor,cnpj_cpf_fornecedor,"
                      "valor_documento,valor_liquido,valor_glosa,data_emissao",
        })

        total = sum(float(r.get("valor_liquido") or 0) for r in rows)

        por_ano: dict[int, float] = {}
        por_tipo: dict[str, float] = {}
        forn: dict[str, dict] = {}

        for r in rows:
            ano = r.get("ano") or 0
            val = float(r.get("valor_liquido") or 0)
            tipo = r.get("tipo_despesa") or "N/A"
            nome_f = (r.get("nome_fornecedor") or "N/A").strip()
            cnpj_f = r.get("cnpj_cpf_fornecedor") or ""

            por_ano[ano] = por_ano.get(ano, 0.0) + val
            por_tipo[tipo] = por_tipo.get(tipo, 0.0) + val

            if nome_f != "N/A":
                if nome_f not in forn:
                    forn[nome_f] = {"nome": nome_f, "cnpj": cnpj_f, "total": 0.0, "n": 0}
                forn[nome_f]["total"] += val
                forn[nome_f]["n"] += 1

        top_forn = sorted(forn.values(), key=lambda x: -x["total"])[:20]

        return ResumoCota(
            total_transacoes=len(rows),
            total_valor=total,
            por_ano=dict(sorted(por_ano.items())),
            por_tipo_despesa=dict(sorted(por_tipo.items(), key=lambda x: -x[1])),
            top_fornecedores=top_forn,
            transacoes=rows[:500],
        )

    # ── 4. Financiamento de campanha ──────────────────────────────────────

    def _buscar_financiamento(self, ids: IdentificadorParlamentar) -> ResumoFinanciamento:
        rows = self._c.get_all("tse_v_financiadores_parlamentar", {
            "cpf_candidato": f"eq.{ids.cpf}",
        })

        total = sum(float(r.get("total_recebido") or 0) for r in rows)

        por_ano: dict[int, float] = {}
        por_tipo: dict[str, float] = {}
        doadores: dict[str, dict] = {}

        for r in rows:
            ano = r.get("ano_eleicao") or 0
            val = float(r.get("total_recebido") or 0)
            tipo = r.get("tipo_doador") or "N/A"
            nome_d = (r.get("nome_doador") or "N/A").strip()
            cpf_cnpj = r.get("cpf_cnpj_doador") or ""

            por_ano[ano] = por_ano.get(ano, 0.0) + val
            por_tipo[tipo] = por_tipo.get(tipo, 0.0) + val

            k = f"{nome_d}|{ano}"
            if k not in doadores:
                doadores[k] = {"nome": nome_d, "cpf_cnpj": cpf_cnpj,
                                "total": 0.0, "n": 0, "ano": ano}
            doadores[k]["total"] += val
            doadores[k]["n"] += r.get("n_transacoes") or 1

        top_d = sorted(doadores.values(), key=lambda x: -x["total"])[:20]

        return ResumoFinanciamento(
            total_transacoes=sum(r.get("n_transacoes") or 1 for r in rows),
            total_arrecadado=total,
            por_ano=dict(sorted(por_ano.items())),
            por_tipo_doador=dict(sorted(por_tipo.items(), key=lambda x: -x[1])),
            top_doadores=top_d,
        )

    # ── 5. Ponto de entrada ───────────────────────────────────────────────

    def gerar(self, cpf: str | None = None, nome: str | None = None) -> RelatorioParlamentar:
        """Gera o dossiê completo. Forneça cpf (preferido) ou nome."""
        ids = self._resolver_id(cpf=cpf, nome=nome)
        logger.info("Dossiê: %s (CPF %s) | CEAP id=%s | emendas cod=%s",
                    ids.nome_parlamentar, ids.cpf, ids.id_camara_ceap, ids.codigo_autor_emendas)

        emendas = self._buscar_emendas(ids)
        cota = self._buscar_cota(ids)
        financiamento = self._buscar_financiamento(ids)

        return RelatorioParlamentar(
            identificador=ids,
            emendas=emendas,
            cota=cota,
            financiamento=financiamento,
        )

    # ── 6. Renderização ───────────────────────────────────────────────────

    @staticmethod
    def imprimir(rel: RelatorioParlamentar) -> None:
        ids = rel.identificador
        sep = "=" * 65

        print(sep)
        print(f"DOSSIÊ: {ids.nome_parlamentar}  ({ids.partido}/{ids.uf})")
        print(f"CPF: {ids.cpf}  |  id_camara_api: {ids.id_camara_api}")
        print(f"id_ceap: {ids.id_camara_ceap}  |  cod_emenda: {ids.codigo_autor_emendas}")
        print(sep)

        # — Emendas
        e = rel.emendas
        print(f"\n{'─'*65}")
        print(f"EMENDAS PARLAMENTARES  ({e.total_transacoes} transações)")
        print(f"  Total pago: R$ {e.total_valor:,.2f}")
        print(f"  Por ano:   " + "  ".join(f"{a}: R${v:,.0f}" for a, v in e.por_ano.items()))
        if e.top_favorecidos:
            print(f"\n  Top favorecidos:")
            for i, f in enumerate(e.top_favorecidos[:10], 1):
                print(f"  {i:2}. {f['favorecido'][:45]:<45}  R$ {f['total']:>12,.2f}  ({f['n']}x)")

        # — Cota
        c = rel.cota
        print(f"\n{'─'*65}")
        print(f"COTA PARLAMENTAR (CEAP)  ({c.total_transacoes} transações)")
        print(f"  Total gasto: R$ {c.total_valor:,.2f}")
        if c.por_ano:
            print(f"  Por ano:    " + "  ".join(f"{a}: R${v:,.0f}" for a, v in c.por_ano.items()))
        if c.por_tipo_despesa:
            print(f"\n  Por tipo de despesa (top 8):")
            for tipo, val in list(c.por_tipo_despesa.items())[:8]:
                print(f"    {tipo[:50]:<50}  R$ {val:>10,.2f}")
        if c.top_fornecedores:
            print(f"\n  Top fornecedores:")
            for i, f in enumerate(c.top_fornecedores[:10], 1):
                print(f"  {i:2}. {f['nome'][:45]:<45}  R$ {f['total']:>10,.2f}  ({f['n']}x)")

        # — Financiamento
        fin = rel.financiamento
        print(f"\n{'─'*65}")
        print(f"FINANCIAMENTO DE CAMPANHA  ({fin.total_transacoes} doações)")
        print(f"  Total arrecadado: R$ {fin.total_arrecadado:,.2f}")
        if fin.por_ano:
            print(f"  Por ano:         " + "  ".join(f"{a}: R${v:,.0f}" for a, v in fin.por_ano.items()))
        if fin.por_tipo_doador:
            print(f"\n  Por tipo de doador:")
            for tipo, val in fin.por_tipo_doador.items():
                print(f"    {tipo[:50]:<50}  R$ {val:>10,.2f}")
        if fin.top_doadores:
            print(f"\n  Top doadores:")
            for i, d in enumerate(fin.top_doadores[:10], 1):
                print(f"  {i:2}. {d['nome'][:45]:<45}  R$ {d['total']:>10,.2f}")

        print(f"\n{sep}\n")

    @staticmethod
    def para_dict(rel: RelatorioParlamentar) -> dict:
        """Serializa o relatório completo como dicionário (JSON-safe com default=str)."""
        ids = rel.identificador
        return {
            "parlamentar": {
                "cpf": ids.cpf,
                "nome": ids.nome,
                "nome_parlamentar": ids.nome_parlamentar,
                "partido": ids.partido,
                "uf": ids.uf,
                "id_camara_api": ids.id_camara_api,
                "id_camara_ceap": ids.id_camara_ceap,
                "codigo_autor_emendas": ids.codigo_autor_emendas,
            },
            "emendas": {
                "total_transacoes": rel.emendas.total_transacoes,
                "total_valor": rel.emendas.total_valor,
                "por_ano": rel.emendas.por_ano,
                "por_tipo": rel.emendas.por_tipo,
                "por_uf": rel.emendas.por_uf,
                "top_favorecidos": rel.emendas.top_favorecidos,
                "transacoes": rel.emendas.transacoes,
            },
            "cota": {
                "total_transacoes": rel.cota.total_transacoes,
                "total_valor": rel.cota.total_valor,
                "por_ano": rel.cota.por_ano,
                "por_tipo_despesa": rel.cota.por_tipo_despesa,
                "top_fornecedores": rel.cota.top_fornecedores,
                "transacoes": rel.cota.transacoes,
            },
            "financiamento": {
                "total_transacoes": rel.financiamento.total_transacoes,
                "total_arrecadado": rel.financiamento.total_arrecadado,
                "por_ano": rel.financiamento.por_ano,
                "por_tipo_doador": rel.financiamento.por_tipo_doador,
                "top_doadores": rel.financiamento.top_doadores,
            },
        }
