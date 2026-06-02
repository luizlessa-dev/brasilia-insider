"""Camada de persistência TSE — The Brasilia Insider."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Iterable, Union

import requests

from typing import Iterator

from .connector import Candidato, Despesa, Receita

logger = logging.getLogger("tse.persistence")

CHUNK = 200


class PersistenceError(Exception):
    pass


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


class TSEWriter:
    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = (
            key
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("INTERNAL_SUPABASE_SERVICE_ROLE_KEY")
            or ""
        )
        if not self.url or not self.key:
            raise PersistenceError(
                "Faltando SUPABASE_URL e/ou SUPABASE_SERVICE_ROLE_KEY."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_env(cls) -> "TSEWriter | None":
        try:
            return cls()
        except PersistenceError as e:
            logger.warning("TSEWriter desativado — %s", e)
            return None

    def _upsert(self, table: str, rows: list[dict], on_conflict: str) -> int:
        rows = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
        keys = [k.strip() for k in on_conflict.split(",")]
        deduped: dict[tuple, dict] = {}
        for r in rows:
            deduped[tuple(r.get(k) for k in keys)] = r
        rows = list(deduped.values())
        total = 0
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i: i + CHUNK]
            resp = self.session.post(
                f"{self.url}/rest/v1/{table}",
                params={"on_conflict": on_conflict},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json=chunk,
                timeout=60,
            )
            if resp.status_code >= 300:
                raise PersistenceError(
                    f"upsert {table}: HTTP {resp.status_code} — {resp.text[:300]}"
                )
            total += len(chunk)
        return total

    def _bulk_insert(self, table: str, rows: list[dict]) -> int:
        """Insere sem dedup — usar após _delete_year para evitar duplicatas."""
        rows = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
        total = 0
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i: i + CHUNK]
            resp = self.session.post(
                f"{self.url}/rest/v1/{table}",
                headers={"Prefer": "return=minimal"},
                json=chunk,
                timeout=180,
            )
            if resp.status_code >= 300:
                raise PersistenceError(
                    f"insert {table}: HTTP {resp.status_code} — {resp.text[:300]}"
                )
            total += len(chunk)
        return total

    def _delete_year(self, table: str, ano: int) -> None:
        """Deleta todas as linhas de um ano eleitoral antes do reload."""
        resp = self.session.delete(
            f"{self.url}/rest/v1/{table}",
            params={"ano_eleicao": f"eq.{ano}"},
            headers={"Prefer": "return=minimal"},
            timeout=60,
        )
        if resp.status_code >= 300:
            raise PersistenceError(
                f"delete {table} ano={ano}: HTTP {resp.status_code} — {resp.text[:200]}"
            )
        logger.info("%s: deletadas linhas do ano %d antes do reload", table, ano)

    # ── Candidatos ────────────────────────────────────────────────────────

    def upsert_candidatos(self, candidatos: Iterable[Candidato]) -> int:
        rows = []
        for c in candidatos:
            rows.append({
                "id": c.id,
                "ano_eleicao": c.ano_eleicao,
                "sq_candidato": c.sq_candidato,
                "cpf": c.cpf,
                "nome": c.nome,
                "nome_urna": c.nome_urna,
                "data_nascimento": c.data_nascimento,
                "genero": c.genero,
                "cor_raca": c.cor_raca,
                "grau_instrucao": c.grau_instrucao,
                "ocupacao": c.ocupacao,
                "estado_civil": c.estado_civil,
                "email": c.email,
                "cd_cargo": c.cd_cargo,
                "cargo": c.cargo,
                "uf": c.uf,
                "municipio_nascimento": c.municipio_nascimento,
                "nr_partido": c.nr_partido,
                "sigla_partido": c.sigla_partido,
                "nome_partido": c.nome_partido,
                "situacao_candidatura": c.situacao_candidatura,
                "situacao_turno": c.situacao_turno,
                "reeleicao": c.reeleicao,
                "limite_despesa": c.limite_despesa,
                "updated_at": datetime.utcnow().isoformat(),
            })
        if not rows:
            return 0
        n = self._upsert("tse_candidatos", rows, on_conflict="id")
        logger.info("TSE candidatos: %d gravados/atualizados", n)
        return n

    # ── Receitas ──────────────────────────────────────────────────────────
    # Estratégia: delete-then-stream-insert por ano_eleicao.
    # O ZIP do TSE é um dump estático pós-eleição — recarregar o ano inteiro
    # é mais simples e confiável do que tentar dedup por recibo (índice parcial
    # não funciona com PostgREST ignore-duplicates: PostgreSQL erro 42P10).
    # Aceita Iterable OU Iterator (generator) para suportar streaming de ZIPs grandes.

    def upsert_receitas(self, receitas: Union[Iterable[Receita], "Iterator[Receita]"],
                        ano: int) -> int:
        self._delete_year("tse_receitas", ano)
        total = 0
        batch: list[dict] = []
        for r in receitas:
            batch.append({
                "ano_eleicao": r.ano_eleicao,
                "numero_recibo": r.numero_recibo,
                "cpf_candidato": r.cpf_candidato,
                "nome_candidato": r.nome_candidato,
                "cargo": r.cargo,
                "sigla_partido": r.sigla_partido,
                "uf": r.uf,
                "cpf_cnpj_doador": r.cpf_cnpj_doador,
                "nome_doador": r.nome_doador,
                "tipo_doador": r.tipo_doador,
                "setor_economico_doador": r.setor_economico_doador,
                "cpf_cnpj_doador_originario": r.cpf_cnpj_doador_originario,
                "nome_doador_originario": r.nome_doador_originario,
                "natureza_receita": r.natureza_receita,
                "origem_receita": r.origem_receita,
                "especie_recurso": r.especie_recurso,
                "fonte_recurso": r.fonte_recurso,
                "valor": r.valor,
                "data_receita": r.data_receita,
                "data_prestacao_contas": r.data_prestacao_contas,
            })
            if len(batch) >= CHUNK:
                self._bulk_insert("tse_receitas", batch)
                total += len(batch)
                batch = []
        if batch:
            self._bulk_insert("tse_receitas", batch)
            total += len(batch)
        logger.info("TSE receitas %d: %d gravadas", ano, total)
        return total

    # ── Despesas ──────────────────────────────────────────────────────────
    # Mesmo padrão streaming das receitas.

    def upsert_despesas(self, despesas: Union[Iterable[Despesa], "Iterator[Despesa]"],
                        ano: int) -> int:
        self._delete_year("tse_despesas", ano)
        total = 0
        batch: list[dict] = []
        for d in despesas:
            batch.append({
                "ano_eleicao": d.ano_eleicao,
                "numero_documento": d.numero_documento,
                "cpf_candidato": d.cpf_candidato,
                "nome_candidato": d.nome_candidato,
                "cargo": d.cargo,
                "sigla_partido": d.sigla_partido,
                "uf": d.uf,
                "cpf_cnpj_fornecedor": d.cpf_cnpj_fornecedor,
                "nome_fornecedor": d.nome_fornecedor,
                "tipo_despesa": d.tipo_despesa,
                "descricao_despesa": d.descricao_despesa,
                "origem_despesa": d.origem_despesa,
                "especie_recurso": d.especie_recurso,
                "fonte_recurso": d.fonte_recurso,
                "valor_despesa": d.valor_despesa,
                "valor_prestado": d.valor_prestado,
                "data_despesa": d.data_despesa,
            })
            if len(batch) >= CHUNK:
                self._bulk_insert("tse_despesas", batch)
                total += len(batch)
                batch = []
        if batch:
            self._bulk_insert("tse_despesas", batch)
            total += len(batch)
        logger.info("TSE despesas %d: %d gravadas", ano, total)
        return total

    # ── Log ───────────────────────────────────────────────────────────────

    def start_log(self, dataset: str) -> int | None:
        resp = self.session.post(
            f"{self.url}/rest/v1/tse_ingest_log",
            headers={"Prefer": "return=representation"},
            json=[{"dataset": dataset, "status": "running"}],
            timeout=30,
        )
        if resp.status_code >= 300:
            logger.warning("start_log falhou: %s", resp.text[:200])
            return None
        try:
            return resp.json()[0]["id"]
        except (IndexError, KeyError, ValueError):
            return None

    def finish_log(
        self,
        log_id: int | None,
        status: str,
        n_processados: int = 0,
        n_novos: int = 0,
        erro: str | None = None,
    ) -> None:
        if not log_id:
            return
        self.session.patch(
            f"{self.url}/rest/v1/tse_ingest_log",
            params={"id": f"eq.{log_id}"},
            headers={"Prefer": "return=minimal"},
            json={
                "finished_at": datetime.utcnow().isoformat(),
                "status": status,
                "n_processados": n_processados,
                "n_novos": n_novos,
                "erro": erro,
            },
            timeout=30,
        )

    def cleanup_stuck_logs(self) -> int:
        """Marca como 'interrompido' entradas 'running' sem finished_at (travadas)."""
        resp = self.session.patch(
            f"{self.url}/rest/v1/tse_ingest_log",
            params={
                "status": "eq.running",
                "finished_at": "is.null",
            },
            headers={"Prefer": "return=minimal"},
            json={
                "status": "interrompido",
                "finished_at": datetime.utcnow().isoformat(),
                "erro": "marcado como interrompido na próxima execução",
            },
            timeout=30,
        )
        if resp.status_code >= 300:
            logger.warning("cleanup_stuck_logs falhou: %s", resp.text[:200])
            return 0
        logger.info("Logs travados limpos.")
        return 1
