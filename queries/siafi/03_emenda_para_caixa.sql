-- ═══════════════════════════════════════════════════════════════════════════
-- QUERY-TROFEU 03 — Caminho do dinheiro: emenda → empenho → pagamento → favorecido final
-- ═══════════════════════════════════════════════════════════════════════════
-- Pergunta jornalística: "Pra cada autor de emenda parlamentar, mostra o
-- caminho completo do dinheiro: que empenhos saíram, em quais OBs foram
-- pagos, e quem foi o favorecido final (incluindo quando o pagamento vai
-- pra uma lista)."
--
-- Por que essa não é trivial no Portal da Transparência oficial:
--   - É um JOIN N:N:N em 4 tabelas (emenda↔empenho via texto, empenho↔OB
--     via junction, OB↔favorecido_final via outra junction)
--   - UI mostra um nível por vez; pra cruzar precisa exportar tudo e
--     fazer planilhas
--   - Junction pagamento_empenho é INVISÍVEL na UI (Despesas → Pagamentos
--     mostra só o pagador genérico, não o empenho de origem)
--
-- Isso é o cruzamento que diferencia o BR Insider do Portal oficial. Era
-- exatamente o tipo de trabalho que tornou a apuração de Emendas Pix
-- possível.

-- ─── Versão PostgreSQL (silver, completa) ──────────────────────────────────
WITH base AS (
  SELECT
    e.autor_emenda,
    e.codigo_empenho,
    e.nome_favorecido AS favorecido_empenho,
    e.cod_orgao_superior,
    e.nome_orgao_superior,
    e.valor_empenho_brl,
    e.modalidade_licitacao
  FROM siafi_empenho e
  WHERE e.snapshot_date = $1::DATE
    AND e.autor_emenda IS NOT NULL
    AND e.autor_emenda <> 'SEM EMENDA'
),
caminho AS (
  SELECT
    b.autor_emenda,
    b.codigo_empenho,
    b.favorecido_empenho,
    b.cod_orgao_superior,
    b.nome_orgao_superior,
    b.valor_empenho_brl,
    pe.codigo_pagamento,
    pe.valor_pago,
    pff.cnpj_favorecido_final,
    pff.nome_favorecido_final,
    pff.valor_pagamento_brl AS valor_para_favorecido_final
  FROM base b
  LEFT JOIN siafi_pagamento_empenho pe
    ON pe.codigo_empenho = b.codigo_empenho AND pe.snapshot_date = $1::DATE
  LEFT JOIN siafi_pagamento_favorecido_final pff
    ON pff.codigo_pagamento = pe.codigo_pagamento AND pff.snapshot_date = $1::DATE
)
SELECT
  autor_emenda,
  cod_orgao_superior,
  nome_orgao_superior,
  COUNT(DISTINCT codigo_empenho)            AS n_empenhos,
  COUNT(DISTINCT codigo_pagamento)          AS n_pagamentos,
  COUNT(DISTINCT cnpj_favorecido_final)     AS n_favorecidos_finais,
  SUM(valor_empenho_brl)::NUMERIC(20,2)     AS total_empenhado_brl,
  SUM(valor_pago)::NUMERIC(20,2)            AS total_pago_brl,
  -- Top 3 favorecidos finais como array
  array_agg(DISTINCT nome_favorecido_final ORDER BY nome_favorecido_final NULLS LAST)
    FILTER (WHERE nome_favorecido_final IS NOT NULL) AS favorecidos_finais
FROM caminho
GROUP BY autor_emenda, cod_orgao_superior, nome_orgao_superior
ORDER BY total_pago_brl DESC NULLS LAST
LIMIT 50;

-- Parâmetros:
--   $1 = snapshot_date (ex: '2025-04-30')

-- ─── Versão DuckDB (bronze) — versão simplificada (sem favorecido final) ───
-- DUCKDB BEGIN
-- SELECT
--   e.autor_emenda,
--   e.nome_orgao_superior,
--   COUNT(DISTINCT e.codigo_empenho) AS n_empenhos,
--   COUNT(DISTINCT pe.codigo_pagamento) AS n_pagamentos,
--   ROUND(SUM(TRY_CAST(REPLACE(REPLACE(e.valor_original_empenho, '.', ''), ',', '.') AS DOUBLE)), 2) AS total_empenhado_brl
-- FROM read_parquet('/tmp/brinsider-lake/siafi/snapshot/snapshot_date=2025-04-30/empenho.parquet') e
-- LEFT JOIN read_parquet('/tmp/brinsider-lake/siafi/snapshot/snapshot_date=2025-04-30/pagamento_empenho.parquet') pe
--   ON pe.codigo_empenho = e.codigo_empenho
-- WHERE e.autor_emenda IS NOT NULL AND e.autor_emenda <> 'SEM EMENDA' AND e.autor_emenda <> ''
-- GROUP BY 1, 2
-- ORDER BY total_empenhado_brl DESC NULLS LAST
-- LIMIT 50;
-- DUCKDB END
