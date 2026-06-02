-- The Brasilia Insider — log de ingestão SIAFI.
-- Tabela auxiliar usada pelo cron pra detectar mudanças retroativas no
-- Portal da Transparência (CGU republica histórico ocasionalmente).
--
-- Pra cada execução do cron, gravamos (competencia, source_last_modified,
-- status). A próxima execução compara o Last-Modified atual com o último
-- gravado — se mudou, reprocessa; se igual, skipa.

CREATE TABLE IF NOT EXISTS public.siafi_ingestao_log (
  id                    BIGSERIAL PRIMARY KEY,
  stream                TEXT NOT NULL CHECK (stream IN ('execucao_mensal', 'snapshot_diario')),
  competencia           TEXT,                 -- YYYY-MM (mensal) ou YYYY-MM-DD (snapshot)
  source_url            TEXT NOT NULL,
  source_last_modified  TIMESTAMPTZ,
  rows_bronze           INTEGER,
  rows_silver           INTEGER,
  status                TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'skipped')),
  error                 TEXT,
  duration_seconds      NUMERIC(10,2),
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_siafi_log_competencia ON public.siafi_ingestao_log(competencia);
CREATE INDEX IF NOT EXISTS idx_siafi_log_stream      ON public.siafi_ingestao_log(stream);
CREATE INDEX IF NOT EXISTS idx_siafi_log_ingested    ON public.siafi_ingestao_log(ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_siafi_log_status      ON public.siafi_ingestao_log(status) WHERE status <> 'ok';

ALTER TABLE public.siafi_ingestao_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read_siafi_ingestao_log ON public.siafi_ingestao_log;
CREATE POLICY public_read_siafi_ingestao_log ON public.siafi_ingestao_log FOR SELECT USING (true);

COMMENT ON TABLE public.siafi_ingestao_log IS
  'Log de cada execução do cron de ingestão SIAFI. Usado pra detectar '
  'republicação retroativa do Portal da Transparência (compara Last-Modified).';
