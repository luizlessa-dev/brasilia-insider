# SIAFI — Ingestão de execução orçamentária federal

Módulo do BR Insider que ingere os dados públicos do SIAFI via Portal da Transparência (CGU) e materializa um data lake consultável.

**Status:** operacional (validado 2026-06-02). ADR detalhado em [`docs/adr/001-siafi-ingestao.md`](../../../docs/adr/001-siafi-ingestao.md).

---

## Visão geral

Dois streams complementares:

| Stream | Fonte | Granularidade | Saída bronze | Saída silver |
|---|---|---|---|---|
| **A — Execução mensal** | `/despesas-execucao/{YYYYMM}` | Agregado por (UG × programa × ação × elemento × emenda × subtítulo), valores mensais | Parquet 1 mês/arquivo | `siafi_execucao_mensal` |
| **B — Snapshot diário** | `/despesas/{YYYYMMDD}` | Documentos individuais (empenho, item, OB, favorecidos finais) | 6 Parquets/snapshot | `siafi_empenho`, `siafi_item_empenho`, `siafi_liquidacao`, `siafi_pagamento`, `siafi_pagamento_empenho`, `siafi_pagamento_favorecido_final` + dim `siafi_fornecedor` |

Capturamos **1 snapshot por mês** (último dia útil) — o stream B é acumulativo, então múltiplos snapshots intra-mês seriam redundantes.

---

## Quickstart

### 1. Pré-requisitos

```bash
pip install -r ingestao/requirements.txt
```

Dependências novas adicionadas pelo módulo: `pyarrow`, `boto3`, `duckdb`.

### 2. Modo dev (sem credenciais)

Grava em `/tmp/brinsider-lake/` para inspeção via DuckDB:

```bash
# Stream A: 1 mês de execução agregada
python -m ingestao.lake.siafi.run --execucao-mensal 2026-04

# Stream B: snapshot do último dia útil de um mês
python -m ingestao.lake.siafi.run --snapshot-last-business-day 2026-04

# Stream B: snapshot de uma data específica
python -m ingestao.lake.siafi.run --snapshot 2026-04-30
```

Validação local com DuckDB:

```bash
duckdb -c "
SELECT cod_orgao_superior, COUNT(*) FROM read_parquet(
  '/tmp/brinsider-lake/siafi/snapshot/snapshot_date=2026-04-30/empenho.parquet'
) GROUP BY 1 ORDER BY 2 DESC LIMIT 5;
"
```

### 3. Modo produção (R2 + Supabase)

Variáveis no `.env`:

```dotenv
# Bronze layer (R2)
R2_ACCOUNT_ID=...
R2_BUCKET=brinsider-lake
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...

# Silver layer (Supabase)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
```

Carga incremental bronze → silver:

```bash
python -m ingestao.lake.siafi.silver --execucao-mensal 2026-04
python -m ingestao.lake.siafi.silver --snapshot 2026-04-30
```

### 4. Cron mensal (automação)

```bash
# Modo padrão: ingere mês anterior ao corrente
python -m ingestao.lake.siafi.cron --modo incremental

# Revalida últimos 24 meses (detecta republicação retroativa da CGU)
python -m ingestao.lake.siafi.cron --modo revalidate --janela-revalidacao 24

# Backfill manual de 1 mês específico
python -m ingestao.lake.siafi.cron --modo backfill --competencia 2014-01
```

Acionamento automático via GitHub Actions: workflow [`ingest-siafi-mensal.yml`](../../../.github/workflows/ingest-siafi-mensal.yml) roda dia 20 de cada mês às 09:00 UTC.

---

## Schema silver

8 tabelas em `public.siafi_*` (DDL completo em [`db/migrations/0003_siafi_canonical_schema.sql`](../../../db/migrations/0003_siafi_canonical_schema.sql)):

```
siafi_fornecedor                      [dim]   CNPJ/CPF/especial deduplicado
siafi_execucao_mensal                 [stream A]  agregado mensal
siafi_empenho                         [stream B]  cabeçalho de empenho
siafi_item_empenho                    [stream B]  linhas detalhadas (FK→empenho)
siafi_liquidacao                      [stream B]
siafi_pagamento                       [stream B]
siafi_pagamento_empenho               [stream B]  junction N:N
siafi_pagamento_favorecido_final      [stream B]  favorecido quando OB→lista
siafi_ingestao_log                    [auxiliar]  log do cron
```

