-- The Brasilia Insider — Senado Federal: CEAPS + Votações Nominais
--
-- Stream A: senado_ceaps_despesa
--   Fonte: https://adm.senado.gov.br/adm-dadosabertos/api/v1/senadores/despesas_ceaps/{ano}
--   Espelho da cota_despesa (Câmara). Mesmo campo chave: cpf_cnpj → cruzamento fornecedor.
--
-- Stream B: senado_votacao + senado_voto + senado_orientacao
--   Fonte: https://legis.senado.leg.br/dadosabertos/plenario/lista/votacao/{ini}/{fim}.json
--   Espelho de camara_votacao. Votos individuais de 81 senadores por votação.
--
-- Cruzamentos estratégicos:
--   senado_ceaps_despesa.cpf_cnpj × emendas_favorecidos.codigo_favorecido
--   senado_ceaps_despesa.cpf_cnpj × cota_despesa.cnpj_cpf_fornecedor  → mesmo CNPJ nos dois lados
--   senado_voto × senado_orientacao → dissidência no Senado

-- ═══════════════════════════════════════════════════════════════════
-- A. CEAPS — Cota dos Senadores
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.senado_ceaps_despesa (
  id                  BIGINT        PRIMARY KEY,     -- campo "id" da API
  tipo_documento      TEXT,
  ano                 SMALLINT      NOT NULL,
  mes                 SMALLINT      NOT NULL,
  cod_senador         INTEGER       NOT NULL,
  nome_senador        TEXT          NOT NULL,
  tipo_despesa        TEXT          NOT NULL,
  cpf_cnpj            TEXT,                          -- chave de cruzamento
  nome_fornecedor     TEXT,
  documento           TEXT,
  data                DATE,
  detalhamento        TEXT,
  valor_reembolsado   NUMERIC(14,2) NOT NULL DEFAULT 0,
  ano_csv             SMALLINT      NOT NULL,
  ingested_at         TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_senado_ceaps_senador  ON public.senado_ceaps_despesa(cod_senador);
CREATE INDEX IF NOT EXISTS idx_senado_ceaps_cnpj     ON public.senado_ceaps_despesa(cpf_cnpj)
  WHERE cpf_cnpj IS NOT NULL AND cpf_cnpj <> '';
CREATE INDEX IF NOT EXISTS idx_senado_ceaps_ano_mes  ON public.senado_ceaps_despesa(ano, mes);
CREATE INDEX IF NOT EXISTS idx_senado_ceaps_tipo     ON public.senado_ceaps_despesa(tipo_despesa);

COMMENT ON TABLE public.senado_ceaps_despesa IS
  'CEAPS — Cota para o Exercício da Atividade Parlamentar dos Senadores. '
  'Disponível 2008–atual. cpf_cnpj é a chave de cruzamento com emendas e cota da Câmara.';

-- ═══════════════════════════════════════════════════════════════════
-- B1. Votação (cabeçalho)
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.senado_votacao (
  id_sve              INTEGER       PRIMARY KEY,     -- codigoVotacaoSve
  cod_sessao          INTEGER,
  cod_sessao_votacao  INTEGER,
  data_sessao         DATE,
  hora_inicio         TEXT,
  tipo_sessao         TEXT,
  numero_sessao       TEXT,
  descricao           TEXT,
  resultado           TEXT,                          -- "Aprovado","Rejeitado","Prejudicado"
  cod_materia         INTEGER,
  sigla_materia       TEXT,                          -- "PL","PEC","PLS" etc.
  numero_materia      TEXT,
  ano_materia         SMALLINT,
  secreta             BOOLEAN NOT NULL DEFAULT false,
  votos_sim           SMALLINT,
  votos_nao           SMALLINT,
  votos_abstencao     SMALLINT,
  ingested_at         TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_senado_vot_data     ON public.senado_votacao(data_sessao);
CREATE INDEX IF NOT EXISTS idx_senado_vot_materia  ON public.senado_votacao(cod_materia)
  WHERE cod_materia IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_senado_vot_resultado ON public.senado_votacao(resultado);

COMMENT ON TABLE public.senado_votacao IS
  'Cabeçalho de votações do Plenário do Senado. '
  'id_sve = codigoVotacaoSve (identificador canônico da API).';

-- ═══════════════════════════════════════════════════════════════════
-- B2. Voto individual
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.senado_voto (
  id_sve              INTEGER       NOT NULL REFERENCES public.senado_votacao(id_sve) ON DELETE CASCADE,
  cod_parlamentar     INTEGER       NOT NULL,
  nome_parlamentar    TEXT,
  sigla_partido       TEXT,
  sigla_uf            TEXT,
  voto                TEXT          NOT NULL,        -- "Sim","Não","Abstenção","Obstrução","P-OD","NCom"
  ingested_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
  PRIMARY KEY (id_sve, cod_parlamentar)
);
CREATE INDEX IF NOT EXISTS idx_senado_voto_parl    ON public.senado_voto(cod_parlamentar);
CREATE INDEX IF NOT EXISTS idx_senado_voto_partido ON public.senado_voto(sigla_partido);
CREATE INDEX IF NOT EXISTS idx_senado_voto_voto    ON public.senado_voto(voto);

-- ═══════════════════════════════════════════════════════════════════
-- B3. Orientação de bancada
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.senado_orientacao (
  id_sve              INTEGER       NOT NULL REFERENCES public.senado_votacao(id_sve) ON DELETE CASCADE,
  sigla_partido       TEXT          NOT NULL,
  orientacao          TEXT          NOT NULL,
  ingested_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
  PRIMARY KEY (id_sve, sigla_partido)
);
CREATE INDEX IF NOT EXISTS idx_senado_ori_partido ON public.senado_orientacao(sigla_partido);

-- ═══════════════════════════════════════════════════════════════════
-- VIEW OURO: Dissidência no Senado
-- ═══════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.senado_dissidencia AS
SELECT
  v.id_sve,
  v.cod_parlamentar,
  v.nome_parlamentar,
  v.sigla_partido,
  v.sigla_uf,
  v.voto                    AS voto_real,
  o.orientacao              AS orientacao_partido,
  vot.data_sessao,
  vot.descricao,
  vot.sigla_materia,
  vot.numero_materia,
  vot.ano_materia
FROM public.senado_voto v
JOIN public.senado_orientacao o
  ON o.id_sve = v.id_sve AND o.sigla_partido = v.sigla_partido
JOIN public.senado_votacao vot ON vot.id_sve = v.id_sve
WHERE
  o.orientacao NOT IN ('Liberado', 'Abstenção')
  AND v.voto NOT IN ('Abstenção', 'P-OD', 'NCom')
  AND v.voto <> o.orientacao;

-- ═══════════════════════════════════════════════════════════════════
-- VIEW OURO: Cruzamento CEAPS × Emendas (Senado)
-- ═══════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.senado_ceaps_emenda_cruzamento AS
SELECT
  c.cpf_cnpj                                        AS cnpj,
  c.nome_fornecedor                                 AS nome_na_ceaps,
  COUNT(DISTINCT c.cod_senador)                     AS senadores_ceaps,
  SUM(c.valor_reembolsado)                          AS total_ceaps_brl,
  e.favorecido                                      AS nome_na_emenda,
  e.valor_total                                     AS total_emenda_brl,
  e.n_autores                                       AS autores_emenda
FROM public.senado_ceaps_despesa c
JOIN (
  SELECT
    codigo_favorecido,
    MAX(favorecido)                                 AS favorecido,
    SUM(valor_recebido)                             AS valor_total,
    COUNT(DISTINCT codigo_autor)                    AS n_autores
  FROM public.emendas_favorecidos
  WHERE codigo_favorecido IS NOT NULL AND codigo_favorecido <> ''
  GROUP BY codigo_favorecido
) e ON e.codigo_favorecido = c.cpf_cnpj
WHERE c.cpf_cnpj IS NOT NULL AND c.cpf_cnpj <> ''
GROUP BY c.cpf_cnpj, c.nome_fornecedor, e.favorecido, e.valor_total, e.n_autores
ORDER BY (SUM(c.valor_reembolsado) + e.valor_total) DESC NULLS LAST;

COMMENT ON VIEW public.senado_ceaps_emenda_cruzamento IS
  'CNPJs que receberam da CEAPS (senadores) E de emendas parlamentares. '
  'Candidatos a investigação jornalística.';

-- ═══════════════════════════════════════════════════════════════════
-- RLS
-- ═══════════════════════════════════════════════════════════════════
DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT unnest(ARRAY[
    'senado_ceaps_despesa','senado_votacao','senado_voto','senado_orientacao'
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
