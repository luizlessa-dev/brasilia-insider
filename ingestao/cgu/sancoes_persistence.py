"""Persistência de sanções CEIS/CNEP — The Brasilia Insider."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Iterator

import requests

from .sancoes_connector import Sancao

logger = logging.getLogger("cgu.sancoes.persistence")

CHUNK = 500


class PersistenceError(Exception):
    pass


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


class SancoesWriter:
    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = (
            key
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("INTERNAL_SUPABASE_SERVICE_ROLE_KEY")
            or ""
        )
        if not self.url or not self.key:
            raise PersistenceError("Faltando SUPABASE_URL e/ou SUPABASE_SERVICE_ROLE_KEY.")
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_env(cls) -> "SancoesWriter | None":
        try:
            return cls()
        except PersistenceError as e:
            logger.warning("SancoesWriter desativado — %s", e)
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

    def upsert_sancoes(self, sancoes: Iterator[Sancao]) -> int:
        """
        Upsert streaming de sanções na tabela `sancoes`.
        PK: id (int da API) — idempotente, seguro re-rodar.
        """
        total = 0
        batch: list[dict] = []
        for s in sancoes:
            batch.append({
                "id":                   s.id,
                "cadastro":             s.cadastro,
                "cpf_cnpj":             s.cpf_cnpj,
                "cpf_cnpj_formatado":   s.cpf_cnpj_formatado,
                "tipo_pessoa":          s.tipo_pessoa,
                "nome":                 s.nome,
                "razao_social":         s.razao_social,
                "nome_fantasia":        s.nome_fantasia,
                "tipo_sancao":          s.tipo_sancao,
                "descricao_sancao":     s.descricao_sancao,
                "data_inicio":          s.data_inicio,
                "data_fim":             s.data_fim,
                "data_publicacao":      s.data_publicacao,
                "data_transitado":      s.data_transitado,
                "data_referencia":      s.data_referencia,
                "orgao_nome":           s.orgao_nome,
                "orgao_uf":             s.orgao_uf,
                "orgao_poder":          s.orgao_poder,
                "orgao_esfera":         s.orgao_esfera,
                "numero_processo":      s.numero_processo,
                "fundamentacao":        s.fundamentacao,
                "valor_multa":          s.valor_multa,
                "abrangencia":          s.abrangencia,
                "informacoes_adicionais": s.informacoes_adicionais,
                "link_publicacao":      s.link_publicacao,
                "updated_at":           datetime.utcnow().isoformat(),
            })
            if len(batch) >= CHUNK:
                self._upsert("sancoes", batch, on_conflict="id")
                total += len(batch)
                batch = []
        if batch:
            self._upsert("sancoes", batch, on_conflict="id")
            total += len(batch)
        logger.info("Sanções: %d upsertadas", total)
        return total

    # ── Log ───────────────────────────────────────────────────────────────

    def start_log(self, dataset: str) -> int | None:
        resp = self.session.post(
            f"{self.url}/rest/v1/sancoes_ingest_log",
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
        n_novos: int = 0,
        erro: str | None = None,
    ) -> None:
        if not log_id:
            return
        self.session.patch(
            f"{self.url}/rest/v1/sancoes_ingest_log",
            params={"id": f"eq.{log_id}"},
            headers={"Prefer": "return=minimal"},
            json={
                "finished_at": datetime.utcnow().isoformat(),
                "status": status,
                "n_novos": n_novos,
                "erro": erro,
            },
            timeout=30,
        )
