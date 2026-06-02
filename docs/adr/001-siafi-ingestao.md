# ADR 001 — Ingestão SIAFI (Portal da Transparência) para o BR Insider

**Status:** aceito (2026-06-02)
**Autor:** Luiz Lessa
**Contexto editorial:** TF/BR Insider — dados de execução orçamentária federal pra produtos jornalísticos (Bastidores BR, Emendas Pix, ecossistema 12 produtos).

---

## 1. Contexto

O SIAFI (`siafi.tesouro.gov.br`) é um sistema operacional do Tesouro — acesso restrito a servidores com ICP-Brasil. **Não é uma fonte ingerível diretamente.**

A camada pública dos dados do SIAFI é distribuída pela CGU via **Portal da Transparência** em dois canais:

| Canal | Path | Granularidade | Histórico desde |
|---|---|---|---|
| **Execução mensal agregada** | `/download-de-dados/despesas-execucao/{YYYYMM}` | Linha por (UG × programa × ação × elemento × fonte × emenda), valores mensais | 2014-01 |
| **Snapshot diário operacional** | `/download-de-dados/despesas/{YYYYMMDD}` | Documentos individuais (empenho, item, liquidação, OB, favorecidos) | 2013-03-31 |

Ambos redirecionam (HTTP 302) para `dadosabertos-download.cgu.gov.br` (CloudFront → S3 `sa-east-1`).

**Observação crítica:** o portal protege as URLs com AWS WAF/captcha. **Funciona apenas com User-Agent específico** (Chrome 92 testado e validado). Outros UAs caem em desafio CAPTCHA (HTTP 405).

---

## 2. Decisão

### 2.1 Dois streams complementares

**Stream A — Séries históricas agregadas (`siafi_execucao_mensal`)**
- Fonte: `/despesas-execucao/{YYYYMM}`
- Frequência: mensal, com revalidação anti-republicação (ver §2.5)
- Volume validado: ~6 MB ZIP / ~50 MB CSV por mês. 149 meses (2014-01 → 2026-05) ≈ 900 MB ZIP / 7,5 GB CSV / ~2 GB Parquet snappy.
- Uso: séries temporais longas, comparativos ano-a-ano, dashboards.

**Stream B — Snapshot operacional (tabelas detalhadas)**
- Fonte: `/despesas/{YYYYMMDD}` — pegamos **último dia útil de cada mês** (1 snapshot/mês).
- Frequência: mensal.
- Volume validado: ~9 MB ZIP / ~80 MB CSV por snapshot (11 arquivos internos).
- Uso: identificar empenhos individuais, fornecedores, observações textuais, vínculo emenda↔empenho↔pagamento.

A escolha de só capturar o último dia útil é deliberada: o snapshot diário é **acumulativo** (contém o estado vigente daquele dia, não o delta do dia). Múltiplos snapshots intra-mês são quase 100% redundantes — capturamos um por mês como ponto de auditoria.

### 2.2 Arquitetura em três camadas

```
Portal Transparência (CGU CloudFront)
       │  HTTPS, UA=Chrome/92.0.4515.93
       ▼
┌─────────────────────────────────────────────┐
│ BRONZE (R2, Parquet snappy, append-only)    │
│   siafi/execucao-mensal/competencia=YYYY-MM │
│   siafi/snapshot/snapshot_date=YYYY-MM-DD/  │
│     empenho.parquet                         │
│     item_empenho.parquet                    │
│     liquidacao.parquet                      │
│     pagamento.parquet                       │
│     pagamento_empenhos_impactados.parquet   │
│     pagamento_favorecidos_finais.parquet    │
└──────────────┬──────────────────────────────┘
               │  carga incremental (dbt-like)
               ▼
┌─────────────────────────────────────────────┐
│ SILVER (Supabase Postgres, schema siafi_*) │
│   siafi_execucao_mensal                     │
│   siafi_empenho                             │
│   siafi_item_empenho                        │
│   siafi_liquidacao                          │
│   siafi_pagamento                           │
│   siafi_pagamento_empenho   (junction N:N)  │
│   siafi_fornecedor          (dim CNPJ/CPF)  │
└──────────────┬──────────────────────────────┘
               │  views/materialized views
               ▼
┌─────────────────────────────────────────────┐
│ GOLD (Supabase, views/MV temáticas)         │
│   v_top_fornecedores_ano                    │
│   v_emenda_pix_empenho_pagamento            │
│   v_pessoal_por_orgao                       │
│   ...                                       │
└─────────────────────────────────────────────┘
```

### 2.3 Tabelas silver (DDL resumido)

**`siafi_execucao_mensal`** — 46 colunas, PK = `(competencia, ug, programa, acao, plano_orcamentario, elemento_despesa, fonte_recurso, autor_emenda)`. Métricas: valor_empenhado, valor_liquidado, valor_pago, restos_pagar_*.

**`siafi_empenho`** — PK = `id_empenho` (canônico do portal, ex: `564324296`). Campos-chave: `codigo_empenho` (`257001000012025NE447249`), `data_emissao`, `cnpj_favorecido` → `siafi_fornecedor.cnpj_cpf` (FK), `observacao` (texto livre, rico pra reportagem), `cod_convenio`, `cod_contrato_repasse`, `autor_emenda`, `valor_original`.

**`siafi_item_empenho`** — FK `id_empenho`. Detalhes de subelemento, descrição, quantidade, valor_unitario, valor_total, valor_atual.

