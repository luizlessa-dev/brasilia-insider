-- The Brasilia Insider — Dados complementares da Câmara Federal.
-- Cobre 4 streams via API dadosabertos.camara.leg.br/api/v2:
--
--   1. camara_votacao        — votações do Plenário (cabeçalho)
--   2. camara_voto           — voto individual por deputado
--   3. camara_orientacao     — orientação de bancada por votação
--   4. camara_frente         — frentes parlamentares
--   5. camara_frente_membro  — membros de cada frente
--   6. camara_ocupacao       — histórico profissional dos deputados
--
-- Prefixo `camara_` — namespacing claro:
--   cota_*       → despesas CEAP
--   ale_*        → atividade legislativa estadual
--   siafi_*      → execução orçamentária
--   parlamentares, proposicoes → pipeline federal legado
--
-- RLS: leitura pública, escrita só service_role.

-- ═══════════════════════════════════════════════════════════════════
-- 1. Votação (cabeçalho)
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.camara_votacao (
  id                    TEXT PRIMARY KEY,          -- ex: "2450672-40"
  data                  DATE,
  data_hora_registro    TIMESTAMPTZ,
  sigla_orgao           TEXT,                       -- "PLEN", "CCJ", etc.
  uri_evento            TEXT,
  proposicao_objeto     TEXT,                       -- "PL 1234/2023"
  tipo_votacao          TEXT,                       -- "Nominal", "Simbólica"
  descricao             TEXT,
  aprovacao             SMALLINT,                   -- 1=aprovado, 0=rejeitado, NULL=inconclusivo
  votos_sim             SMALLINT,
  votos_nao             SMALLINT,
  votos_abstencao       SMALLINT,
  total_votos           SMALLINT,
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_camara_vot_data      ON public.camara_votacao(data);
CREATE INDEX IF NOT EXISTS idx_camara_vot_orgao     ON public.camara_votacao(sigla_orgao);
CREATE INDEX IF NOT EXISTS idx_camara_vot_prop      ON public.camara_votacao(proposicao_objeto)
  WHERE proposicao_objeto IS NOT NULL;

COMMENT ON TABLE public.camara_votacao IS
  'Cabeçalho de votações da Câmara Federal (Plenário + comissões). '
  'Uma linha por votação. Votos individuais em camara_voto.';

-- ═══════════════════════════════════════════════════════════════════
-- 2. Voto individual
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.camara_voto (
  id_votacao            TEXT NOT NULL REFERENCES public.camara_votacao(id) ON DELETE CASCADE,
  id_deputado           INTEGER NOT NULL,           -- nuDeputadoId da Câmara
  nome_deputado         TEXT,
  sigla_partido         TEXT,
  sigla_uf              TEXT,
  voto                  TEXT NOT NULL,              -- "Sim","Não","Abstenção","Obstrução","Art. 17","Presidente"
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_votacao, id_deputado)
);
CREATE INDEX IF NOT EXISTS idx_camara_voto_dep      ON public.camara_voto(id_deputado);
CREATE INDEX IF NOT EXISTS idx_camara_voto_partido  ON public.camara_voto(sigla_partido);
CREATE INDEX IF NOT EXISTS idx_camara_voto_voto     ON public.camara_voto(voto);

COMMENT ON TABLE public.camara_voto IS
  'Voto individual de cada deputado por votação. '
  'JOIN com camara_votacao.proposicao_objeto revela padrão de voto por tema.';

-- ═══════════════════════════════════════════════════════════════════
-- 3. Orientação de bancada
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.camara_orientacao (
  id_votacao            TEXT NOT NULL REFERENCES public.camara_votacao(id) ON DELETE CASCADE,
  sigla_partido         TEXT NOT NULL,
  orientacao            TEXT NOT NULL,              -- "Sim","Não","Abstenção","Liberado"
  cod_tipo_lideranca    TEXT,
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_votacao, sigla_partido)
);
CREATE INDEX IF NOT EXISTS idx_camara_ori_partido   ON public.camara_orientacao(sigla_partido);

COMMENT ON TABLE public.camara_orientacao IS
  'Orientação oficial de cada bancada por votação. '
  'Contraste com camara_voto revela dissidências.';

