-- The Brasilia Insider — Sanções (CEIS + CNEP)
-- Fonte: API Portal da Transparência
--   https://api.portaldatransparencia.gov.br/api-de-dados/ceis
--   https://api.portaldatransparencia.gov.br/api-de-dados/cnep
--
-- CEIS — Cadastro de Empresas Inidôneas e Suspensas
--   Empresas/pessoas impedidas de licitar ou contratar com o governo federal.
-- CNEP — Cadastro Nacional de Empresas Punidas
--   Empresas/pessoas com punições administrativas (multas, suspensões, proibições).
--
-- Design: tabela única `sancoes` com coluna `cadastro` (CEIS|CNEP).
--   Simplifica joins e evita duplicação de schema.
--   PK: id (int da API) — garante upsert idempotente.
--
-- Cruzamentos estratégicos:
--   sancoes.cpf_cnpj × emendas_favorecidos.codigo_favorecido
--       → empresa sancionada recebeu emenda (achado puro)
--   sancoes.cpf_cnpj × tse_receitas.cpf_cnpj_doador
--       → empresa sancionada financiou campanha
--   sancoes.cpf_cnpj × tse_despesas.cpf_cnpj_fornecedor
--       → empresa sancionada prestou serviço de campanha
--
-- RLS: leitura pública, escrita service_role.

CREATE TABLE IF NOT EXISTS public.sancoes (
  -- PK da API do Portal da Transparência
  id                      INTEGER       PRIMARY KEY,
  cadastro                TEXT          NOT NULL CHECK (cadastro IN ('CEIS','CNEP')),

  -- sancionado
  cpf_cnpj                TEXT,          -- só dígitos — CHAVE DE CRUZAMENTO
  cpf_cnpj_formatado      TEXT,          -- formato original (14.895.123/0001-45)
  tipo_pessoa             TEXT,          -- 'PF' ou 'PJ'
  nome                    TEXT,          -- nome/razão social conforme CEIS/CNEP
  razao_social            TEXT,          -- razão social conforme Receita
  nome_fantasia           TEXT,

  -- tipo e período da sanção
  tipo_sancao             TEXT,          -- descrição resumida
  descricao_sancao        TEXT,          -- descrição completa do portal
  data_inicio             DATE,
  data_fim                DATE,          -- NULL = prazo indeterminado
  data_publicacao         DATE,
  data_transitado         DATE,          -- trânsito em julgado
  data_referencia         DATE,

  -- órgão sancionador
  orgao_nome              TEXT,
  orgao_uf                CHAR(2),
  orgao_poder             TEXT,          -- Executivo, Judiciário, Legislativo
  orgao_esfera            TEXT,          -- Federal, Estadual, Municipal

  -- detalhes
  numero_processo         TEXT,
  fundamentacao           TEXT[],        -- lista de textos legais
  valor_multa             TEXT,          -- apenas CNEP; mantido como texto (formato variável)
  abrangencia             TEXT,
  informacoes_adicionais  TEXT,
  link_publicacao         TEXT,

  updated_at              TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Índice principal: cruzamento com emendas e campanhas
CREATE INDEX IF NOT EXISTS idx_sancoes_cpf_cnpj
  ON public.sancoes(cpf_cnpj)
  WHERE cpf_cnpj IS NOT NULL;

-- Busca por cadastro e período ativo
CREATE INDEX IF NOT EXISTS idx_sancoes_cadastro_data
  ON public.sancoes(cadastro, data_inicio, data_fim);

-- Busca por nome (investigação manual)
CREATE INDEX IF NOT EXISTS idx_sancoes_nome
  ON public.sancoes USING gin(to_tsvector('portuguese', coalesce(nome,'') || ' ' || coalesce(razao_social,'')));

COMMENT ON TABLE public.sancoes IS
  'CEIS + CNEP unificados (Portal da Transparência). '
  'cpf_cnpj (só dígitos) cruza com emendas_favorecidos.codigo_favorecido, '
  'tse_receitas.cpf_cnpj_doador e tse_despesas.cpf_cnpj_fornecedor.';

-- ═══════════════════════════════════════════════════════════════════════════
-- VIEW 1: Empresa sancionada que recebeu emenda parlamentar
-- "Empresa impedida de contratar com o governo E recebeu dinheiro público"
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.v_sancao_emenda AS
SELECT
  s.cadastro,
  s.cpf_cnpj,
  s.nome                                     AS nome_sancionado,
  s.tipo_sancao,
  s.data_inicio                              AS sancao_inicio,
  s.data_fim                                 AS sancao_fim,
  s.orgao_nome                               AS orgao_sancionador,
  s.orgao_uf,
  ef.valor_recebido                          AS valor_emenda,
  ef.ano_emenda,
  ef.nome_autor                              AS parlamentar,
  ef.municipio_favorecido,
  ef.uf_favorecido,
  ef.tipo_emenda,
  ef.subtipo
FROM public.sancoes s
JOIN public.emendas_favorecidos ef
  ON ef.codigo_favorecido = s.cpf_cnpj
WHERE length(s.cpf_cnpj) = 14              -- apenas CNPJ (PJ)
ORDER BY ef.valor_recebido DESC NULLS LAST;

COMMENT ON VIEW public.v_sancao_emenda IS
  'Empresas sancionadas (CEIS/CNEP) que receberam emendas parlamentares. '
  'Cruza sancoes.cpf_cnpj com emendas_favorecidos.codigo_favorecido. '
  'Alerta: não considera vigência da sanção vs. data da emenda — filtrar se necessário.';

-- ═══════════════════════════════════════════════════════════════════════════
-- VIEW 2: Empresa sancionada que também doou para campanhas (CEIS × TSE)
-- "Empresa impedida de contratar financiou candidato"
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.v_sancao_doacao AS
SELECT
  s.cadastro,
  s.cpf_cnpj,
  s.nome                                     AS nome_sancionado,
  s.tipo_sancao,
  s.data_inicio                              AS sancao_inicio,
  r.nome_candidato                           AS candidato,
  r.sigla_partido,
  r.uf,
  r.cargo,
  r.ano_eleicao,
  r.valor                                    AS valor_doacao,
  r.origem_receita
FROM public.sancoes s
JOIN public.tse_receitas r
  ON r.cpf_cnpj_doador = s.cpf_cnpj
WHERE length(s.cpf_cnpj) = 14
ORDER BY r.valor DESC NULLS LAST;

COMMENT ON VIEW public.v_sancao_doacao IS
  'Empresas sancionadas (CEIS/CNEP) que doaram para campanhas eleitorais. '
  'Cruza sancoes.cpf_cnpj com tse_receitas.cpf_cnpj_doador.';

-- ═══════════════════════════════════════════════════════════════════════════
-- LOG de ingestão
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.sancoes_ingest_log (
  id           BIGSERIAL   PRIMARY KEY,
  dataset      TEXT        NOT NULL,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  status       TEXT        NOT NULL DEFAULT 'running',
  n_novos      INTEGER,
  erro         TEXT
);

-- ═══════════════════════════════════════════════════════════════════════════
-- RLS
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE public.sancoes          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sancoes_ingest_log ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT unnest(ARRAY['sancoes','sancoes_ingest_log'])
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS %I ON public.%I; '
      'CREATE POLICY %I ON public.%I FOR SELECT USING (true);',
      'public_read_'||tbl, tbl, 'public_read_'||tbl, tbl
    );
  END LOOP;
END $$;
