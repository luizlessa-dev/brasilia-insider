"""
Camada de persistência — The Brasilia Insider

Grava os modelos canônicos no Supabase via PostgREST (REST), usando apenas
`requests` para não adicionar dependências. Upsert idempotente: as PKs vêm
da fonte (ids prefixados pela casa), então reexecutar o ingester atualiza
em vez de duplicar.

Env vars (mesmas do pipeline TS em dados-civicos):
  SUPABASE_URL                 — ex: https://xxxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY    — chave service_role (escrita)

Se as env vars não estiverem presentes, o scheduler roda em modo fetch-only.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Iterable

import requests

from .models import Deputado, Proposicao, Votacao

logger = logging.getLogger("persistence")

# PostgREST aceita lotes grandes, mas mantemos chunks moderados pra payloads
# previsíveis e mensagens de erro legíveis.
CHUNK = 500


class PersistenceError(Exception):
    """Falha ao gravar no Supabase."""


def _jsonable(value: Any) -> Any:
    """Converte tipos não-serializáveis (date/datetime) recursivamente."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class SupabaseWriter:
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
                "Faltando SUPABASE_URL e/ou SUPABASE_SERVICE_ROLE_KEY no ambiente."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_env(cls) -> "SupabaseWriter | None":
        """Retorna um writer se o ambiente estiver configurado, senão None."""
        try:
            return cls()
        except PersistenceError as e:
            logger.warning("Persistência desativada — %s", e)
            return None

    # ── Upsert genérico ───────────────────────────────────────────────────
    def _upsert(self, table: str, rows: list[dict], on_conflict: str) -> int:
        rows = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
        total = 0
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i:i + CHUNK]
            resp = self.session.post(
                f"{self.url}/rest/v1/{table}",
                params={"on_conflict": on_conflict},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json=chunk,
                timeout=60,
            )
            if resp.status_code >= 300:
                raise PersistenceError(
                    f"upsert {table} falhou: HTTP {resp.status_code} — {resp.text[:300]}"
                )
            total += len(chunk)
        return total

    # ── Casas (seed antes de gravar filhos por causa da FK casa_id) ───────
    def upsert_casa(self, connector) -> None:
        row = {
            "id": connector.assembly_id,
            "nome": connector.assembly_name,
            "uf": connector.uf,
            "base_url": getattr(connector, "base_url", None),
            "api_url": getattr(connector, "api_url", None),
            "updated_at": datetime.utcnow().isoformat(),
        }
        self._upsert("casas", [row], on_conflict="id")

    # ── Parlamentares ─────────────────────────────────────────────────────
    def upsert_deputados(self, deps: Iterable[Deputado]) -> int:
        rows = []
        for d in deps:
            rows.append({
                "id": d.id,
                "casa_id": d.assembly_id,
                "nome": d.nome,
                "slug": d.slug,
                "partido": d.partido or None,
                "uf": d.uf or None,
                "mandato_inicio": d.mandato_inicio,
                "mandato_fim": d.mandato_fim,
                "foto_url": d.foto_url,
                "email": d.email,
                "telefone": d.telefone,
                "raw": d.raw or None,
                "updated_at": datetime.utcnow().isoformat(),
            })
        return self._upsert("parlamentares", rows, on_conflict="id") if rows else 0

    # ── Proposições ───────────────────────────────────────────────────────
    def upsert_proposicoes(self, props: Iterable[Proposicao]) -> int:
        rows = []
        for p in props:
            rows.append({
                "id": p.id,
                "casa_id": p.assembly_id,
                "numero": p.numero or None,
                "ano": p.ano,
                "tipo": p.tipo or None,
                "ementa": p.ementa or None,
                "autor": p.autor,
                "autor_id": p.autor_id,
                "data_apresentacao": p.data_apresentacao,
                "situacao": p.situacao,
                "regime": p.regime,
                "url": p.url,
                "assuntos": p.assuntos or None,
                "raw": p.raw or None,
                "updated_at": datetime.utcnow().isoformat(),
            })
        return self._upsert("proposicoes", rows, on_conflict="id") if rows else 0

    # ── Votações (+ votos nominais) ───────────────────────────────────────
    def upsert_votacoes(self, vots: Iterable[Votacao]) -> int:
        vot_rows = []
        voto_rows = []
        seen_votos: set[tuple[str, str]] = set()
        for v in vots:
            vot_rows.append({
                "id": v.id,
                "casa_id": v.assembly_id,
                "proposicao_id": v.proposicao_id or None,
                "data": v.data,
                "hora": v.hora,
                "resultado": v.resultado,
                "votos_sim": v.votos_sim,
                "votos_nao": v.votos_nao,
                "votos_abstencao": v.votos_abstencao,
                "votos_ausente": v.votos_ausente,
                "raw": v.raw or None,
                "updated_at": datetime.utcnow().isoformat(),
            })
            for det in v.detalhes:
                key = (v.id, det.deputado_id)
                if not det.deputado_id or key in seen_votos:
                    continue
                seen_votos.add(key)
                voto_rows.append({
                    "votacao_id": v.id,
                    "deputado_id": det.deputado_id,
                    "deputado_nome": det.deputado_nome,
                    "voto": det.voto or None,
                    "partido": det.partido,
                })
        n = self._upsert("votacoes", vot_rows, on_conflict="id") if vot_rows else 0
        if voto_rows:
            self._upsert("votos", voto_rows, on_conflict="votacao_id,deputado_id")
        return n

    # ── Log de execução ───────────────────────────────────────────────────
    def start_run(self, casa_id: str, data_inicio: date, data_fim: date) -> str | None:
        resp = self.session.post(
            f"{self.url}/rest/v1/ingest_runs",
            headers={"Prefer": "return=representation"},
            json=[{
                "casa_id": casa_id,
                "status": "running",
                "data_inicio": data_inicio.isoformat(),
                "data_fim": data_fim.isoformat(),
            }],
            timeout=30,
        )
        if resp.status_code >= 300:
            logger.warning("start_run falhou: %s", resp.text[:200])
            return None
        try:
            return resp.json()[0]["id"]
        except (IndexError, KeyError, ValueError):
            return None

    def finish_run(self, run_id: str | None, status: str, counts: dict, erro: str | None = None) -> None:
        if not run_id:
            return
        self.session.patch(
            f"{self.url}/rest/v1/ingest_runs",
            params={"id": f"eq.{run_id}"},
            headers={"Prefer": "return=minimal"},
            json={
                "status": status,
                "finished_at": datetime.utcnow().isoformat(),
                "n_deputados": counts.get("deputados", 0),
                "n_proposicoes": counts.get("proposicoes", 0),
                "n_votacoes": counts.get("votacoes", 0),
                "erro": erro,
            },
            timeout=30,
        )
