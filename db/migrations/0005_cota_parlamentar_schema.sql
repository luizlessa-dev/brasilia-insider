-- The Brasilia Insider — Cota Parlamentar (CEAP) federal.
-- Fonte primária: downloads CSV anuais em
--   http://dadosabertos.camara.leg.br/arquivos/despesasDeputados/
-- Granularidade: 1 linha por nota fiscal / documento de despesa.
--
-- Prefixo `cota_` — namespacing claro, sem colidir com:
--   siafi_*          (execução orçamentária)
--   ale_*            (atividade legislativa estadual)
--   emendas_*        (emendas parlamentares)
--   parlamentares    (pipeline federal legado)
--
-- Cruzamentos possíveis:
--   cnpj_cpf_fornecedor × emendas_favorecidos.cnpj   → mesmo fornecedor
--   cnpj_cpf_fornecedor × siafi_fornecedor.cnpj_cpf  → mesmo CNPJ no SIAFI
--   id_deputado → parlamentares (pipeline federal legado)
--
-- RLS: leitura pública (dado público), escrita só service_role.

-- ═════════════════════════════════════════════════════════════════════════
-- DIM: Deputado (snapshot de cada mandato presente no CSV)
-- Normalizado do CSV para evitar repetição de texto em cada despesa.
-- ═════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.cota_deputado (
  id_camara           INTEGER PRIMARY KEY,   -- "idDeputado" no CSV
  nome                TEXT NOT NULL,
  cpf                 TEXT,
  partido             TEXT,
  uf                  TEXT,
  legislatura         SMALLINT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cota_dep_partido ON public.cota_deputado(partido);
CREATE INDEX IF NOT EXISTS idx_cota_dep_uf      ON public.cota_deputado(uf);

COMMENT ON TABLE public.cota_deputado IS
  'Dim de deputados derivada do CSV da Cota Parlamentar. '
  'Atualizada por upsert a cada ingestão — não é a fonte canônica de perfis.';

-- ═════════════════════════════════════════════════════════════════════════
-- FATO: Despesa individual da Cota
-- ═════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.cota_despesa (
  -- PK natural da fonte
  id_documento        BIGINT      NOT NULL,   -- "idDocumento" (único por nota)
  id_deputado         INTEGER     NOT NULL REFERENCES public.cota_deputado(id_camara),

  -- Quando
  ano                 SMALLINT    NOT NULL,
  mes                 SMALLINT    NOT NULL,
  data_emissao        DATE,

  -- O que
  tipo_despesa        TEXT        NOT NULL,   -- "Combustíveis e lubrificantes.", etc.
  sub_quotaid_cnt     SMALLINT,              -- subtipo interno (inteiro)
  descricao           TEXT,

  -- Fornecedor
  cnpj_cpf_fornecedor TEXT,                 -- CNPJ ou CPF (campo "cnpjCpf")
  nome_fornecedor     TEXT,

  -- Documento fiscal
  tipo_documento      SMALLINT,             -- 0=SF, 1=NF, 2=recibo, etc.
  numero_documento    TEXT,                 -- número da nota/recibo

  -- Valores
  valor_documento     NUMERIC(14,2) NOT NULL DEFAULT 0,
  valor_liquido       NUMERIC(14,2) NOT NULL DEFAULT 0,
  valor_glosa         NUMERIC(14,2) NOT NULL DEFAULT 0,

  -- Passagem aérea (preenchido quando tipo = viagem)
  num_sub_cota        SMALLINT,
  trecho             TEXT,

  -- Metadados de ingestão
  ano_csv             SMALLINT    NOT NULL,  -- ano do arquivo CSV de origem
  ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (id_documento, id_deputado)
);

-- Índices para as queries mais frequentes
CREATE INDEX IF NOT EXISTS idx_cota_desp_deputado  ON public.cota_despesa(id_deputado);
CREATE INDEX IF NOT EXISTS idx_cota_desp_cnpj       ON public.cota_despesa(cnpj_cpf_fornecedor)
  WHERE cnpj_cpf_fornecedor IS NOT NULL AND cnpj_cpf_fornecedor <> '';
CREATE INDEX IF NOT EXISTS idx_cota_desp_ano_mes    ON public.cota_despesa(ano, mes);
CREATE INDEX IF NOT EXISTS idx_cota_desp_tipo       ON public.cota_despesa(tipo_despesa);
CREATE INDEX IF NOT EXISTS idx_cota_desp_fornecedor_nome ON public.cota_despesa
  USING gin (to_tsvector('portuguese', coalesce(nome_fornecedor, '')));

COMMENT ON TABLE public.cota_despesa IS
  'Fato de despesas da Cota Parlamentar (CEAP) federal. '
  'Uma linha por nota/documento. cnpj_cpf_fornecedor é o campo de cruzamento '
  'com emendas_favorecidos e siafi_fornecedor.';

-- ═════════════════════════════════════════════════════════════════════════
-- VIEW OURO: Ranking de CNPJ por valor total na Cota
-- Usada diretamente pelo produto de cruzamento emenda × cota.
-- ═════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.cota_cnpj_ranking AS
SELECT
  cnpj_cpf_fornecedor                                   AS cnpj,
  nome_fornecedor,
  COUNT(DISTINCT id_deputado)                           AS n_deputados,
  COUNT(*)                                              AS n_notas,
  SUM(valor_liquido)                                    AS total_liquido_brl,
  MIN(data_emissao)                                     AS primeira_nota,
  MAX(data_emissao)                                     AS ultima_nota
FROM public.cota_despesa
WHERE cnpj_cpf_fornecedor IS NOT NULL AND cnpj_cpf_fornecedor <> ''
GROUP BY cnpj_cpf_fornecedor, nome_fornecedor
ORDER BY total_liquido_brl DESC NULLS LAST;

COMMENT ON VIEW public.cota_cnpj_ranking IS
  'Ranking de fornecedores por valor total liquidado na Cota Parlamentar. '
  'JOIN com emendas_favorecidos.cnpj revela empresas nos dois fluxos.';

-- ═════════════════════════════════════════════════════════════════════════
-- VIEW OURO: Cruzamento Cota × Emendas (mesmo CNPJ nos dois fluxos)
-- Requer emendas_favorecidos já populada (pipeline federal legado).
-- ═════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.cota_emenda_cruzamento AS
SELECT
  c.cnpj_cpf_fornecedor                              AS cnpj,
  c.nome_fornecedor                                  AS nome_na_cota,
  COUNT(DISTINCT c.id_deputado)                      AS dep_cota,
  SUM(c.valor_liquido)                               AS total_cota_brl,
  e.nome_favorecido                                  AS nome_na_emenda,
  e.valor_total                                      AS total_emenda_brl,
  e.n_autores                                        AS autores_emenda
FROM public.cota_despesa c
JOIN (
  SELECT
    cnpj,
    MAX(nome_favorecido)                             AS nome_favorecido,
    SUM(valor_repasse)                               AS valor_total,
    COUNT(DISTINCT autor_cpf)                        AS n_autores
  FROM public.emendas_favorecidos
  WHERE cnpj IS NOT NULL AND cnpj <> ''
  GROUP BY cnpj
) e ON e.cnpj = c.cnpj_cpf_fornecedor
WHERE c.cnpj_cpf_fornecedor IS NOT NULL AND c.cnpj_cpf_fornecedor <> ''
GROUP BY c.cnpj_cpf_fornecedor, c.nome_fornecedor, e.nome_favorecido, e.valor_total, e.n_autores
ORDER BY (SUM(c.valor_liquido) + e.valor_total) DESC NULLS LAST;

COMMENT ON VIEW public.cota_emenda_cruzamento IS
  'Empresas que aparecem nos dois fluxos de dinheiro federal: '
  'cota parlamentar (uso pessoal do deputado) + emendas (verba pública). '
  'Candidatos a investigação jornalística.';

-- ═════════════════════════════════════════════════════════════════════════
-- RLS
-- ═════════════════════════════════════════════════════════════════════════
ALTER TABLE public.cota_deputado  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cota_despesa   ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT unnest(ARRAY['cota_deputado','cota_despesa'])
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS %I ON public.%I; '
      'CREATE POLICY %I ON public.%I FOR SELECT USING (true);',
      'public_read_' || tbl, tbl, 'public_read_' || tbl, tbl
    );
  END LOOP;
END $$;