**Convenções:**
- Encoding silver: UTF-8 (origem é ISO-8859-1, conversão na ingestão).
- Valores monetários: `NUMERIC(20,2)` (preserva centavos sem perda de float).
- PK natural da fonte (`id_empenho`, `codigo_pagamento`, etc.).
- FK pra `siafi_fornecedor` é `DEFERRABLE INITIALLY DEFERRED` (permite ordem livre de UPSERT).
- RLS: leitura pública (dado público), escrita só `service_role`.
- GIN full-text português em `empenho.observacao` e `item_empenho.descricao`.

---

## Volumes (validados 2026-06-02)

| Item | Tamanho |
|---|---|
| 1 mês execução agregada (CSV) | ~50 MB |
| 1 mês execução agregada (Parquet snappy) | ~3 MB |
| 1 snapshot diário (CSV total 11 arquivos) | ~80 MB |
| 1 snapshot diário (Parquet snappy total) | ~11 MB |
| **Histórico completo 2014-01 → 2026-05 estimado** | **~2 GB** (R2 free tier cobre) |
| Tempo de ingestão por mês | ~3 s |
| Tempo de backfill completo estimado | ~10 min |

---

## Operação

### Lag de publicação

Portal da Transparência publica execução com **lag de ~17 dias** (validado: arquivos de abril/2025 publicados em 27/maio/2025). O cron roda dia 20 do mês seguinte pra ter folga.

### Republicação retroativa

**Importante:** CGU republica histórico ocasionalmente (exemplo registrado no ADR: arquivo de jan/2015 foi atualizado em 30/mar/2026). Solução: o modo `revalidate` faz HEAD nos últimos N meses e recarrega os que mudaram. Estado é mantido em `siafi_ingestao_log`.

### WAF bypass

Portal da Transparência usa AWS WAF que bloqueia clientes não-navegadores. Único User-Agent validado em produção: **Chrome 92** (constante em `client.py`). Mudar UA exige revalidação contra o WAF — se começar a falhar com 405 + `x-amzn-waf-action: captcha`, é provavelmente bloqueio de UA.

### Troubleshooting

| Sintoma | Causa provável | Solução |
|---|---|---|
| HTTP 405 com WAF challenge | User-Agent rejeitado | Confirmar `USER_AGENT` em `client.py` |
| HTTP 404 em mês recente | Mês ainda não publicado (lag) | Aguardar dia 20+ do mês seguinte |
| UPSERT erro "foreign key" | Fornecedor não inserido antes | Validar ordem em `silver_snapshot()` |
| Cron `incremental` skipa tudo | `siafi_ingestao_log.source_last_modified` já = remoto | Rodar `--modo revalidate` ou `--modo backfill --competencia ...` |

### Custos estimados (mensal)

| Componente | Custo |
|---|---|
| Cloudflare R2 (<10 GB) | R$ 0 (free tier) |
| GitHub Actions cron (1 run/mês × ~30 min) | R$ 0 (free tier 2000 min/mês) |
| Supabase Postgres (silver, <500 MB) | R$ 0 (free tier) |
| **Total** | **R$ 0** |

A configuração atual cabe no free tier de tudo. Crescimento futuro: passar do free tier R2 (10 GB) demoraria ~5 anos no ritmo atual.

---

## Queries de exemplo

Veja [`queries/siafi/`](../../../queries/siafi/) — 3 queries-trofeu que demonstram cruzamentos impossíveis (ou impraticáveis) na UI oficial:

1. **01_top_fornecedores_mes.sql** — Top 20 recebedores por valor pago, com classificação PJ/PF/EXTERIOR/ESPECIAL.
2. **02_observacao_full_text.sql** — Busca textual em justificativas de empenho (dispensa, emergencial, inexigibilidade).
3. **03_emenda_para_caixa.sql** — Caminho do dinheiro: emenda parlamentar → empenho → OB → favorecido final.

Latências observadas localmente (DuckDB sobre Parquet, snapshot de 30/abr/2025):

| Query | Tempo |
|---|---|
| 01 — Top fornecedores | 12 ms |
| 02 — Full-text em observação | ~30 ms (ILIKE) |
| 03 — Emenda → caixa (4 tabelas) | 37 ms |

Comparativo com a UI oficial: as mesmas perguntas levam **segundos a minutos** lá. Speedup: ~100–1000×.

---

## Referências

- [ADR 001 — Ingestão SIAFI](../../../docs/adr/001-siafi-ingestao.md)
- [Migration 0003 — Schema canônico siafi_*](../../../db/migrations/0003_siafi_canonical_schema.sql)
- [Migration 0004 — Log de ingestão](../../../db/migrations/0004_siafi_ingestao_log.sql)
- Portal da Transparência: <https://portaldatransparencia.gov.br/download-de-dados>
- Bucket S3 público (origem): `dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/`
