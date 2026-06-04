-- CVM — Processos Sancionadores
-- Fonte: dados.cvm.gov.br/dataset/processo-sancionador
-- Atualização diária. Dois CSVs: processo + acusado (join por NUP).
-- Criado em: 2026-06-04

-- ── Processos (cabeçalho) ──────────────────────────────────────────────────────
create table if not exists cvm_processos (
    nup                             text primary key,   -- ex: "19957000073202414"
    objeto                          text,
    ementa                          text,
    data_abertura                   date,
    componente_instrucao            text,
    fase_atual                      text,
    subfase_atual                   text,
    local_atual                     text,
    data_ultima_movimentacao        date,
    updated_at                      timestamptz default now()
);

create index if not exists cvm_processos_fase_idx on cvm_processos (fase_atual);
create index if not exists cvm_processos_abertura_idx on cvm_processos (data_abertura);

-- ── Acusados (uma linha por acusado × processo) ────────────────────────────────
create table if not exists cvm_acusados (
    id                  text primary key,   -- "<nup>_<nome_normalizado>"
    nup                 text not null references cvm_processos(nup),
    nome_acusado        text not null,
    -- nome normalizado para cruzamento (maiúsculas, sem pontuação)
    nome_normalizado    text generated always as (
        upper(regexp_replace(nome_acusado, '[^A-Za-zÀ-ÿ0-9 ]', '', 'g'))
    ) stored,
    situacao            text,
    data_situacao       date,
    updated_at          timestamptz default now()
);

create index if not exists cvm_acusados_nup_idx on cvm_acusados (nup);
create index if not exists cvm_acusados_nome_idx on cvm_acusados (nome_normalizado);

-- ── Log de ingestão ────────────────────────────────────────────────────────────
create table if not exists cvm_ingest_log (
    id          uuid primary key default gen_random_uuid(),
    dataset     text not null,
    status      text not null default 'running',
    n_processos integer,
    n_acusados  integer,
    erro        text,
    started_at  timestamptz default now(),
    finished_at timestamptz
);

-- ── View: acusados × emendas_favorecidos (match por nome normalizado) ──────────
-- Encontra favorecidos de emendas parlamentares que também são acusados em
-- processos sancionadores da CVM. Join fuzzy por razão social normalizada.
create or replace view cvm_cruzamento_emendas as
select
    a.nome_acusado                          as cvm_nome,
    a.situacao                              as cvm_situacao,
    p.fase_atual                            as cvm_fase,
    p.data_abertura                         as cvm_data_abertura,
    p.nup                                   as cvm_nup,
    f.cnpj_cpf                              as favorecido_cnpj,
    f.favorecido_nome                       as favorecido_nome,
    f.uf_favorecido                         as uf,
    sum(f.valor_repasse)                    as total_emendas,
    count(distinct f.autor_cpf)             as n_parlamentares,
    count(*)                                as n_transacoes
from cvm_acusados a
join cvm_processos p on p.nup = a.nup
join emendas_favorecidos f
    on upper(regexp_replace(f.favorecido_nome, '[^A-Za-zÀ-ÿ0-9 ]', '', 'g'))
       = a.nome_normalizado
group by
    a.nome_acusado, a.situacao, p.fase_atual,
    p.data_abertura, p.nup,
    f.cnpj_cpf, f.favorecido_nome, f.uf_favorecido
order by total_emendas desc;

comment on view cvm_cruzamento_emendas is
    'Favorecidos de emendas parlamentares que figuram como acusados em processos sancionadores da CVM. '
    'Join por nome normalizado — validar CNPJs manualmente antes de publicar.';