**`siafi_liquidacao`** — PK = `codigo_liquidacao`. FK indireta ao empenho via `siafi_liquidacao_empenho` (não detalhado aqui).

**`siafi_pagamento`** — PK = `codigo_pagamento`. Contém `cnpj_favorecido` (intermediário), valor_original, data_emissao.

**`siafi_pagamento_empenho`** — junction N:N. PK composta = `(codigo_pagamento, codigo_empenho)`. **É a tabela que une empenho ↔ pagamento.**

**`siafi_pagamento_favorecido_final`** — quando pagamento vai a lista (folha de pessoal). FK `codigo_pagamento`, contém `cnpj_favorecido_final`.

**`siafi_fornecedor`** — dim deduplicada. PK = `cnpj_cpf`. Campos: `nome`, `tipo_pessoa` (PJ/PF/SR especial), `primeira_aparicao`, `ultima_aparicao`, `n_empenhos`, `n_pagamentos`, `valor_total_pago`. Atualizada por trigger ou batch nightly.

### 2.4 Convenções

- **Encoding origem:** ISO-8859-1. **Encoding destino:** UTF-8.
- **Separador CSV:** `;`. **Decimal:** `,` (BR). Parser converte pra ponto.
- **User-Agent fixo:** `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.93 Safari/537.36`. Documentado em `siafi/client.py`.
- **Rate limit defensivo:** 1 req / 3s. Não há limite oficial publicado, mas evita acionar WAF.
- **Schemas Parquet:** todos os campos `string` no bronze (preserva fidelidade). Tipagem acontece no silver.
- **Idempotência:** carga silver usa `UPSERT ON CONFLICT (PK) DO UPDATE`. Re-execução é segura.

### 2.5 Detecção de republicação histórica

**Observação validada (2026-06-02):** o portal **republica retroativamente** todos os ZIPs históricos. Exemplo: `201501_Despesas.zip` tem `Last-Modified: Mon, 30 Mar 2026 04:14:21 GMT`.

**Implicação:** backfill 1x + cron incremental mensal **não é suficiente** — dados pretéritos podem ter sido corrigidos.

**Solução:**
1. Armazenar `last_modified_remote` no metadata Parquet de cada arquivo bronze.
2. Job semanal (`siafi-revalidate.yml`) faz HEAD em todos os meses ingeridos, compara `Last-Modified`, refaz ingestão dos meses alterados.
3. Janela de revalidação inicial: últimos 24 meses (foco onde correções são mais comuns). Estender se observarmos mudanças em arquivos mais antigos.

### 2.6 Fornecedor (`siafi_fornecedor`) — design especial

Optamos por **tabela dim própria** (não reusar dim federal genérica) porque:
- Empenhos/pagamentos têm "favorecidos especiais" sem CNPJ válido (códigos SIAFI internos como `-1`, `SI`, `NAO SE APLICA`). Precisa de tipagem `(PJ | PF | ESPECIAL)`.
- Permite enriquecimento posterior (Receita Federal, CADIN, etc.) sem contaminar outras dims.
- Habilita cruzamento futuro com `emendas_pix.cnpj_beneficiario` (que já está no schema do TF).

---

## 3. Alternativas consideradas e rejeitadas

| Alternativa | Por que rejeitada |
|---|---|
| **API REST do Portal da Transparência** (`api.portaldatransparencia.gov.br`) | Exige token, rate limit ~700 req/h, inviável pra histórico completo. Bom pra consultas pontuais. Mantemos como complemento. |
| **Tudo no Supabase (sem R2)** | 7,5 GB CSV → ~5 GB Postgres mesmo com compressão. Plano free Supabase (500 MB DB) estoura. Bronze em R2 mantém Postgres só com o que vira produto. |
| **Mirror data.brasil.io/turicas** | 404 em todos os datasets testados. Mirror descontinuado. |
| **Snapshot diário 1x/dia** | Dados acumulativos — redundância de ~95% entre dias consecutivos. Custo de storage 30x maior sem ganho informacional. |
| **Reusar dim de fornecedor existente** | TF ainda não tem dim federal de fornecedor canônica. Criamos junto. |

---

## 4. Plano de execução (escopo: 9h, 5 fases)

1. **F1 — Discovery + design (1h)** ✅ este documento
2. **F2 — PoC end-to-end 1 mês (3h)** — conector Python + abril/2026 em R2 + smoke test DuckDB
3. **F3 — Schema silver no Supabase (2h)** — migrations + carga incremental
4. **F4 — Backfill 2014→2026 + cron mensal (2h)** — script de backfill + GHA workflows
5. **F5 — 3 queries-trofeu + README (1h)** — ativos pro pitch SP

---

## 5. Decisões pendentes (re-aprovar antes da F2)

- [x] Bronze storage: **Cloudflare R2** (aprovado 2026-06-01)
- [x] Fornecedor: **tabela dim própria `siafi_fornecedor`** (aprovado 2026-06-01)
- [ ] Conta R2: criar nova ou reusar existente? (decisão F2)
- [ ] Limite de janela de revalidação histórica: 24 meses ou todo histórico? (decisão F4)

---

## 6. Referências

- Portal da Transparência — Download de dados: <https://portaldatransparencia.gov.br/download-de-dados>
- Scraper de referência (LGPL-3.0): <https://github.com/turicas/transparencia-gov-br>
- Bucket S3 público: `dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/`
