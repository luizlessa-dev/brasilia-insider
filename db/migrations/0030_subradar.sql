-- =============================================================
-- SUBRADAR — schema de produto sobre a infraestrutura BR Insider
-- Todas as tabelas prefixadas com sub_
-- =============================================================

-- ------------------------------------------------------------
-- 1. CLIENTES
-- Escritórios e departamentos cadastrados no Subradar
-- ------------------------------------------------------------
create table if not exists sub_clientes (
  id            uuid primary key default gen_random_uuid(),
  nome          text not null,
  email         text not null unique,
  empresa       text,
  plano         text not null default 'starter'
                  check (plano in ('trial','starter','profissional','enterprise')),
  status        text not null default 'trial'
                  check (status in ('trial','ativo','pausado','cancelado')),
  max_cnpjs     int  not null default 3,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

-- ------------------------------------------------------------
-- 2. CNPJs MONITORADOS
-- Um cliente pode monitorar N CNPJs (limitado pelo plano)
-- ------------------------------------------------------------
create table if not exists sub_cnpjs_monitorados (
  id            uuid primary key default gen_random_uuid(),
  cliente_id    uuid not null references sub_clientes(id) on delete cascade,
  cnpj          text not null,           -- formato: 00.000.000/0000-00
  razao_social  text,
  ativo         boolean not null default true,
  created_at    timestamptz default now(),
  unique (cliente_id, cnpj)
);

-- ------------------------------------------------------------
-- 3. DOSSIÊS
-- Um dossiê por CNPJ por ciclo mensal (formato: 'YYYY-MM')
-- ------------------------------------------------------------
create table if not exists sub_dossies (
  id            uuid primary key default gen_random_uuid(),
  cliente_id    uuid not null references sub_clientes(id),
  cnpj          text not null,
  razao_social  text,
  ciclo         text not null,           -- ex: '2026-06'
  score_num     int  not null default 0  check (score_num between 0 and 100),
  score_texto   text not null default 'baixo'
                  check (score_texto in ('baixo','medio','alto','critico')),
  total_alertas int  not null default 0,
  status        text not null default 'gerado'
                  check (status in ('gerado','enviado','lido')),
  pdf_url       text,
  generated_at  timestamptz default now(),
  sent_at       timestamptz,
  unique (cliente_id, cnpj, ciclo)
);

-- ------------------------------------------------------------
-- 4. ALERTAS
-- Achados individuais dentro de cada dossiê
-- Cada linha = um evento/ocorrência numa fonte específica
-- ------------------------------------------------------------
create table if not exists sub_alertas (
  id             uuid primary key default gen_random_uuid(),
  dossie_id      uuid not null references sub_dossies(id) on delete cascade,
  cnpj           text not null,
  ciclo          text not null,

  -- origem do dado
  fonte          text not null,          -- pncp | ceis | cnep | cepim | pep | emenda |
                                         -- tcu | dou | trf | cgupd | rfb_divida |
                                         -- bndes | ibama | aneel | anatel | opensanctions |
                                         -- leniencia | jucae

  -- classificação
  categoria      text not null,          -- contrato | sancao | pep | emenda |
                                         -- societario | judicial | divida | regulatorio | internacional
  severidade     text not null           -- critico | atencao | ok | info
                  check (severidade in ('critico','atencao','ok','info')),

  -- conteúdo
  titulo         text not null,
  descricao      text,
  valor_brl      numeric(18,2),
  contraparte    text,                   -- órgão, parlamentar, tribunal etc.
  data_evento    date,

  -- rastreabilidade
  referencia_id  text,                   -- ID externo na fonte original
  url_fonte      text,

  -- delta: true = apareceu pela primeira vez neste ciclo
  is_novo        boolean not null default true,

  created_at     timestamptz default now()
);

-- ------------------------------------------------------------
-- 5. SNAPSHOTS
-- Hash do estado de cada CNPJ por fonte por ciclo.
-- Usado para calcular o delta (o que mudou vs. mês anterior).
-- ------------------------------------------------------------
create table if not exists sub_snapshots (
  id          uuid primary key default gen_random_uuid(),
  cnpj        text not null,
  ciclo       text not null,
  fonte       text not null,
  hash_dados  text not null,             -- SHA-256 do conteúdo serializado
  dados       jsonb,                     -- payload bruto para auditoria
  created_at  timestamptz default now(),
  unique (cnpj, ciclo, fonte)
);

-- ------------------------------------------------------------
-- 6. LOG DE ENVIOS
-- Histórico de emails/PDFs entregues por dossiê
-- ------------------------------------------------------------
create table if not exists sub_envios (
  id          uuid primary key default gen_random_uuid(),
  dossie_id   uuid not null references sub_dossies(id),
  cliente_id  uuid not null references sub_clientes(id),
  canal       text not null default 'email' check (canal in ('email','pdf','api')),
  destinatario text not null,
  status      text not null default 'enviado' check (status in ('enviado','falhou','abriu')),
  enviado_at  timestamptz default now()
);

-- ------------------------------------------------------------
-- ÍNDICES
-- ------------------------------------------------------------
create index if not exists idx_sub_cnpjs_cliente    on sub_cnpjs_monitorados (cliente_id);
create index if not exists idx_sub_cnpjs_cnpj       on sub_cnpjs_monitorados (cnpj);
create index if not exists idx_sub_dossies_ciclo    on sub_dossies (ciclo);
create index if not exists idx_sub_dossies_cnpj     on sub_dossies (cnpj);
create index if not exists idx_sub_alertas_dossie   on sub_alertas (dossie_id);
create index if not exists idx_sub_alertas_cnpj     on sub_alertas (cnpj, ciclo);
create index if not exists idx_sub_alertas_sev      on sub_alertas (severidade);
create index if not exists idx_sub_snapshots_lookup on sub_snapshots (cnpj, fonte, ciclo);

-- ------------------------------------------------------------
-- VIEW: resumo por cliente (útil para dashboard interno)
-- ------------------------------------------------------------
create or replace view sub_v_resumo_clientes as
select
  c.id,
  c.nome,
  c.empresa,
  c.plano,
  c.status,
  count(distinct m.cnpj)               as cnpjs_ativos,
  c.max_cnpjs,
  count(distinct d.id)                 as total_dossies,
  max(d.generated_at)                  as ultimo_dossie,
  sum(case when a.severidade = 'critico' then 1 else 0 end) as alertas_criticos
from sub_clientes c
left join sub_cnpjs_monitorados m  on m.cliente_id = c.id and m.ativo
left join sub_dossies d            on d.cliente_id = c.id
left join sub_alertas a            on a.dossie_id  = d.id
group by c.id, c.nome, c.empresa, c.plano, c.status, c.max_cnpjs;

-- ------------------------------------------------------------
-- VIEW: alertas críticos do ciclo corrente (para triagem)
-- ------------------------------------------------------------
create or replace view sub_v_criticos_mes as
select
  a.cnpj,
  d.razao_social,
  c.nome        as cliente,
  c.email       as email_cliente,
  a.fonte,
  a.titulo,
  a.descricao,
  a.valor_brl,
  a.data_evento,
  a.url_fonte,
  d.ciclo
from sub_alertas a
join sub_dossies d  on d.id = a.dossie_id
join sub_clientes c on c.id = d.cliente_id
where a.severidade = 'critico'
  and d.ciclo = to_char(now(), 'YYYY-MM')
order by a.valor_brl desc nulls last;
