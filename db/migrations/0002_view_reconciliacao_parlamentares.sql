-- View de reconciliação: liga a dimensão-ator do pipeline de ATIVIDADE
-- legislativa (ale_parlamentares) ao pipeline de GASTOS por-casa
-- (<casa>_deputados), resolvendo a sobreposição de deputados decidida como
-- dívida técnica controlada (decisão 2026-05-28, Opção A).
--
-- Ponte por casa:
--   ALESP: ale_parlamentares.raw->>'Matricula'  =  alesp_deputados.matricula
-- Conforme novas casas com <casa>_deputados entrarem, somar LEFT JOINs/branches.

CREATE OR REPLACE VIEW public.ale_parlamentares_reconciliado AS
SELECT
  p.id                         AS ale_parlamentar_id,
  p.casa_id,
  p.nome,
  p.partido,
  p.slug,
  (p.raw ->> 'Matricula')      AS matricula,
  ad.matricula                 AS gastos_matricula,
  (ad.matricula IS NOT NULL)   AS tem_dados_gastos
FROM public.ale_parlamentares p
LEFT JOIN public.alesp_deputados ad
  ON p.casa_id = 'alesp'
 AND (p.raw ->> 'Matricula') = ad.matricula;

COMMENT ON VIEW public.ale_parlamentares_reconciliado IS
  'Reconcilia ale_parlamentares (atividade) com <casa>_deputados (gastos). '
  'Cobre ALESP via Matricula; estender por casa.';
