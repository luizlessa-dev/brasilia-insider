-- MG Fiscal — empenhos estaduais + log de ingestão
-- Fonte: dados.mg.gov.br (Portal de Dados Abertos de Minas Gerais / SEF-MG)
-- Criado em: 2026-06-04

-- ── Empenhos ──────────────────────────────────────────────────────────────────
create table if not exists mg_empenhos (
    id                              text primary key,   -- "mg_<ano>_<uo_cod>_<num_emp>"

    ano_exercicio                   integer not null,
    unidade_orcamentaria_codigo     integer,
    unidade_orcamentaria_sigla      text,
    unidade_orcamentaria_nome       text,

    ano_empenho                     integer,
    numero_empenho                  integer,
    data_registro                   date,
    numero_processo_compra          text,

    elemento_despesa_codigo         integer,
    elemento_despesa_descricao      text,
    item_despesa_codigo             integer,
    item_despesa_descricao          text,
    fonte_recurso_codigo            integer,
    fonte_recurso_descricao         text,

    -- chave de cruzamento com emendas_favorecidos (CNPJ/CPF sem formatação)
    razao_social_credor             text,
    cnpj_cpf_credor                 text,

    valor_empenhado                 numeric(18,2),
    valor_liquidado                 numeric(18,2),
    valor_pago                      numeric(18,2),

    updated_at                      timestamptz default now()
);

-- índices de cruzamento
create index if not exists mg_empenhos_cnpj_idx        on mg_empenhos (cnpj_cpf_credor);
create index if not exists mg_empenhos_ano_idx         on mg_empenhos (ano_exercicio);
create index if not exists mg_empenhos_elemento_idx    on mg_empenhos (elemento_despesa_codigo);
create index if not exists mg_empenhos_uo_idx          on mg_empenhos (unidade_orcamentaria_codigo);

-- ── Log de ingestão ────────────────────────────────────────────────────────────
create table if not exists mg_ingest_log (
    id          uuid primary key default gen_random_uuid(),
    dataset     text not null,
    status      text not null default 'running',   -- running | ok | erro
    n_gravados  integer,
    erro        text,
    started_at  timestamptz default now(),
    finished_at timestamptz
);

-- ── View: cruzamento empenhos MG × emendas federais por CNPJ ─────────────────
-- Mostra empresas que recebem tanto contratos/empenhos estaduais em MG
-- quanto recursos de emendas parlamentares federais.
-- Requer que emendas_favorecidos já exista (pipeline federal).
create or replace view mg_cruzamento_emendas as
select
    e.cnpj_cpf_credor                                   as cnpj,
    e.razao_social_credor                               as razao_social_mg,
    count(distinct e.id)                                as n_empenhos_mg,
    sum(e.valor_pago)                                   as total_pago_mg,
    count(distinct f.id)                                as n_transacoes_emendas_fed,
    sum(f.valor_pago)                                   as total_emendas_fed,
    array_agg(distinct f.autor_nome order by f.autor_nome) filter (
        where f.autor_nome is not null
    )                                                   as autores_emendas
from mg_empenhos e
join emendas_favorecidos f
    on regexp_replace(e.cnpj_cpf_credor,  '[^0-9]', '', 'g')
     = regexp_replace(f.codigo_favorecido, '[^0-9]', '', 'g')
group by e.cnpj_cpf_credor, e.razao_social_credor
order by total_pago_mg desc nulls last;

comment on view mg_cruzamento_emendas is
    'Empresas que recebem empenhos estaduais MG E emendas parlamentares federais. '
    'Chave de join: CNPJ normalizado (só dígitos).';

-- ── Contratos MG ──────────────────────────────────────────────────────────────
-- Adicionado em 2026-06-05
create table if not exists mg_contratos (
    id                          text primary key,   -- "mg_ct_<ano>_<numero_contrato>"

    ano_assinatura              integer,
    codigo_orgao                text,
    nome_orgao                  text,

    cnpj_cpf_fornecedor         text,               -- chave de cruzamento
    nome_fornecedor             text,
    tipo_pessoa                 text,

    numero_processo             text,
    numero_contrato             text,
    situacao                    text,
    tipo_contrato               text,
    objeto                      text,

    data_assinatura             date,
    data_inicio_vigencia        date,
    data_termino_vigencia       date,

    procedimento_contratacao    text,
    procedimento_detalhamento   text,

    valor_total                 numeric(18,2),
    valor_empenhado             numeric(18,2),
    valor_liquidado             numeric(18,2),

    updated_at                  timestamptz default now()
);

create index if not exists mg_contratos_cnpj_idx      on mg_contratos (cnpj_cpf_fornecedor);
create index if not exists mg_contratos_ano_idx       on mg_contratos (ano_assinatura);
create index if not exists mg_contratos_orgao_idx     on mg_contratos (codigo_orgao);
create index if not exists mg_contratos_proc_idx      on mg_contratos (procedimento_contratacao);
