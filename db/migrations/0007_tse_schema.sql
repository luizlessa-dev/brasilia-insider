-- The Brasilia Insider — TSE (Tribunal Superior Eleitoral)
-- Fonte: https://dadosabertos.tse.jus.br/
-- Dados: candidatos federais/estaduais + receitas de campanha (2022 e 2024)
--
-- Prefixo `tse_` — sem colidir com:
--   ale_*           (atividade legislativa estadual)
--   siafi_*         (execução orçamentária)
--   emendas_*       (emendas parlamentares)
--   cgu_pad_*       (processos disciplinares)
--
-- Cruzamentos estratégicos:
--   tse_candidatos.cpf × parlamentares.cpf
--       → enrichment de perfil: partido de origem, histórico eleitoral
--   tse_receitas.cpf_cnpj_doador × emendas_favorecidos.cnpj_favorecido
--       → empresa doou para campanha E recebeu emenda do mesmo parlamentar
--   tse_receitas.cpf_cnpj_doador × tse_candidatos.cpf
--       → parlamentar recebeu doação de outro político (rede de financiamento)
--
-- RLS: leitura pública, escrita service_role.

-- ═══════════════════════════════════════════════════════════════════════════
-- CANDIDATOS
-- Arquivo: consulta_cand_<ano>.csv  (separador ";", encoding latin1)
-- Filtro ingestão: CD_CARGO IN (1,3,5,6,7) — Presidente, Governador, Senador,
--                 Dep. Federal, Dep. Estadual
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.tse_candidatos (
  -- chave: TSE usa SQ_CANDIDATO como PK, mas só é único dentro do ano
  id                      TEXT        PRIMARY KEY,  -- "<ano>_<sq_candidato>"

  ano_eleicao             SMALLINT    NOT NULL,
  sq_candidato            TEXT        NOT NULL,     -- SQ_CANDIDATO original

  -- identificação pessoal
  cpf                     TEXT,                     -- NR_CPF_CANDIDATO (chave cruzamento)
  nome                    TEXT        NOT NULL,     -- NM_CANDIDATO
  nome_urna               TEXT,
  data_nascimento         DATE,
  genero                  TEXT,
  cor_raca                TEXT,
  grau_instrucao          TEXT,
  ocupacao                TEXT,
  estado_civil            TEXT,
  email                   TEXT,

  -- cargo e localização
  cd_cargo                SMALLINT,
  cargo                   TEXT,                     -- DS_CARGO
  uf                      CHAR(2),
  municipio_nascimento    TEXT,

  -- partido
  nr_partido              SMALLINT,
  sigla_partido           TEXT,
  nome_partido            TEXT,

  -- resultado
  situacao_candidatura    TEXT,                     -- DS_SITUACAO_CANDIDATURA
  situacao_turno          TEXT,                     -- DS_SIT_TOT_TURNO
  reeleicao               BOOLEAN,

  -- financeiro
  limite_despesa          NUMERIC(16,2),

  ingested_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tse_cand_cpf
  ON public.tse_candidatos(cpf)
  WHERE cpf IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tse_cand_ano_cargo
  ON public.tse_candidatos(ano_eleicao, cd_cargo);

CREATE INDEX IF NOT EXISTS idx_tse_cand_uf
  ON public.tse_candidatos(uf);

CREATE INDEX IF NOT EXISTS idx_tse_cand_partido
  ON public.tse_candidatos(sigla_partido);

COMMENT ON TABLE public.tse_candidatos IS
  'Candidatos federais e estaduais (TSE, 2022+2024). '
  'Chave cpf permite cruzamento com parlamentares.cpf. '
  'Filtrado para CD_CARGO IN (1,3,5,6,7).';

-- ═══════════════════════════════════════════════════════════════════════════
-- RECEITAS DE CAMPANHA
-- Arquivo: receitas_candidatos_<ano>.csv  (separador ";", encoding latin1)
-- Foco: doações de pessoa jurídica (natureza_receita LIKE '%Jurídica%')
--       mas ingere tudo — filtragem fica nas views
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.tse_receitas (
  id                          BIGSERIAL   PRIMARY KEY,

  ano_eleicao                 SMALLINT    NOT NULL,
  numero_recibo               TEXT,                     -- dedup natural

  -- candidato recebedor
  cpf_candidato               TEXT,                     -- cpf no arquivo
  nome_candidato              TEXT,
  cargo                       TEXT,
  sigla_partido               TEXT,
  uf                          CHAR(2),

  -- doador
  cpf_cnpj_doador             TEXT,                     -- chave cruzamento emendas
  nome_doador                 TEXT,
  tipo_doador                 TEXT,                     -- PF / PJ / partido / etc.
  setor_economico_doador      TEXT,

  -- doador originário (cascata)
  cpf_cnpj_doador_originario  TEXT,
  nome_doador_originario      TEXT,

  -- caracterização
  natureza_receita            TEXT,
  origem_receita              TEXT,
  especie_recurso             TEXT,
  fonte_recurso               TEXT,

  -- valor
  valor                       NUMERIC(16,2) NOT NULL,

  -- datas
  data_receita                DATE,
  data_prestacao_contas       DATE,

  ingested_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- índice de dedup na re-ingestão
CREATE UNIQUE INDEX IF NOT EXISTS idx_tse_receitas_recibo_ano
  ON public.tse_receitas(numero_recibo, ano_eleicao)
  WHERE numero_recibo IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tse_receitas_cpf_cand
  ON public.tse_receitas(cpf_candidato)
  WHERE cpf_candidato IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tse_receitas_cnpj_doador
  ON public.tse_receitas(cpf_cnpj_doador)
  WHERE cpf_cnpj_doador IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tse_receitas_ano
  ON public.tse_receitas(ano_eleicao);

COMMENT ON TABLE public.tse_receitas IS
  'Receitas de campanha por candidato (TSE, 2022+2024). '
  'cpf_cnpj_doador permite cruzamento com emendas_favorecidos.cnpj_favorecido. '
  'Inclui doações PF, PJ, partido e recursos próprios.';

-- ═══════════════════════════════════════════════════════════════════════════
-- VIEW: doador de campanha que também é favorecido de emenda
-- "Empresa financiou a campanha e recebeu emenda do mesmo parlamentar"
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.tse_v_doador_emenda AS
SELECT
  c.nome                                      AS parlamentar,
  c.sigla_partido,
  c.uf,
  c.ano_eleicao,
  r.nome_doador                               AS empresa_doadora,
  r.cpf_cnpj_doador                           AS cnpj_doador,
  r.setor_economico_doador,
  r.valor                                     AS valor_doacao,
  ef.valor_recebido                           AS valor_emenda,
  ef.ano_emenda,
  ef.municipio_favorecido,
  ef.uf_favorecido,
  ef.tipo_emenda,
  ef.subtipo
FROM public.tse_receitas r
JOIN public.tse_candidatos c
  ON r.cpf_candidato = c.cpf
  AND r.ano_eleicao  = c.ano_eleicao
JOIN public.parlamentares p
  ON p.cpf = c.cpf
JOIN public.emendas_favorecidos ef
  ON ef.codigo_autor::integer = p.id_camara
  AND ef.codigo_favorecido    = r.cpf_cnpj_doador
WHERE length(r.cpf_cnpj_doador) = 14          -- só CNPJ, exclui PF
  AND r.tipo_doador ILIKE '%jurídica%';

COMMENT ON VIEW public.tse_v_doador_emenda IS
  'Empresas que doaram para campanha de parlamentar e receberam emenda do mesmo. '
  'Join: tse_candidatos.cpf → parlamentares.cpf → emendas_favorecidos via id_camara. '
  'Filtro: apenas doadores PJ (CNPJ 14 dígitos). Exclui bancadas/comissões.';

-- ═══════════════════════════════════════════════════════════════════════════
-- LOG de ingestão
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.tse_ingest_log (
  id              BIGSERIAL   PRIMARY KEY,
  dataset         TEXT        NOT NULL,  -- 'candidatos_2024', 'receitas_2022', etc.
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ,
  status          TEXT        NOT NULL DEFAULT 'running',
  n_processados   INTEGER,
  n_novos         INTEGER,
  n_atualizados   INTEGER,
  erro            TEXT
);

-- ═══════════════════════════════════════════════════════════════════════════
-- RLS
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE public.tse_candidatos  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tse_receitas    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tse_ingest_log  ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT unnest(ARRAY[
    'tse_candidatos', 'tse_receitas', 'tse_ingest_log'
  ])
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS %I ON public.%I; '
      'CREATE POLICY %I ON public.%I FOR SELECT USING (true);',
      'public_read_' || tbl, tbl, 'public_read_' || tbl, tbl
    );
  END LOOP;
END $$;
