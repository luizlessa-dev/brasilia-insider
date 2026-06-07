-- =============================================================================
-- 0019_agenda_executivo_schema.sql
-- Agenda do Poder Executivo Federal — The BR Insider
-- Fonte: e-Agendas (CGU) — eagendas.cgu.gov.br/api/v2
-- Obrigatoriedade: Decreto nº 10.889/2021
-- Cobertura: ministros + cúpula do Executivo (PR, VPR, Casa Civil, etc.)
-- Autenticação: Bearer token pessoal (variável EAGENDAS_TOKEN no GHA)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- agenda_executivo_compromissos
-- Compromissos dos ministros e cúpula do Poder Executivo Federal.
-- Granularidade: 1 linha = 1 compromisso de 1 autoridade.
-- ---------------------------------------------------------------------------
create table if not exists agenda_executivo_compromissos (
    id                          text primary key,   -- id numérico do e-Agendas (string)

    -- Classificação
    tipo_compromisso            text,               -- "Audiência", "Reunião", "Evento", "Viagem", etc.
    assunto                     text,
    detalhamento                text,
    local                       text,
    objetivos                   text,               -- concatenação de objetivos_compromisso[].descricao

    -- Órgão e autoridade responsável
    orgao_id                    integer,
    orgao_sigla                 text,               -- "MEC", "MF", "PR", etc.
    autoridade_nome             text,               -- nome do ministro/presidente
    autoridade_cargo            text,               -- "MINISTRO DE ESTADO DA EDUCAÇÃO"
    apo_id                      integer,            -- ID do agente no e-Agendas

    -- Temporalidade
    data_inicio                 date,
    data_termino                date,
    hora_inicio                 text,
    hora_termino                text,

    -- Participantes (alto valor investigativo)
    tem_participantes_privados  boolean default false,
    n_participantes_privados    integer default 0,
    participantes_publicos      jsonb,              -- [{apo_id, nome, cargo, orgao, tipo_participacao}]
    participantes_privados      jsonb,              -- [{nome, cnpj/cpf, representado}] — setor privado
    representantes              jsonb,

    -- Metadados de publicação
    publicado_em                text,
    ultima_atualizacao          text,

    -- Raw + ingestão
    raw                         jsonb,
    ingested_at                 timestamptz default now(),
    updated_at                  timestamptz default now()
);

-- Índices
create index if not exists agex_data_idx    on agenda_executivo_compromissos (data_inicio);
create index if not exists agex_orgao_idx   on agenda_executivo_compromissos (orgao_sigla);
create index if not exists agex_tipo_idx    on agenda_executivo_compromissos (tipo_compromisso);
create index if not exists agex_priv_idx    on agenda_executivo_compromissos (tem_participantes_privados)
    where tem_participantes_privados = true;
create index if not exists agex_apo_idx     on agenda_executivo_compromissos (apo_id);

comment on table agenda_executivo_compromissos is
    'Compromissos dos ministros e cúpula do Executivo Federal (e-Agendas/CGU). '
    'Decreto nº 10.889/2021 — publicação obrigatória em até 10 dias. '
    'Cobertura: PR, VPR, Casa Civil + 37 ministérios.';

-- ---------------------------------------------------------------------------
-- Views investigativas
-- ---------------------------------------------------------------------------

-- Audiências com setor privado (maior valor editorial)
create or replace view agenda_ministerial_setor_privado as
select
    id,
    data_inicio,
    hora_inicio,
    orgao_sigla,
    autoridade_nome,
    autoridade_cargo,
    tipo_compromisso,
    assunto,
    local,
    n_participantes_privados,
    participantes_privados,
    publicado_em,
    ultima_atualizacao
from agenda_executivo_compromissos
where tem_participantes_privados = true
order by data_inicio desc, orgao_sigla;

comment on view agenda_ministerial_setor_privado is
    'Compromissos do Executivo com representantes do setor privado — '
    'insumo primário para investigação de lobby e captura regulatória.';

-- Agenda semanal consolidada por ministério
create or replace view agenda_ministerial_semana as
select
    orgao_sigla,
    autoridade_nome,
    data_inicio,
    hora_inicio,
    tipo_compromisso,
    assunto,
    local,
    tem_participantes_privados
from agenda_executivo_compromissos
where data_inicio >= current_date - 7
order by data_inicio, orgao_sigla, hora_inicio;

comment on view agenda_ministerial_semana is
    'Agenda da semana por ministério — visão editorial rápida.';

-- Agenda completa unificada (Executivo + Legislativo)
create or replace view agenda_federal_completa as
-- Executivo (e-Agendas)
select
    'executivo'         as poder,
    orgao_sigla         as orgao,
    data_inicio::timestamptz + (
        case when hora_inicio ~ '^\d{2}:\d{2}$'
        then hora_inicio::interval else '00:00'::interval end
    )                   as data_hora,
    data_inicio,
    tipo_compromisso    as tipo,
    assunto             as descricao,
    autoridade_nome     as responsavel,
    local,
    tem_participantes_privados as envolve_privado
from agenda_executivo_compromissos
where data_inicio >= current_date - 30

union all

-- Câmara dos Deputados
select
    'legislativo_camara' as poder,
    coalesce(array_to_string(orgaos_siglas, '/'), 'PLEN') as orgao,
    data_hora_inicio,
    data_inicio_date    as data_inicio,
    tipo_evento         as tipo,
    descricao,
    null                as responsavel,
    local_nome          as local,
    false               as envolve_privado
from agenda_camara_eventos
where data_inicio_date >= current_date - 30

union all

-- Senado — Comissões
select
    'legislativo_senado' as poder,
    comissao_sigla       as orgao,
    data_hora_inicio,
    data_inicio_date     as data_inicio,
    tipo_desc            as tipo,
    descricao,
    null                 as responsavel,
    local,
    false                as envolve_privado
from agenda_senado_comissoes
where data_inicio_date >= current_date - 30

union all

-- Senado — Plenário
select
    'legislativo_senado_plenario' as poder,
    casa                 as orgao,
    (data_sessao::text || ' ' || coalesce(hora, '00:00'))::timestamptz as data_hora,
    data_sessao          as data_inicio,
    tipo_sessao          as tipo,
    evento_desc          as descricao,
    null                 as responsavel,
    local,
    false                as envolve_privado
from agenda_senado_plenario
where data_sessao >= current_date - 30

order by data_hora;

comment on view agenda_federal_completa is
    'Agenda unificada dos 3 poderes (Executivo + Câmara + Senado) — últimos 30 dias. '
    'Base para o feed de agenda do BR Insider.';
