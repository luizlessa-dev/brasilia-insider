-- =============================================================================
-- 0018_agenda_schema.sql
-- Agenda Legislativa — The BR Insider
-- Fontes:
--   1. Câmara dos Deputados — dadosabertos.camara.leg.br/api/v2/eventos
--   2. Senado Federal      — legis.senado.leg.br/dadosabertos
--      2a. Comissões:  /comissao/agenda/{ini}/{fim}.json
--      2b. Plenário:   /plenario/agenda/dia/{data}.json
--      2c. Votações:   /plenario/lista/votacao/{ini}/{fim}.json
-- Sem autenticação. Atualização diária via GHA.
-- Schema: agenda_*
-- =============================================================================

-- ---------------------------------------------------------------------------
-- agenda_camara_eventos
-- Eventos (reuniões, audiências, sessões) da Câmara dos Deputados.
-- Fonte: GET /api/v2/eventos — histórico desde 2013.
-- ---------------------------------------------------------------------------
create table if not exists agenda_camara_eventos (
    id                  text primary key,           -- id numérico da API

    -- Temporalidade
    data_hora_inicio    timestamptz,
    data_hora_fim       timestamptz,
    data_inicio_date    date,                       -- preenchido pelo conector (data_hora_inicio::date)

    -- Classificação
    tipo_evento_cod     integer,
    tipo_evento         text,                       -- "Reunião Deliberativa", "Audiência Pública", etc.
    situacao            text,                       -- "Agendada", "Encerrada", "Cancelada"
    descricao           text,                       -- pauta completa

    -- Local
    local_nome          text,
    local_predio        text,
    local_sala          text,
    local_andar         text,
    local_externo       text,                       -- endereço quando fora da Câmara

    -- Órgãos envolvidos (comissões, etc.)
    orgaos              jsonb,                      -- array [{id, sigla, nome, tipoOrgao}]
    orgaos_siglas       text[],                     -- para indexação e filtragem rápida

    -- Links
    url_documento_pauta text,
    url_registro        text,                       -- YouTube
    url_convite         text,

    -- Requerimentos
    requerimentos       jsonb,

    -- Metadados
    raw                 jsonb,
    ingested_at         timestamptz default now(),
    updated_at          timestamptz default now()
);

create index if not exists agenda_cam_data_idx   on agenda_camara_eventos (data_inicio_date);
create index if not exists agenda_cam_tipo_idx   on agenda_camara_eventos (tipo_evento);
create index if not exists agenda_cam_sit_idx    on agenda_camara_eventos (situacao);
create index if not exists agenda_cam_orgaos_idx on agenda_camara_eventos using gin (orgaos_siglas);

comment on table agenda_camara_eventos is
    'Eventos da Câmara dos Deputados (reuniões, audiências, sessões plenárias). '
    'Fonte: dadosabertos.camara.leg.br/api/v2/eventos. Histórico desde 2013.';

-- ---------------------------------------------------------------------------
-- agenda_senado_comissoes
-- Reuniões de comissões do Senado Federal.
-- Fonte: /dadosabertos/comissao/agenda/{ini}/{fim}.json
-- ---------------------------------------------------------------------------
create table if not exists agenda_senado_comissoes (
    id                  text primary key,           -- codigo da reunião (string)

    -- Temporalidade
    data_hora_inicio    timestamptz,
    data_inicio_date    date,                       -- preenchido pelo conector

    -- Identificação
    titulo              text,
    descricao           text,
    tipo_cod            text,
    tipo_desc           text,                       -- "Ordinária", "Extraordinária"

    -- Comissão
    comissao_codigo     text,
    comissao_sigla      text,
    comissao_nome       text,
    casa                text,                       -- "SF", "CN"

    -- Status
    confirmada          boolean,
    realizada           boolean,
    situacao            text,                       -- "Agendada", "Cancelada"

    -- Local e formato
    local               text,
    tipo_presenca       text,                       -- "Presencial", "Semipresencial", "Remoto"

    -- Links de pauta
    url_pauta_simples   text,
    url_pauta_completa  text,

    -- Partes / subeventos
    partes              jsonb,

    -- Metadados
    raw                 jsonb,
    ingested_at         timestamptz default now(),
    updated_at          timestamptz default now()
);

create index if not exists agenda_sen_com_data_idx on agenda_senado_comissoes (data_inicio_date);
create index if not exists agenda_sen_com_sig_idx  on agenda_senado_comissoes (comissao_sigla);
create index if not exists agenda_sen_com_sit_idx  on agenda_senado_comissoes (situacao);