-- ═══════════════════════════════════════════════════════════════════
-- 4. Frente parlamentar
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.camara_frente (
  id                    INTEGER PRIMARY KEY,
  titulo                TEXT NOT NULL,
  id_legislatura        SMALLINT,
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_camara_frente_leg    ON public.camara_frente(id_legislatura);
CREATE INDEX IF NOT EXISTS idx_camara_frente_titulo ON public.camara_frente
  USING gin (to_tsvector('portuguese', titulo));

-- ═══════════════════════════════════════════════════════════════════
-- 5. Membro de frente
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.camara_frente_membro (
  id_frente             INTEGER NOT NULL REFERENCES public.camara_frente(id) ON DELETE CASCADE,
  id_deputado           INTEGER NOT NULL,
  nome_deputado         TEXT,
  sigla_partido         TEXT,
  sigla_uf              TEXT,
  titulo_na_frente      TEXT,                       -- "Titular","Coordenador", etc.
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_frente, id_deputado)
);
CREATE INDEX IF NOT EXISTS idx_camara_fm_dep        ON public.camara_frente_membro(id_deputado);
CREATE INDEX IF NOT EXISTS idx_camara_fm_partido    ON public.camara_frente_membro(sigla_partido);

COMMENT ON TABLE public.camara_frente_membro IS
  'Membros de cada frente parlamentar. '
  'Cruzamento com cota_despesa e emendas_favorecidos por id_deputado '
  'revela alinhamento entre agenda declarada e uso de verba.';

-- ═══════════════════════════════════════════════════════════════════
-- 6. Histórico profissional / ocupações
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.camara_ocupacao (
  id_deputado           INTEGER NOT NULL,
  titulo                TEXT NOT NULL,
  entidade              TEXT,
  entidade_uf           TEXT,
  entidade_pais         TEXT,
  ano_inicio            SMALLINT,
  ano_fim               SMALLINT,
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_deputado, titulo)
);
CREATE INDEX IF NOT EXISTS idx_camara_ocup_dep      ON public.camara_ocupacao(id_deputado);
CREATE INDEX IF NOT EXISTS idx_camara_ocup_titulo   ON public.camara_ocupacao
  USING gin (to_tsvector('portuguese', titulo));

COMMENT ON TABLE public.camara_ocupacao IS
  'Histórico profissional declarado dos deputados. '
  'Útil para identificar conflito de interesses com emendas/cota.';

-- ═══════════════════════════════════════════════════════════════════
-- VIEW OURO: Dissidência — voto contra orientação do partido
-- ═══════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.camara_dissidencia AS
SELECT
  v.id_votacao,
  v.id_deputado,
  v.nome_deputado,
  v.sigla_partido,
  v.sigla_uf,
  v.voto                          AS voto_real,
  o.orientacao                    AS orientacao_partido,
  vot.data,
  vot.proposicao_objeto,
  vot.sigla_orgao
FROM public.camara_voto v
JOIN public.camara_orientacao o
  ON o.id_votacao = v.id_votacao AND o.sigla_partido = v.sigla_partido
JOIN public.camara_votacao vot ON vot.id = v.id_votacao
WHERE
  o.orientacao NOT IN ('Liberado', 'Abstenção')   -- orientação definida
  AND v.voto NOT IN ('Abstenção', 'Presidente', 'Art. 17')  -- voto registrado
  AND v.voto <> o.orientacao;                     -- divergência

COMMENT ON VIEW public.camara_dissidencia IS
  'Votos em que o deputado divergiu da orientação oficial do partido. '
  'Base para análise de coesão partidária e identificação de dissidentes.';

-- ═══════════════════════════════════════════════════════════════════
-- VIEW OURO: Ranking de dissidência por deputado
-- ═══════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.camara_ranking_dissidencia AS
SELECT
  id_deputado,
  nome_deputado,
  sigla_partido,
  sigla_uf,
  COUNT(*) AS n_dissidencias,
  COUNT(DISTINCT id_votacao) AS votacoes_divergentes
FROM public.camara_dissidencia
GROUP BY id_deputado, nome_deputado, sigla_partido, sigla_uf
ORDER BY n_dissidencias DESC;

-- ═══════════════════════════════════════════════════════════════════
-- RLS
-- ═══════════════════════════════════════════════════════════════════
DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT unnest(ARRAY[
    'camara_votacao','camara_voto','camara_orientacao',
    'camara_frente','camara_frente_membro','camara_ocupacao'
  ])
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', tbl);
    EXECUTE format(
      'DROP POLICY IF EXISTS %I ON public.%I; '
      'CREATE POLICY %I ON public.%I FOR SELECT USING (true);',
      'public_read_'||tbl, tbl, 'public_read_'||tbl, tbl
    );
  END LOOP;
END $$;
