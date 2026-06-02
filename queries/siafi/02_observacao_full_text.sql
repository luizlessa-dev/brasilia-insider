-- ═══════════════════════════════════════════════════════════════════════════
-- QUERY-TROFEU 02 — Empenhos com observação suspeita (full-text)
-- ═══════════════════════════════════════════════════════════════════════════
-- Pergunta jornalística: "Mostra todos os empenhos da Saúde acima de R$ 500k
-- cuja observação menciona dispensa, emergencial, inexigibilidade, sem licitação."
--
-- Por que essa não é trivial no Portal da Transparência oficial:
--   - Busca textual livre não existe na UI pública
--   - O campo `observacao` é onde aparecem justificativas problemáticas
--   - Combinar valor + órgão + palavras-chave exige cruzamento que a UI não tem
--
-- Tipos de "achado" que essa query habilita:
--   - Dispensas emergenciais sequenciais (rastro de fracionamento)
--   - "DEA — Despesas de Exercício Anterior" (pagamentos atrasados)
--   - Reforço de empenho ("REFORCO DE NE") — sinal de orçamento mal dimensionado
--   - Justificativas genéricas em valores altos

-- ─── Versão PostgreSQL (silver) ────────────────────────────────────────────
SELECT
  e.snapshot_date,
  e.data_emissao,
  e.cod_orgao_superior,
  e.nome_orgao_superior,
  e.codigo_empenho,
  e.nome_favorecido,
  e.valor_original_empenho::NUMERIC(20,2)         AS valor_brl,
  LEFT(e.observacao, 200)                         AS observacao_preview,
  e.modalidade_licitacao,
  ts_rank(to_tsvector('portuguese', e.observacao),
          plainto_tsquery('portuguese', $1))      AS relevancia
FROM siafi_empenho e
WHERE to_tsvector('portuguese', coalesce(e.observacao, ''))
       @@ plainto_tsquery('portuguese', $1)
  AND e.valor_original_empenho > $2
  AND ($3 IS NULL OR e.cod_orgao_superior = $3)
ORDER BY e.valor_original_empenho DESC NULLS LAST
LIMIT 50;

-- Parâmetros:
--   $1 = termo de busca (ex: 'dispensa emergencial', 'inexigibilidade')
--   $2 = valor mínimo em reais (ex: 500000)
--   $3 = código órgão superior (ex: '36000' = Min. Saúde) ou NULL pra todos

-- Exemplo de uso:
--   SELECT ... WHERE $1 = 'emergencial dispensa' AND $2 = 500000 AND $3 = '36000'

-- ─── Versão DuckDB (bronze) — busca simples por ILIKE ──────────────────────
-- DUCKDB BEGIN
-- SELECT
--   nome_orgao_superior, codigo_empenho, nome_favorecido,
--   TRY_CAST(REPLACE(REPLACE(valor_original_empenho, '.', ''), ',', '.') AS DOUBLE) AS valor_brl,
--   LEFT(observacao, 200) AS observacao_preview
-- FROM read_parquet('/tmp/brinsider-lake/siafi/snapshot/snapshot_date=2025-04-30/empenho.parquet')
-- WHERE (observacao ILIKE '%emergencial%' OR observacao ILIKE '%dispensa%' OR observacao ILIKE '%inexigibilidade%')
--   AND TRY_CAST(REPLACE(REPLACE(valor_original_empenho, '.', ''), ',', '.') AS DOUBLE) > 500000
-- ORDER BY valor_brl DESC NULLS LAST
-- LIMIT 50;
-- DUCKDB END