comment on table agenda_senado_comissoes is
    'Reuniões de comissões do Senado Federal. '
    'Fonte: legis.senado.leg.br/dadosabertos/comissao/agenda. Limite: 1 mês/req.';

-- ---------------------------------------------------------------------------
-- agenda_senado_plenario
-- Sessões plenárias do Senado Federal e Congresso Nacional.
-- Fonte: /dadosabertos/plenario/agenda/dia/{YYYYMMDD}.json
-- ---------------------------------------------------------------------------
create table if not exists agenda_senado_plenario (
    id                  text primary key,           -- {data}_{tipo_sessao}_{seq}

    -- Temporalidade
    data_sessao         date not null,
    hora                text,

    -- Classificação
    tipo_sessao         text,                       -- "Sessão Especial", "Deliberativa"
    casa                text,                       -- "SF", "CN"
    local               text,

    -- Status
    situacao            text,                       -- "Agendada", "Realizada", "Cancelada"
    pauta_confirmada    boolean,
    tipo_presenca       text,

    -- Evento associado
    evento_tipo         text,
    evento_desc         text,
    origem_autor        text,
    requerimento        text,

    -- Oradores
    oradores            jsonb,

    -- Metadados
    raw                 jsonb,
    ingested_at         timestamptz default now(),
    updated_at          timestamptz default now()
);

create index if not exists agenda_sen_plen_data_idx on agenda_senado_plenario (data_sessao);
create index if not exists agenda_sen_plen_tipo_idx on agenda_senado_plenario (tipo_sessao);
create index if not exists agenda_sen_plen_sit_idx  on agenda_senado_plenario (situacao);

comment on table agenda_senado_plenario is
    'Sessões plenárias do Senado Federal e Congresso Nacional. '
    'Fonte: legis.senado.leg.br/dadosabertos/plenario/agenda/dia.';

-- ---------------------------------------------------------------------------
-- agenda_ingest_log
-- Histórico de execuções dos crons de agenda.
-- ---------------------------------------------------------------------------
create table if not exists agenda_ingest_log (
    id              uuid primary key default gen_random_uuid(),
    fonte           text not null,          -- "camara", "senado_comissoes", "senado_plenario"
    data_inicio     date,
    data_fim        date,
    status          text not null default 'running',    -- "ok", "erro", "parcial"
    n_inseridos     integer,
    n_atualizados   integer,
    n_erros         integer,
    erro_msg        text,
    started_at      timestamptz default now(),
    finished_at     timestamptz
);

-- ---------------------------------------------------------------------------
-- Views investigativas
-- ---------------------------------------------------------------------------

-- Agenda unificada dos últimos 7 dias (legislativo completo)
create or replace view agenda_legislativo_semana as
select
    'camara'            as casa,
    id,
    data_inicio_date    as data,
    data_hora_inicio,
    tipo_evento         as tipo,
    situacao,
    descricao,
    local_nome          as local,
    tipo_presenca       as null,
    orgaos_siglas       as orgaos,
    url_registro        as url_video
from agenda_camara_eventos
where data_inicio_date >= current_date - 7

union all

select
    'senado_comissao'   as casa,
    id,
    data_inicio_date    as data,
    data_hora_inicio,
    tipo_desc           as tipo,
    situacao,
    descricao,
    local,
    tipo_presenca,
    array[comissao_sigla] as orgaos,
    null                as url_video
from agenda_senado_comissoes
where data_inicio_date >= current_date - 7

union all

select
    'senado_plenario'   as casa,
    id,
    data_sessao         as data,
    (data_sessao::text || ' ' || coalesce(hora, '00:00'))::timestamptz as data_hora_inicio,
    tipo_sessao         as tipo,
    situacao,
    evento_desc         as descricao,
    local,
    tipo_presenca,
    null                as orgaos,
    null                as url_video
from agenda_senado_plenario
where data_sessao >= current_date - 7

order by data_hora_inicio;

comment on view agenda_legislativo_semana is
    'Agenda consolidada dos últimos 7 dias: Câmara + comissões Senado + plenário Senado.';

-- Audiências públicas (alto valor editorial)
create or replace view agenda_audiencias_publicas as
select
    'camara'            as casa,
    id,
    data_inicio_date    as data,
    data_hora_inicio,
    descricao,
    local_nome          as local,
    orgaos_siglas       as orgaos,
    situacao,
    url_documento_pauta as url_pauta
from agenda_camara_eventos
where tipo_evento ilike '%audiência pública%'
   or tipo_evento ilike '%audiencia publica%'
order by data_hora_inicio desc;

comment on view agenda_audiencias_publicas is
    'Todas as audiências públicas da Câmara — filtro editorial de alto valor.';
