-- Migration: tabela bulk de dívida ativa PGFN
-- Fonte: dadosabertos.pgfn.gov.br (trimestral)
-- 3 arquivos: Previdenciário, Não Previdenciário, FGTS
-- Ciclo: YYYY_trimestre_NN (ex: 2026_trimestre_01)

create table if not exists pgfn_divida_ativa (
  id                    bigint generated always as identity primary key,
  cpf_cnpj              text not null,
  tipo_pessoa           text,
  tipo_devedor          text,  -- Principal, Corresponsável, etc.
  nome_devedor          text,
  uf_devedor            text,
  unidade_responsavel   text,
  numero_inscricao      text,
  tipo_situacao         text,
  situacao              text,  -- Em cobrança, Suspensa, etc.
  tipo_credito          text,
  data_inscricao        date,
  indicador_ajuizado    text,  -- SIM / NAO
  valor_consolidado     numeric(18,2),
  arquivo               text,  -- previdenciario | nao_previdenciario | fgts
  ciclo                 text not null,  -- ex: 2026_trimestre_01
  created_at            timestamptz default now(),
  unique (numero_inscricao, ciclo)
);

create index if not exists idx_pgfn_cnpj_ciclo on pgfn_divida_ativa (cpf_cnpj, ciclo);
create index if not exists idx_pgfn_situacao   on pgfn_divida_ativa (situacao, ciclo);
