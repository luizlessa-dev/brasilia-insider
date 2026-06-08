-- The BR Insider — Eleições 2026
-- Candidaturas, financiamento de campanha e gastos para o ciclo eleitoral 2026.
--
-- Calendário de referência:
--   Registro de candidaturas: 20 jun – 5 ago 2026 (prazo TSE estimado)
--   Dados abertos TSE (candidatos): normalmente liberados em agosto/2026
--   Dados de receitas/despesas: liberados em prestação de contas (out/nov 2026)
--   1º turno: 4 out 2026 | 2º turno: 25 out 2026
--
-- Cargos cobertos (CD_CARGO):
--   1 = Presidente/Vice-Presidente
--   3 = Governador/Vice-Governador
--   5 = Senador
--   6 = Deputado Federal
--   7 = Deputado Estadual / Distrital
--
-- Design:
--   Prefixo próprio `ele2026_` → isolado das tabelas históricas `tse_*` (2022/2024).
--   Estrutura intencionalmente espelha tse_candidatos / tse_receitas / tse_despesas
--   para facilitar queries comparativas entre ciclos.
--   Campos de cruzamento (cpf, cnpj) seguem o padrão já consolidado.
--
-- Cruzamentos estratégicos prontos antes dos dados chegarem:
--   ele2026_candidatos.cpf × parlamentares.cpf
--       → candidato é parlamentar ativo? quais emendas destinou?
--   ele2026_candidatos.cpf × tse_candidatos.cpf
--       → histórico eleitoral 2022/2024 do mesmo candidato
--   ele2026_financiamento.cpf_cnpj_doador × emendas_favorecidos.codigo_favorecido
--       → doador de campanha 2026 recebeu emenda de parlamentar no passado
--   ele2026_financiamento.cpf_cnpj_doador × sancoes.cpf_cnpj
--       → doador de campanha está na lista CEIS/CNEP
--   ele2026_gastos.cpf_cnpj_fornecedor × emendas_favorecidos.codigo_favorecido
--       → fornecedor de campanha é favorecido de emenda do mesmo candidato
--   ele2026_gastos.cpf_cnpj_fornecedor × sancoes.cpf_cnpj
--       → fornecedor de campanha sancionado contratado por candidato
--   ele2026_alertas (tabela editorial) → disparo quando candidatos de interesse entram
--
-- RLS: leitura pública, escrita service_role.

