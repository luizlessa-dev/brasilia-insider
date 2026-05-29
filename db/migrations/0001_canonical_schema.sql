-- The Brasilia Insider — schema canônico multi-casa (atividade legislativa).
-- Cobre deputados, proposições e votações das 27 ALEs + CLDF.
-- NÃO cobre gastos/verba indenizatória — isso vive no pipeline TS (tabelas almg_*).
--
-- Prefixo `ale_` (assembleias legislativas estaduais): namespacing deliberado
-- para NÃO colidir com tabelas federais já existentes no mesmo banco
-- (public.parlamentares, public.proposicoes etc. do pipeline federal). Segue a
-- mesma convenção de prefixo-por-domínio já adotada nas tabelas almg_*.
--
-- Convenção de IDs: os conectores já emitem ids globalmente únicos prefixados
-- pela casa (ex: "almg_12345", "alesp_678"). Por isso as PKs de domínio são TEXT,
-- não serial — o id vem da fonte, garantindo upsert idempotente entre execuções.
--
-- RLS: leitura pública (portal é aberto), escrita só service_role (ingester).

-- ── Casas ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ale_casas (
  id           TEXT PRIMARY KEY,           -- assembly_id: "almg", "alesp", "cldf"
  nome         TEXT NOT NULL,
  nome_curto   TEXT,
  uf           TEXT NOT NULL,
  capital      TEXT,
  n_deputados  INTEGER,
  tier         INTEGER,                     -- 1=API 2=CSV 3=scraping 4=fechado
  base_url     TEXT,
  api_url      TEXT,
  notas        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Parlamentares ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ale_parlamentares (
  id              TEXT PRIMARY KEY,          -- "almg_12345"
  casa_id         TEXT NOT NULL REFERENCES public.ale_casas(id) ON DELETE CASCADE,
  nome            TEXT NOT NULL,
  slug            TEXT,
  partido         TEXT,
  uf              TEXT,
  mandato_inicio  DATE,
  mandato_fim     DATE,
  foto_url        TEXT,
  email           TEXT,
  telefone        TEXT,
  raw             JSONB,
  fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ale_parlamentares_casa ON public.ale_parlamentares(casa_id);
CREATE INDEX IF NOT EXISTS idx_ale_parlamentares_partido ON public.ale_parlamentares(partido);
CREATE INDEX IF NOT EXISTS idx_ale_parlamentares_slug ON public.ale_parlamentares(slug);

-- ── Proposições ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ale_proposicoes (
  id                 TEXT PRIMARY KEY,       -- "almg_678"
  casa_id            TEXT NOT NULL REFERENCES public.ale_casas(id) ON DELETE CASCADE,
  numero             TEXT,
  ano                INTEGER,
  tipo               TEXT,                    -- "PL", "PEC", "PLO", ...
  ementa             TEXT,
  autor              TEXT,
  autor_id           TEXT,                    -- soft ref a ale_parlamentares.id
  data_apresentacao  DATE,
  situacao           TEXT,
  regime             TEXT,
  url                TEXT,
  assuntos           TEXT[],
  raw                JSONB,
  fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ale_proposicoes_casa ON public.ale_proposicoes(casa_id);
CREATE INDEX IF NOT EXISTS idx_ale_proposicoes_ano ON public.ale_proposicoes(ano);
CREATE INDEX IF NOT EXISTS idx_ale_proposicoes_tipo ON public.ale_proposicoes(tipo);
CREATE INDEX IF NOT EXISTS idx_ale_proposicoes_data ON public.ale_proposicoes(data_apresentacao);

-- ── Votações ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ale_votacoes (
  id               TEXT PRIMARY KEY,         -- "almg_999"
  casa_id          TEXT NOT NULL REFERENCES public.ale_casas(id) ON DELETE CASCADE,
  proposicao_id    TEXT,                      -- soft ref a ale_proposicoes.id
  data             DATE,
  hora             TEXT,
  resultado        TEXT,                      -- "aprovado", "rejeitado", ...
  votos_sim        INTEGER NOT NULL DEFAULT 0,
  votos_nao        INTEGER NOT NULL DEFAULT 0,
  votos_abstencao  INTEGER NOT NULL DEFAULT 0,
  votos_ausente    INTEGER NOT NULL DEFAULT 0,
  raw              JSONB,
  fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ale_votacoes_casa ON public.ale_votacoes(casa_id);
CREATE INDEX IF NOT EXISTS idx_ale_votacoes_proposicao ON public.ale_votacoes(proposicao_id);
CREATE INDEX IF NOT EXISTS idx_ale_votacoes_data ON public.ale_votacoes(data);

-- ── Votos nominais ───────────────────────────────────────────────────────
-- Chave natural composta (votacao_id, deputado_id): um deputado vota uma vez
-- por votação. deputado_id é soft ref — votos históricos podem citar deputados
-- fora do mandato atual.
CREATE TABLE IF NOT EXISTS public.ale_votos (
  votacao_id     TEXT NOT NULL REFERENCES public.ale_votacoes(id) ON DELETE CASCADE,
  deputado_id    TEXT NOT NULL,
  deputado_nome  TEXT,
  voto           TEXT,                        -- "sim", "não", "abstenção", "ausente"
  partido        TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (votacao_id, deputado_id)
);
CREATE INDEX IF NOT EXISTS idx_ale_votos_deputado ON public.ale_votos(deputado_id);

-- ── Log de execuções de ingestão ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ale_ingest_runs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  casa_id       TEXT NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  status        TEXT NOT NULL DEFAULT 'running',  -- running, ok, erro, stub
  data_inicio   DATE,
  data_fim      DATE,
  n_deputados   INTEGER NOT NULL DEFAULT 0,
  n_proposicoes INTEGER NOT NULL DEFAULT 0,
  n_votacoes    INTEGER NOT NULL DEFAULT 0,
  erro          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ale_ingest_runs_casa ON public.ale_ingest_runs(casa_id, started_at DESC);

-- ── RLS — leitura pública, escrita service_role ──────────────────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['ale_casas','ale_parlamentares','ale_proposicoes','ale_votacoes','ale_votos','ale_ingest_runs']
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', t);

    EXECUTE format($f$
      DROP POLICY IF EXISTS "public_read_%1$s" ON public.%1$I;
      CREATE POLICY "public_read_%1$s" ON public.%1$I FOR SELECT USING (true);
    $f$, t);

    EXECUTE format($f$
      DROP POLICY IF EXISTS "service_write_%1$s" ON public.%1$I;
      CREATE POLICY "service_write_%1$s" ON public.%1$I FOR ALL
        USING (((current_setting('request.jwt.claims', true))::jsonb ->> 'role') = 'service_role')
        WITH CHECK (((current_setting('request.jwt.claims', true))::jsonb ->> 'role') = 'service_role');
    $f$, t);
  END LOOP;
END $$;
