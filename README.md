# The Brasilia Insider — ingestão de assembleias estaduais

Framework de ingestão das 27 ALEs + CLDF. Coleta **atividade legislativa**
(deputados, proposições, votações) e grava no Supabase em schema canônico
multi-casa. Gastos/verba indenizatória são domínio de outro pipeline (tabelas
`almg_*`, `alesp_despesas_*`, etc.).

## Estrutura

```
ingestao/
  base_connector.py      # interface comum (HTTP, retry, throttle, health check)
  models.py              # dataclasses canônicas (Deputado, Proposicao, Votacao)
  persistence.py         # writer Supabase via PostgREST (só requests)
  scheduler.py           # CLI de orquestração
  connectors/
    almg.py, alep.py, alesp.py   # conectores implementados
    _stubs.py            # 24 casas restantes (stubs registrados)
db/migrations/           # schema canônico (tabelas ale_*) + view de reconciliação
```

## Estado dos conectores

| Casa | Deputados | Proposições | Votações |
|------|-----------|-------------|----------|
| ALMG (MG) | ✅ | ✅ | mapeado, não implementado (plenário/reuniões) |
| ALEP (PR) | ✅ | ✅ | ✅ (não validado contra API live) |
| ALESP (SP) | ✅ | ✅ | ✅ (comissões permanentes) |
| demais (24) | stub | stub | stub |

## Banco (Supabase)

Schema canônico prefixado `ale_` (namespacing deliberado — `parlamentares`,
`proposicoes` etc. nuas já são do pipeline federal):
`ale_casas`, `ale_parlamentares`, `ale_proposicoes`, `ale_votacoes`,
`ale_votos`, `ale_ingest_runs` + view `ale_parlamentares_reconciliado`
(liga deputados de atividade ↔ gastos por `Matricula`).

Migrations em `db/migrations/`. Aplicadas em produção (projeto
`redggdtakzmsabwvjzhb`) em 2026-05-29.

## Uso

```bash
pip install -r ingestao/requirements.txt

# tudo, últimos 7 dias
python -m ingestao.scheduler --dias 7

# casa específica, fetch-only (não grava)
python -m ingestao.scheduler --assembly alesp --no-persist

# cadência separada: leve (frequente) vs. pesado (raro)
python -m ingestao.scheduler --entidades deputados proposicoes   # frequente
python -m ingestao.scheduler --entidades votacoes --dias 14      # raro/pesado

# conectividade
python -m ingestao.scheduler --health-check
```

Persistência ativa quando `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` estão no
ambiente; senão roda fetch-only.

## Agendamento (GitHub Actions)

Dois workflows com cadência separada por causa do custo das votações
(a ALESP só tem dump bulk; `get_votacoes` baixa ~70MB/execução):

- `ingest-frequente.yml` — deputados + proposições, **diário** (08:00 UTC).
- `ingest-votacoes.yml` — votações, **semanal** (segundas, 06:00 UTC).

Secrets necessários no repositório: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