-- ═══════════════════════════════════════════════════════════════════════════════
-- 1. CANDIDATOS
-- Arquivo TSE: consulta_cand_2026.csv (sep ";", encoding latin1)
-- Filtro ingestão: CD_CARGO IN (1,3,5,6,7)
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.ele2026_candidatos (
  -- "<ano>_<sq_candidato>" — mesmo padrão de tse_candidatos
  id                      TEXT          PRIMARY KEY,

  sq_candidato            TEXT          NOT NULL,   -- SQ_CANDIDATO original (único no ano)

  -- identificação pessoal
  cpf                     TEXT,                     -- NR_CPF_CANDIDATO — chave de cruzamento
  nome                    TEXT          NOT NULL,   -- NM_CANDIDATO
  nome_urna               TEXT,
  data_nascimento         DATE,
  genero                  TEXT,
  cor_raca                TEXT,
  grau_instrucao          TEXT,
  ocupacao                TEXT,
  estado_civil            TEXT,
  email                   TEXT,
  foto_url                TEXT,                     -- URL da foto oficial TSE (quando disponível)

  -- cargo e localização
  cd_cargo                SMALLINT,
  cargo                   TEXT,                     -- DS_CARGO
  uf                      CHAR(2),
  municipio_nascimento    TEXT,

  -- partido
  nr_partido              SMALLINT,
  sigla_partido           TEXT,
  nome_partido            TEXT,

  -- federação / coligação (reintroduzido em 2022; relevante para deputados/governadores)
  nome_federacao          TEXT,                     -- NM_FEDERACAO_PARTIDARIA (se houver)
  sigla_federacao         TEXT,                     -- SG_FEDERACAO_PARTIDARIA

  -- resultado (preenchido após apuração)
  situacao_candidatura    TEXT,                     -- DS_SITUACAO_CANDIDATURA (Deferida / Indeferida)
  situacao_turno1         TEXT,                     -- DS_SIT_TOT_TURNO (1º turno)
  situacao_turno2         TEXT,                     -- DS_SIT_TOT_TURNO (2º turno — se aplicável)
  eleito                  BOOLEAN,                  -- derivado de situacao_turno*; NULL até apuração
  reeleicao               BOOLEAN,

  -- financeiro (limite declarado ao TSE)
  limite_despesa          NUMERIC(16,2),

  -- cruzamento com mandato anterior (preenchido na ingestão quando disponível)
  parlamentar_id          UUID          REFERENCES public.parlamentares(id),
  id_camara               INTEGER,                  -- para join direto com emendas_favorecidos.codigo_autor

  ingested_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ele26_cand_cpf
  ON public.ele2026_candidatos(cpf)
  WHERE cpf IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ele26_cand_cargo
  ON public.ele2026_candidatos(cd_cargo);

CREATE INDEX IF NOT EXISTS idx_ele26_cand_uf
  ON public.ele2026_candidatos(uf);

CREATE INDEX IF NOT EXISTS idx_ele26_cand_partido
  ON public.ele2026_candidatos(sigla_partido);

CREATE INDEX IF NOT EXISTS idx_ele26_cand_parlamentar
  ON public.ele2026_candidatos(parlamentar_id)
  WHERE parlamentar_id IS NOT NULL;

COMMENT ON TABLE public.ele2026_candidatos IS
  'Candidatos federais e estaduais (eleições outubro 2026). '
  'Tabela vazia até TSE liberar dados (~agosto 2026). '
  'cpf cruza com parlamentares.cpf (mandato anterior) e tse_candidatos.cpf (histórico). '
  'parlamentar_id preenchido na ingestão para candidatos que são deputados/senadores ativos.';

-- ═══════════════════════════════════════════════════════════════════════════════
-- 2. FINANCIAMENTO DE CAMPANHA (receitas)
-- Arquivo TSE: receitas_candidatos_2026_BRASIL.csv
-- Disponível após início da campanha (normalmente set/out 2026)
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.ele2026_financiamento (
  id                          BIGSERIAL     PRIMARY KEY,

  numero_recibo               TEXT,                     -- dedup natural
  data_receita                DATE,

  -- candidato que recebe
  cpf_candidato               TEXT,                     -- FK lógica → ele2026_candidatos.cpf
  nome_candidato              TEXT,
  cargo                       TEXT,
  sigla_partido               TEXT,
  uf                          CHAR(2),

  -- doador
  cpf_cnpj_doador             TEXT,                     -- chave cruzamento emendas / sanções
  nome_doador                 TEXT,
  tipo_doador                 TEXT,                     -- 'PF' | 'PJ' | 'Partido' | etc.
  setor_economico_doador      TEXT,

  -- doador originário (quando passa por intermediário)
  cpf_cnpj_doador_originario  TEXT,
  nome_doador_originario      TEXT,

  -- classificação
  natureza_receita            TEXT,
  origem_receita              TEXT,
  especie_recurso             TEXT,
  fonte_recurso               TEXT,

  -- valor
  valor                       NUMERIC(16,2) NOT NULL,

  data_prestacao_contas       DATE,
  ingested_at                 TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ele26_fin_recibo
  ON public.ele2026_financiamento(numero_recibo)
  WHERE numero_recibo IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ele26_fin_cpf_cand
  ON public.ele2026_financiamento(cpf_candidato)
  WHERE cpf_candidato IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ele26_fin_cnpj_doador
  ON public.ele2026_financiamento(cpf_cnpj_doador)
  WHERE cpf_cnpj_doador IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ele26_fin_uf
  ON public.ele2026_financiamento(uf);

COMMENT ON TABLE public.ele2026_financiamento IS
  'Receitas de campanha 2026 (TSE). '
  'cpf_cnpj_doador cruza com emendas_favorecidos.codigo_favorecido e sancoes.cpf_cnpj. '
  'Dedup por numero_recibo. Tabela vazia até prestação de contas (~out/nov 2026).';

-- ═══════════════════════════════════════════════════════════════════════════════
-- 3. GASTOS DE CAMPANHA (despesas)
-- Arquivo TSE: despesas_candidatos_2026_BRASIL.csv
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.ele2026_gastos (
  id                      BIGSERIAL     PRIMARY KEY,

  numero_documento        TEXT,                     -- NR_DOCUMENTO_DESPESA
  data_despesa            DATE,

  -- candidato que gastou
  cpf_candidato           TEXT,
  nome_candidato          TEXT,
  cargo                   TEXT,
  sigla_partido           TEXT,
  uf                      CHAR(2),

  -- fornecedor do serviço / produto
  cpf_cnpj_fornecedor     TEXT,                     -- chave cruzamento emendas / sanções
  nome_fornecedor         TEXT,

  -- classificação
  tipo_despesa            TEXT,                     -- DS_TIPO_DESPESA
  descricao_despesa       TEXT,
  origem_despesa          TEXT,
  especie_recurso         TEXT,
  fonte_recurso           TEXT,

  -- valores
  valor_despesa           NUMERIC(16,2) NOT NULL,
  valor_prestado          NUMERIC(16,2),

  ingested_at             TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ele26_gast_cpf_cand
  ON public.ele2026_gastos(cpf_candidato)
  WHERE cpf_candidato IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ele26_gast_cnpj_forn
  ON public.ele2026_gastos(cpf_cnpj_fornecedor)
  WHERE cpf_cnpj_fornecedor IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ele26_gast_tipo
  ON public.ele2026_gastos(tipo_despesa);

COMMENT ON TABLE public.ele2026_gastos IS
  'Despesas de campanha 2026 (TSE). '
  'cpf_cnpj_fornecedor cruza com emendas_favorecidos e sancoes. '
  'Tabela vazia até prestação de contas (~out/nov 2026).';

-- ═══════════════════════════════════════════════════════════════════════════════
-- 4. ALERTAS EDITORIAIS
-- Lista de candidatos de interesse para monitoramento proativo.
-- Preenchida manualmente (investigados nas emendas, fichas sujas, etc.)
-- Disparo quando financiamento / resultado entrar no banco.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.ele2026_alertas (
  id                  SERIAL        PRIMARY KEY,

  -- identificação do alvo
  cpf                 TEXT,                         -- CPF (preferencial, único por pessoa)
  nome                TEXT          NOT NULL,        -- nome como referência editorial
  uf                  CHAR(2),
  cargo_interesse     TEXT,                         -- cargo pelo qual está concorrendo em 2026

  -- motivo do monitoramento (pode ser múltiplo)
  motivos             TEXT[]        NOT NULL,       -- ex: {'emenda_xcmg','sancao_ceis','investigado'}
  descricao           TEXT,                         -- contexto editorial livre (não publicado)

  -- referências cruzadas já confirmadas
  parlamentar_id      UUID          REFERENCES public.parlamentares(id),
  emenda_total_hist   NUMERIC(16,2),               -- total de emendas históricas (snapshot)
  tem_sancao          BOOLEAN       DEFAULT false,
  investigacoes       TEXT[],                       -- referências a matérias / dossiês internos

  -- controle de disparo
  alerta_ativo        BOOLEAN       NOT NULL DEFAULT true,
  candidatura_entrou  BOOLEAN       NOT NULL DEFAULT false,  -- true quando entrar em ele2026_candidatos
  financiamento_entrou BOOLEAN      NOT NULL DEFAULT false,  -- true quando 1ª receita entrar
  notificado_em       TIMESTAMPTZ,

  criado_em           TIMESTAMPTZ   NOT NULL DEFAULT now(),
  atualizado_em       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ele26_alert_cpf
  ON public.ele2026_alertas(cpf)
  WHERE cpf IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ele26_alert_ativo
  ON public.ele2026_alertas(alerta_ativo)
  WHERE alerta_ativo = true;

COMMENT ON TABLE public.ele2026_alertas IS
  'Candidatos de interesse editorial pré-cadastrados para monitoramento em 2026. '
  'Alimentado manualmente antes dos dados chegarem. '
  'candidatura_entrou e financiamento_entrou marcados pelo conector na ingestão. '
  'motivos: array de tags (emenda_xcmg, sancao_ceis, investigado, etc.).';

-- ═══════════════════════════════════════════════════════════════════════════════
-- 5. LOG DE INGESTÃO (padrão da stack)
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.ele2026_ingest_log (
  id              BIGSERIAL   PRIMARY KEY,
  dataset         TEXT        NOT NULL,             -- 'candidatos', 'financiamento', 'gastos'
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ,
  status          TEXT        NOT NULL DEFAULT 'running', -- running | ok | erro
  n_processados   INTEGER,
  n_novos         INTEGER,
  n_atualizados   INTEGER,
  erro            TEXT
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 6. VIEWS DE CRUZAMENTO
-- Prontas agora — retornam vazio até os dados chegarem, mas já são usáveis
-- ═══════════════════════════════════════════════════════════════════════════════

-- ─── 6a. Candidato 2026 × rastro de emendas (parlamentar ativo) ──────────────
-- "Este candidato, enquanto deputado/senador, destinou emendas para quem?"
CREATE OR REPLACE VIEW public.ele26_v_candidato_emendas AS
SELECT
  c.nome                              AS candidato,
  c.cargo,
  c.sigla_partido,
  c.uf,
  ef.codigo_favorecido                AS cnpj_favorecido,
  ef.nome_favorecido,
  ef.municipio_favorecido,
  ef.uf_favorecido,
  ef.valor_recebido,
  ef.ano_emenda,
  ef.tipo_emenda,
  ef.subtipo,
  ef.funcao,
  ef.subfuncao
FROM public.ele2026_candidatos c
JOIN public.parlamentares p
  ON p.cpf = c.cpf
JOIN public.emendas_favorecidos ef
  ON ef.codigo_autor::integer = p.id_camara
WHERE c.cpf IS NOT NULL;

COMMENT ON VIEW public.ele26_v_candidato_emendas IS
  'Emendas destinadas por candidatos 2026 em seus mandatos anteriores. '
  'Retorna vazio até ele2026_candidatos ser preenchido (agosto 2026). '
  'Filtrar por candidato ou cnpj_favorecido para investigar.';

-- ─── 6b. Doador de campanha 2026 × emendas recebidas no passado ──────────────
-- "Esta empresa que está doando já foi favorecida por emendas do mesmo candidato?"
CREATE OR REPLACE VIEW public.ele26_v_doador_emenda_hist AS
SELECT
  f.nome_candidato                    AS candidato,
  f.sigla_partido,
  f.uf,
  f.nome_doador                       AS empresa_doadora,
  f.cpf_cnpj_doador                   AS cnpj_doador,
  f.setor_economico_doador,
  f.valor                             AS valor_doacao_2026,
  ef.valor_recebido                   AS valor_emenda_hist,
  ef.ano_emenda,
  ef.municipio_favorecido,
  ef.tipo_emenda,
  ef.subtipo
FROM public.ele2026_financiamento f
JOIN public.tse_candidatos tc
  ON tc.cpf = f.cpf_candidato
JOIN public.parlamentares p
  ON p.cpf = f.cpf_candidato
JOIN public.emendas_favorecidos ef
  ON ef.codigo_autor::integer = p.id_camara
  AND ef.codigo_favorecido    = f.cpf_cnpj_doador
WHERE length(f.cpf_cnpj_doador) = 14   -- apenas CNPJ
  AND f.tipo_doador ILIKE '%jurídica%';

COMMENT ON VIEW public.ele26_v_doador_emenda_hist IS
  'Empresas que doaram para campanha 2026 e já receberam emendas do mesmo candidato no passado. '
  'Retorna vazio até ele2026_financiamento ser preenchido. '
  'Mesmo padrão de tse_v_doador_emenda para comparação entre ciclos.';

-- ─── 6c. Doador / fornecedor de campanha 2026 × sanções CEIS/CNEP ────────────
-- "Esta empresa está proibida de contratar com o governo e está financiando campanha?"
CREATE OR REPLACE VIEW public.ele26_v_financiamento_sancoes AS
SELECT
  'doador'                            AS papel,
  f.nome_candidato                    AS candidato,
  f.sigla_partido,
  f.uf,
  f.cpf_cnpj_doador                   AS cpf_cnpj,
  f.nome_doador                       AS nome_empresa,
  f.valor                             AS valor_campanha,
  f.tipo_doador,
  s.cadastro                          AS sancao_cadastro,      -- CEIS | CNEP
  s.tipo_sancao,
  s.data_inicio                       AS sancao_inicio,
  s.data_fim                          AS sancao_fim,
  s.orgao_nome                        AS orgao_sancionador
FROM public.ele2026_financiamento f
JOIN public.sancoes s
  ON s.cpf_cnpj = f.cpf_cnpj_doador
WHERE f.cpf_cnpj_doador IS NOT NULL

UNION ALL

SELECT
  'fornecedor'                        AS papel,
  g.nome_candidato                    AS candidato,
  g.sigla_partido,
  g.uf,
  g.cpf_cnpj_fornecedor               AS cpf_cnpj,
  g.nome_fornecedor                   AS nome_empresa,
  g.valor_despesa                     AS valor_campanha,
  NULL                                AS tipo_doador,
  s.cadastro                          AS sancao_cadastro,
  s.tipo_sancao,
  s.data_inicio                       AS sancao_inicio,
  s.data_fim                          AS sancao_fim,
  s.orgao_nome                        AS orgao_sancionador
FROM public.ele2026_gastos g
JOIN public.sancoes s
  ON s.cpf_cnpj = g.cpf_cnpj_fornecedor
WHERE g.cpf_cnpj_fornecedor IS NOT NULL;

COMMENT ON VIEW public.ele26_v_financiamento_sancoes IS
  'Empresas sancionadas (CEIS/CNEP) que aparecem como doadores ou fornecedores de campanha 2026. '
  'UNION de ele2026_financiamento + ele2026_gastos × sancoes. '
  'papel = "doador" (receitas) ou "fornecedor" (despesas).';

-- ─── 6d. Candidato 2026 × histórico eleitoral (2022/2024) ───────────────────
-- "Este candidato já concorreu antes? Com qual resultado?"
CREATE OR REPLACE VIEW public.ele26_v_historico_eleitoral AS
SELECT
  c.nome                              AS candidato,
  c.cpf,
  c.cargo                             AS cargo_2026,
  c.uf,
  c.sigla_partido                     AS partido_2026,
  -- histórico TSE
  h.ano_eleicao,
  h.cargo                             AS cargo_hist,
  h.sigla_partido                     AS partido_hist,
  h.situacao_turno                    AS resultado_hist,
  h.limite_despesa                    AS limite_hist
FROM public.ele2026_candidatos c
JOIN public.tse_candidatos h
  ON h.cpf = c.cpf
WHERE c.cpf IS NOT NULL
ORDER BY c.nome, h.ano_eleicao DESC;

COMMENT ON VIEW public.ele26_v_historico_eleitoral IS
  'Histórico eleitoral (2022+2024) de candidatos que também concorrem em 2026. '
  'Join por CPF. Retorna vazio até ele2026_candidatos ser preenchido.';

-- ─── 6e. Painel de alertas — estado atual dos monitorados ────────────────────
CREATE OR REPLACE VIEW public.ele26_v_alertas_painel AS
SELECT
  a.id,
  a.nome,
  a.uf,
  a.cargo_interesse,
  a.motivos,
  a.descricao,
  a.tem_sancao,
  a.emenda_total_hist,
  a.candidatura_entrou,
  a.financiamento_entrou,
  a.alerta_ativo,
  -- candidatura no banco?
  c.id                                AS candidatura_id,
  c.sigla_partido,
  c.situacao_candidatura,
  c.eleito,
  -- total doado para ele até o momento
  SUM(f.valor)                        AS total_arrecadado_2026,
  COUNT(DISTINCT f.cpf_cnpj_doador)   AS n_doadores_2026
FROM public.ele2026_alertas a
LEFT JOIN public.ele2026_candidatos c
  ON c.cpf = a.cpf
LEFT JOIN public.ele2026_financiamento f
  ON f.cpf_candidato = a.cpf
WHERE a.alerta_ativo = true
GROUP BY
  a.id, a.nome, a.uf, a.cargo_interesse, a.motivos, a.descricao,
  a.tem_sancao, a.emenda_total_hist, a.candidatura_entrou,
  a.financiamento_entrou, a.alerta_ativo,
  c.id, c.sigla_partido, c.situacao_candidatura, c.eleito
ORDER BY a.emenda_total_hist DESC NULLS LAST;

COMMENT ON VIEW public.ele26_v_alertas_painel IS
  'Painel dos candidatos monitorados — estado de entrada no banco + arrecadação. '
  'Atualiza automaticamente conforme dados chegam. '
  'Ordenado por emenda_total_hist DESC para priorizar os de maior rastro.';

-- ═══════════════════════════════════════════════════════════════════════════════
-- 7. RLS
-- ═══════════════════════════════════════════════════════════════════════════════
ALTER TABLE public.ele2026_candidatos    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ele2026_financiamento ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ele2026_gastos        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ele2026_alertas       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ele2026_ingest_log    ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT unnest(ARRAY[
    'ele2026_candidatos',
    'ele2026_financiamento',
    'ele2026_gastos',
    'ele2026_alertas',
    'ele2026_ingest_log'
  ])
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS %I ON public.%I; '
      'CREATE POLICY %I ON public.%I FOR SELECT USING (true);',
      'public_read_' || tbl, tbl,
      'public_read_' || tbl, tbl
    );
  END LOOP;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- RESUMO DE OBJETOS CRIADOS
-- ─────────────────────────────────────────────────────────────────────────────
-- Tabelas (5):
--   ele2026_candidatos      — candidatos federais/estaduais 2026 (vazia até ago/2026)
--   ele2026_financiamento   — receitas de campanha 2026 (vazia até out/nov 2026)
--   ele2026_gastos          — despesas de campanha 2026 (vazia até out/nov 2026)
--   ele2026_alertas         — candidatos de interesse — alimentar AGORA
--   ele2026_ingest_log      — log de ingestão
--
-- Views (5):
--   ele26_v_candidato_emendas       — candidato × emendas destinadas no mandato anterior
--   ele26_v_doador_emenda_hist      — doador 2026 × emendas recebidas no passado
--   ele26_v_financiamento_sancoes   — doadores/fornecedores de campanha × CEIS/CNEP
--   ele26_v_historico_eleitoral     — candidato 2026 × resultado 2022/2024
--   ele26_v_alertas_painel          — painel dos monitorados em tempo real
--
-- Próximos passos:
--   [1] Aplicar migration no Supabase (prod)
--   [2] Alimentar ele2026_alertas com candidatos do dossiê de emendas
--   [3] Criar conector stub em ingestao/tse_2026_connector.py
--   [4] GHA workflow em standby (on: workflow_dispatch → vira schedule em ago/2026)
-- ═══════════════════════════════════════════════════════════════════════════════
