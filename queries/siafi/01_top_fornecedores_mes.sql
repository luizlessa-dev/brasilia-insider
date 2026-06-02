-- ═══════════════════════════════════════════════════════════════════════════
-- QUERY-TROFEU 01 — TOP fornecedores por valor pago num snapshot
-- ═══════════════════════════════════════════════════════════════════════════
-- Pergunta jornalística: "Quais foram os 20 maiores recebedores de dinheiro
-- público federal num determinado dia/mês, e que tipo de pessoa são?"
--
-- Por que essa não é trivial no Portal da Transparência oficial:
--   - UI agrega por órgão pagador, não por favorecido
--   - Filtro por tipo de pessoa (PJ/PF/EXTERIOR/ESPECIAL) não existe na UI
--   - Pra ranking precisa baixar 30+ MB de CSV e somar manualmente
--
-- Latência observada localmente (DuckDB sobre Parquet, 30/abr/2025): 12 ms

-- ─── Versão PostgreSQL (silver, produção) ──────────────────────────────────
SELECT
  f.tipo_pessoa,
  f.cnpj_cpf,
  f.nome,
  COUNT(*)                AS n_pagamentos,
  SUM(p.valor_pagamento_brl)::NUMERIC(20,2) AS valor_total_brl,
  ROUND(AVG(p.valor_pagamento_brl)::NUMERIC, 2) AS ticket_medio
FROM siafi_pagamento p
JOIN siafi_fornecedor f ON f.cnpj_cpf = p.cnpj_favorecido
WHERE p.snapshot_date = '2025-04-30'
  AND p.valor_pagamento_brl IS NOT NULL
GROUP BY f.tipo_pessoa, f.cnpj_cpf, f.nome
ORDER BY valor_total_brl DESC NULLS LAST
LIMIT 20;

-- ─── Versão DuckDB (bronze local, sem Supabase) ────────────────────────────
-- LAKE_ROOT=/tmp/brinsider-lake (ou path absoluto)
-- duckdb -c "$(cat queries/siafi/01_top_fornecedores_mes.sql | grep -A100 DUCKDB)"
--
-- DUCKDB BEGIN
-- SELECT
--   nome_favorecido,
--   cnpj_favorecido,
--   COUNT(*) AS n_pagamentos,
--   ROUND(SUM(TRY_CAST(
--     REPLACE(REPLACE(valor_original_pagamento, '.', ''), ',', '.') AS DOUBLE
--   )), 2) AS valor_total_brl
-- FROM read_parquet('/tmp/brinsider-lake/siafi/snapshot/snapshot_date=2025-04-30/pagamento.parquet')
-- WHERE valor_original_pagamento IS NOT NULL AND valor_original_pagamento <> ''
-- GROUP BY 1, 2
-- ORDER BY valor_total_brl DESC NULLS LAST
-- LIMIT 20;
-- DUCKDB END
