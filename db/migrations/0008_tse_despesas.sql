-- The Brasilia Insider — TSE Despesas de Campanha
-- Fonte: https://dadosabertos.tse.jus.br/ (mesmo ZIP das receitas)
-- Arquivo interno: despesas_candidatos_<ano>_BRASIL.csv
--
-- Cruzamentos estratégicos:
--   tse_despesas.cpf_cnpj_fornecedor × emendas_favorecidos.cnpj_favorecido
--       → empresa forneceu serviços de campanha E recebeu emenda do mesmo parlamentar
--   tse_despesas.cpf_candidato × tse_receitas.cpf_candidato
--       → perfil completo: quanto captou × quanto gastou
--   tse_despesas.tipo_despesa GROUP BY sigla_partido
--       → padrão de gasto por partido (publicidade, pesquisa, marketing digital)
--
-- Estratégia de reload: delete-then-insert por ano_eleicao (dados estáticos pós-eleição)
-- RLS: leitura pública, escrita service_role.

CREATE TABLE IF NOT EXISTS public.tse_despesas (
  id                    BIGSERIAL     PRIMARY KEY,

  ano_eleicao           SMALLINT      NOT NULL,
  numero_documento      TEXT,                       -- NR_DOCUMENTO_DESPESA (dedup ref)

  -- candidato que gastou
  cpf_candidato         TEXT,
  nome_candidato        TEXT,
  cargo                 TEXT,
  sigla_partido         TEXT,
  uf                    CHAR(2),

  -- fornecedor do serviço / produto
  cpf_cnpj_fornecedor   TEXT,                       -- chave cruzamento emendas_favorecidos
  nome_fornecedor       TEXT,

  -- classificação da despesa
  tipo_despesa          TEXT,                       -- DS_TIPO_DESPESA (publicidade, pesquisa, etc.)
  descricao_despesa     TEXT,                       -- DS_DESPESA
  origem_despesa        TEXT,
  especie_recurso       TEXT,
  fonte_recurso         TEXT,

  -- valores
  valor_despesa         NUMERIC(16,2) NOT NULL,     -- VR_DESPESA_CONTRATADA
  valor_prestado        NUMERIC(16,2),              -- VR_DESPESA_PAGA

  -- data
  data_despesa          DATE,

  ingested_at           TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tse_desp_cpf_cand
  ON public.tse_despesas(cpf_candidato)
  WHERE cpf_candidato IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tse_desp_cnpj_fornecedor
  ON public.tse_despesas(cpf_cnpj_fornecedor)
  WHERE cpf_cnpj_fornecedor IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tse_desp_ano
  ON public.tse_despesas(ano_eleicao);

CREATE INDEX IF NOT EXISTS idx_tse_desp_tipo
  ON public.tse_despesas(tipo_despesa);

COMMENT ON TABLE public.tse_despesas IS
  'Despesas de campanha por candidato (TSE). '
  'cpf_cnpj_fornecedor cruza com emendas_favorecidos (fornecedor de campanha = favorecido de emenda). '
  'Reload completo por ano_eleicao (dados estáticos pós-eleição).';

-- ═══════════════════════════════════════════════════════════════════════════
-- VIEW: fornecedor de campanha que também é favorecido de emenda
-- "Empresa prestou serviço de campanha E recebeu emenda do mesmo parlamentar"
-- Complementa tse_v_doador_emenda (que cobre doadores → emendas)
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW public.tse_v_fornecedor_emenda AS
SELECT
  c.nome                                      AS parlamentar,
  c.sigla_partido,
  c.uf,
  c.ano_eleicao,
  d.nome_fornecedor                           AS empresa_fornecedora,
  d.cpf_cnpj_fornecedor                       AS cnpj_fornecedor,
  d.tipo_despesa,
  SUM(d.valor_despesa)                        AS total_gasto_campanha,
  SUM(ef.valor_recebido)                      AS total_emendas_recebidas,
  COUNT(DISTINCT ef.id)                       AS qtd_emendas
FROM public.tse_despesas d
JOIN public.tse_candidatos c
  ON d.cpf_candidato = c.cpf
  AND d.ano_eleicao  = c.ano_eleicao
JOIN public.parlamentares p
  ON p.cpf = c.cpf
JOIN public.emendas_favorecidos ef
  ON ef.codigo_autor::integer  = p.id_camara
  AND ef.codigo_favorecido     = d.cpf_cnpj_fornecedor
WHERE length(d.cpf_cnpj_fornecedor) = 14        -- apenas CNPJ
GROUP BY
  c.nome, c.sigla_partido, c.uf, c.ano_eleicao,
  d.nome_fornecedor, d.cpf_cnpj_fornecedor, d.tipo_despesa;

COMMENT ON VIEW public.tse_v_fornecedor_emenda IS
  'Empresas que prestaram serviços de campanha a um parlamentar e receberam emendas dele. '
  'Complementa tse_v_doador_emenda. Soma total gasto × total emendas por combinação.';

-- ═══════════════════════════════════════════════════════════════════════════
-- RLS
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE public.tse_despesas ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read_tse_despesas ON public.tse_despesas;
CREATE POLICY public_read_tse_despesas
  ON public.tse_despesas FOR SELECT USING (true);
