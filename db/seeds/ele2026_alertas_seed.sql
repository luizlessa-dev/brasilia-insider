-- The BR Insider — Seed inicial: ele2026_alertas
-- Candidatos de interesse editorial para monitoramento nas eleições 2026.
--
-- Fonte dos dados: dossiê de investigação de emendas parlamentares (jun/2026)
--   /brasilia-insider/editorial/brief_emendas_rede_parlamentar.md
--   /brasilia-insider/editorial/briefing-sancoes-emendas-jun2026.md
--
-- CPF: deixado NULL aqui — será preenchido automaticamente pelo conector
--   na ingestão de candidatos via JOIN com parlamentares.cpf.
--
-- Metodologia de priorização:
--   ALTA    → valor > R$ 1M ou irregularidade formal documentada
--   MÉDIA   → valor R$ 200k–R$ 1M ou participação em ecossistema confirmado
--   BAIXA   → participação periférica ou dado pendente de verificação
--
-- Atualizar emenda_total_hist executando:
--   SELECT cpf, SUM(ef.valor_recebido) FROM parlamentares p
--   JOIN emendas_favorecidos ef ON ef.codigo_autor::int = p.id_camara
--   WHERE ... GROUP BY cpf
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ═════════════════════════════════════════════════════════════════════════════
-- ECOSSISTEMA 1 — MAQUINÁRIO ESTRANGEIRO (XCMG / LiuGong / Yanmar)
-- Total investigado: ~R$ 450M | Referência: XCMG CNPJ 14707364000110
-- ═════════════════════════════════════════════════════════════════════════════

INSERT INTO public.ele2026_alertas
  (nome, uf, cargo_interesse, motivos, descricao, emenda_total_hist, tem_sancao)
VALUES

-- Roberta Roma — maior individual XCMG (BA)
(
  'Roberta Roma',
  'BA', 'Deputada Federal',
  ARRAY['emenda_xcmg','investigacao_ativa'],
  'Maior autora individual XCMG: R$ 3,2M (2025). Ecossistema maquinário estrangeiro. '
  'XCMG Brasil é a #1 receptora privada de emendas de toda a base (R$ 311M de 66 autores). '
  'Partido: PL/BA. Verificar candidatura a dep. federal ou estadual em 2026.',
  3200000.00, false
),

-- Diego Coronel — PP/RS, R$ 1,9M XCMG
(
  'Diego Coronel',
  'RS', 'Deputado Federal',
  ARRAY['emenda_xcmg','investigacao_ativa'],
  'R$ 1,9M para XCMG (2024-2025). RS é a principal bancada XCMG (R$ 102M). '
  'Anomalia: bancada gaúcha = 1/3 de toda receita federal da empresa chinesa. '
  'Partido: PP/RS.',
  1900000.00, false
),

-- Carlos Viana — Mobiliza/MG, R$ 1,5M XCMG + camada estadual MG
(
  'Carlos Viana',
  'MG', 'Deputado Federal',
  ARRAY['emenda_xcmg','investigacao_ativa','camada_estadual_mg'],
  'R$ 1,5M para XCMG federal. MG tem adicionalmente R$ 25M em empenhos estaduais '
  '(Secretaria Agricultura + IDENE). LAIs protocoladas em 05/06/2026 '
  '(01230.000104/2026-11 e 02420.000088/2026-91). Partido: Mobiliza/MG.',
  1500000.00, false
),

-- Aécio Neves — PSDB/MG, senador, R$ 724k XCMG
(
  'Aécio Neves',
  'MG', 'Senador',
  ARRAY['emenda_xcmg'],
  'R$ 724k para XCMG (2024). Senador PSDB/MG. '
  'Relevante para cobertura de renovação do mandato no Senado em 2026.',
  724000.00, false
),

-- Sergio Moro — Podemos/PR, senador, R$ 680k XCMG
(
  'Sergio Moro',
  'PR', 'Senador',
  ARRAY['emenda_xcmg'],
  'R$ 680k para XCMG (2025). Senador Podemos/PR. '
  'Candidatura presidencial 2026 em aberto — movimentação de campanha de alto interesse.',
  680000.00, false
),

-- Jaques Wagner — PT/BA, senador, R$ 622k XCMG
(
  'Jaques Wagner',
  'BA', 'Senador',
  ARRAY['emenda_xcmg'],
  'R$ 622k para XCMG (2025). Senador PT/BA. '
  'Bancada da BA = R$ 16M para XCMG + R$ 10,3M para LiuGong (total R$ 26,4M).',
  622000.00, false
),

-- Glauber Braga — PSOL/RJ, dep. federal
(
  'Glauber Braga',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_xcmg','emenda_5g'],
  'R$ 90k para XCMG (2026) + aparece no ecossistema 5G (Trindade/GO). '
  'PSOL/RJ. Candidatura a governador RJ em 2026 cogitada.',
  90000.00, false
),

-- Efraim Filho — União/PB, senador, R$ 2,6M duopólio chinês
(
  'Efraim Filho',
  'PB', 'Senador',
  ARRAY['emenda_xcmg','emenda_liugong','duopolio_chines'],
  'R$ 1,39M XCMG + R$ 1,24M LiuGong = R$ 2,63M total. '
  'Financiou os dois concorrentes do duopólio chinês simultaneamente. União Brasil/PB. '
  'Um dos 11 autores que financiaram XCMG e LiuGong ao mesmo tempo.',
  2630000.00, false
),

-- Paulo Abi-Ackel — R$ 1,2M duopólio
(
  'Paulo Abi-Ackel',
  'MG', 'Deputado Federal',
  ARRAY['emenda_xcmg','emenda_liugong','duopolio_chines'],
  'R$ 908k XCMG + R$ 307k LiuGong = R$ 1,21M. '
  'Um dos 11 autores financiando duopólio chinês simultaneamente.',
  1210000.00, false
),

-- Claudio Cajado — R$ 1,3M duopólio
(
  'Claudio Cajado',
  'BA', 'Deputado Federal',
  ARRAY['emenda_xcmg','emenda_liugong','duopolio_chines'],
  'R$ 962k XCMG + R$ 315k LiuGong = R$ 1,28M. '
  'Um dos 11 autores financiando duopólio chinês. Partido: PP/BA.',
  1280000.00, false
),

-- ═════════════════════════════════════════════════════════════════════════════
-- ECOSSISTEMA 2 — FRACIONAMENTO 5G (Trindade/GO)
-- PROVA: mesma emenda 202571090001 pagou os dois CNPJs simultaneamente
-- ═════════════════════════════════════════════════════════════════════════════

-- Vanderlan Cardoso — PL/GO, financiou 5G em seu próprio estado
(
  'Vanderlan Cardoso',
  'GO', 'Senador',
  ARRAY['emenda_5g','fracionamento_cnpj'],
  'Financiou esquema 5G (Trindade/GO): empresa no seu estado com fracionamento '
  'comprovado de CNPJ. Emenda 202571090001 pagou dois CNPJs da mesma operação. '
  'PL/GO. Mandato senatorial — monitorar candidatura 2026.',
  NULL, false
),

-- Tarcísio Motta — PSOL/RJ, aparece em 5G e ecossistema FIOTEC/UFRJ
(
  'Tarcísio Motta',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_5g','emenda_fiotec','concentracao_portfolio'],
  '44,7% do portfólio de emendas vai para ecossistema FIOTEC/UFRJ (R$ 23,2M). '
  'Aparece também no ecossistema 5G. PSOL/RJ. '
  'FIOTEC → subcontrata Tamandaré Informática + Datamed.',
  23200000.00, false
),

-- Ivan Valente — PSOL/SP, 5G + empresa sancionada MANUPA
(
  'Ivan Valente',
  'SP', 'Deputado Federal',
  ARRAY['emenda_5g','emenda_empresa_sancionada'],
  'Aparece no ecossistema 5G (Trindade/GO) + pagou R$ 403.220 à MANUPA '
  '(CNPJ 03093776000191, no CNEP desde 01/10/2020 sem prazo de encerramento). '
  'PSOL/SP.',
  NULL, true
),

-- Sâmia Bomfim — PSOL/SP, 5G + duas empresas em recuperação judicial
(
  'Sâmia Bomfim',
  'SP', 'Deputada Federal',
  ARRAY['emenda_5g','emenda_empresa_recovery','irregularidade_formal'],
  'Aparece no ecossistema 5G + pagou para duas empresas em recuperação judicial: '
  'ProvAC Terceirização (CNPJ em recovery, R$ 2,18M partilhada) e '
  'Eletrodata Engenharia (em recovery). '
  'Irregularidade documentável sem necessidade de apuração adicional. PSOL/SP.',
  NULL, false
),

-- ═════════════════════════════════════════════════════════════════════════════
-- ECOSSISTEMA 3 — OSCs PRIVADAS DO RIO DE JANEIRO
-- Cluster esportivo: R$ 81,8M | Instituto Taiwan: R$ 38M
-- ═════════════════════════════════════════════════════════════════════════════

-- Romário — PL/RJ, senador, cluster esportivo
(
  'Romário',
  'RJ', 'Senador',
  ARRAY['emenda_osc_esportiva_rj','cluster_esportivo'],
  'R$ 9,75M para Pró Esporte + Bem Viver (cluster de 7 parlamentares, R$ 81,8M total). '
  'OSCs sem prestação de contas verificada. '
  'Senador PL/RJ. Ex-jogador de futebol — padrão com Bebeto e Marcos Tavares.',
  9750000.00, false
),

-- Bebeto — SD/RJ, cluster esportivo
(
  'Bebeto',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_osc_esportiva_rj','cluster_esportivo'],
  'R$ 7M para Pró Esporte + Bem Viver. '
  'Ex-jogador de futebol com mandato parlamentar — padrão com Romário e Marcos Tavares. '
  'Partido: Solidariedade/RJ.',
  7000000.00, false
),

-- Marcos Tavares — PSD/RJ, cluster esportivo
(
  'Marcos Tavares',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_osc_esportiva_rj','cluster_esportivo'],
  'R$ 8,4M para Pró Esporte + Bem Viver. '
  'Ex-jogador de futebol com mandato parlamentar. PSD/RJ.',
  8400000.00, false
),

-- Hugo Leal — PSD/RJ, maior individual do cluster
(
  'Hugo Leal',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_osc_esportiva_rj','cluster_esportivo'],
  'R$ 11,4M para Pró Esporte + Bem Viver — maior individual do cluster. '
  'PSD/RJ.',
  11400000.00, false
),

-- Sostenes Cavalcante — PL/RJ, cluster esportivo
(
  'Sostenes Cavalcante',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_osc_esportiva_rj','cluster_esportivo'],
  'R$ 8,5M para Pró Esporte + Bem Viver. PL/RJ.',
  8500000.00, false
),

-- Sargento Portugal — PSD/RJ, cluster esportivo
(
  'Sargento Portugal',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_osc_esportiva_rj','cluster_esportivo'],
  'R$ 7,3M para Pró Esporte + Bem Viver. PSD/RJ.',
  7300000.00, false
),

-- ═════════════════════════════════════════════════════════════════════════════
-- ECOSSISTEMA 4 — SAÚDE/UFRJ (PT/PSOL)
-- Concentração anômala: 40-45% do portfólio individual em FIOTEC/UFRJ
-- ═════════════════════════════════════════════════════════════════════════════

-- Pastor Henrique Vieira — PSOL/RJ
(
  'Pastor Henrique Vieira',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_fiotec','concentracao_portfolio'],
  '42,4% do portfólio (R$ 24,9M) concentrado em FIOTEC/UFRJ. '
  'FIOTEC subcontrata Tamandaré Informática + Datamed. PSOL/RJ.',
  24900000.00, false
),

-- Erika Kokay — PT/DF
(
  'Erika Kokay',
  'DF', 'Deputada Federal',
  ARRAY['emenda_fiotec','concentracao_portfolio'],
  '40,1% do portfólio (R$ 24,7M) concentrado em FIOTEC/UFRJ. '
  'Padrão idêntico ao de Tarcísio Motta e Pastor Henrique Vieira. PT/DF.',
  24700000.00, false
),

-- Lindbergh Farias — PT/RJ, senador
(
  'Lindbergh Farias',
  'RJ', 'Senador',
  ARRAY['emenda_fiotec','concentracao_portfolio','anomalia_geografica'],
  '44,2% do portfólio mapeado (R$ 37,9M total): '
  'FIOTEC R$ 20,7M + Instituto BR Arte/CE R$ 11M + SOFTEX R$ 6M. '
  'Anomalia: senador do RJ mandando R$ 11M para Ceará (Instituto BR Arte). '
  'Senador PT/RJ — candidatura presidencial 2026 especulada.',
  37900000.00, false
),

-- ═════════════════════════════════════════════════════════════════════════════
-- ECOSSISTEMA 5 — IRREGULARIDADES FORMAIS (empresas em recuperação judicial)
-- ═════════════════════════════════════════════════════════════════════════════

-- Arthur Oliveira Maia — BA, Tratormaster (maior caso, R$ 8,74M)
(
  'Arthur Oliveira Maia',
  'BA', 'Deputado Federal',
  ARRAY['emenda_empresa_recovery','irregularidade_formal'],
  'Co-autor com Bancada BA + Bancada SE de R$ 8,74M para Tratormaster '
  '(CNPJs 02745179000131 e 02745179000212) — empresa em recuperação judicial. '
  'Padrão sugere coordenação entre bancadas de dois estados para mesma empresa.',
  8740000.00, false
),

-- Hamilton Mourão — Republicanos/RS, senador, FATEC em recovery
(
  'Hamilton Mourão',
  'RS', 'Senador',
  ARRAY['emenda_empresa_recovery','irregularidade_formal'],
  'Co-autor de emenda para FATEC em Recuperação Judicial (RS): R$ 3,95M '
  'junto com Melchionna (PSOL) e Maria do Rosário (PT). '
  'Ex-vice-presidente. Senador Republicanos/RS.',
  NULL, false
),

-- Luiza Erundina — PSOL/SP, duas empresas em recovery na mesma emenda
(
  'Luiza Erundina',
  'SP', 'Deputada Federal',
  ARRAY['emenda_empresa_recovery','irregularidade_formal'],
  'Emenda inclui duas empresas em recuperação judicial no mesmo instrumento: '
  'ProvAC Terceirização + Eletrodata Engenharia. '
  'Irregularidade documentável sem apuração adicional. PSOL/SP.',
  NULL, false
),

-- Rogério Correia — PT/MG, recovery + empresa sancionada MANUPA
(
  'Rogério Correia',
  'MG', 'Deputado Federal',
  ARRAY['emenda_empresa_recovery','emenda_empresa_sancionada'],
  'Pagou para empresa em recuperação judicial + R$ 221.358 para MANUPA '
  '(CNPJ 03093776000191, no CNEP desde 2020 sem prazo). PT/MG.',
  NULL, true
),

-- ═════════════════════════════════════════════════════════════════════════════
-- SANÇÕES CEIS/CNEP: parlamentares que pagaram durante vigência de sanção
-- Fonte: briefing-sancoes-emendas-jun2026.md
-- ═════════════════════════════════════════════════════════════════════════════

-- Fernando Coelho Filho — PP/PE, maior pagador à empresa sancionada (Caso 1)
(
  'Fernando Coelho Filho',
  'PE', 'Deputado Federal',
  ARRAY['pagamento_empresa_sancionada','sancao_ceis','caso_comercial_licita'],
  'R$ 853.380 para Comercial Licita Máquinas (CNPJ 15513036000146) DURANTE '
  'vigência do impedimento CEIS (13/03/2026–10/06/2026). '
  'R$ 2.146.620 pagos também em fev/2026 (pré-sanção). PP/PE.',
  NULL, false
),

-- Humberto Costa — PT/PE, senador, Caso 1
(
  'Humberto Costa',
  'PE', 'Senador',
  ARRAY['pagamento_empresa_sancionada','sancao_ceis','caso_comercial_licita'],
  'R$ 338.238 para Comercial Licita Máquinas durante impedimento CEIS. '
  'Senador PT/PE.',
  NULL, false
),

-- Patrus Ananias — PT/MG, Caso 1
(
  'Patrus Ananias',
  'MG', 'Deputado Federal',
  ARRAY['pagamento_empresa_sancionada','sancao_ceis','caso_comercial_licita'],
  'R$ 151.368 para Comercial Licita Máquinas durante impedimento CEIS (mar-mai/2026). '
  'PT/MG.',
  NULL, false
),

-- Mauricio do Volei — PSD/ES, Caso 2 MANUPA
(
  'Mauricio do Volei',
  'ES', 'Deputado Federal',
  ARRAY['emenda_empresa_sancionada','sancao_cnep','caso_manupa'],
  'R$ 474.000 para MANUPA (CNPJ 03093776000191, CNEP desde out/2020 sem prazo). '
  'Empresa acumula 4 sanções CEIS/CNEP. PSD/ES.',
  474000.00, false
),

-- Jader Barbalho Filho — MDB/PA, Caso 2 MANUPA
(
  'Jader Barbalho',
  'PA', 'Deputado Federal',
  ARRAY['emenda_empresa_sancionada','sancao_cnep','caso_manupa'],
  'R$ 280.087 para MANUPA (CNEP desde 2020). MDB/PA. '
  'Verificar: Jader Filho (pai, senador) × Jader Barbalho Filho (dep. federal).',
  280000.00, false
),

-- Chris Tonietto — PL/RJ, Caso 2 MANUPA
(
  'Chris Tonietto',
  'RJ', 'Deputada Federal',
  ARRAY['emenda_empresa_sancionada','sancao_cnep','caso_manupa'],
  'R$ 278.490 para MANUPA (dez/2024). CNEP desde 2020. PL/RJ.',
  278490.00, false
),

-- Hélio Lopes — PL/RJ, Caso 2 MANUPA
(
  'Hélio Lopes',
  'RJ', 'Deputado Federal',
  ARRAY['emenda_empresa_sancionada','sancao_cnep','caso_manupa'],
  'R$ 278.490 para MANUPA (dez/2024). CNEP desde 2020. '
  'Mesmo valor que Chris Tonietto — verificar se é emenda compartilhada. PL/RJ.',
  278490.00, false
),

-- Dani Cunha — UB/RJ, Federação de Motociclismo
(
  'Dani Cunha',
  'RJ', 'Deputada Federal',
  ARRAY['concentracao_federacao_esportiva'],
  'R$ 4,3M para Federação de Motociclismo RJ. '
  'Dep. federal (UB/RJ) mandando R$ 4,3M para federação estadual esportiva. '
  'Volume atípico para o tipo de entidade.',
  4300000.00, false
);

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────
-- Verificação pós-insert
-- ─────────────────────────────────────────────────────────────────────────────
-- SELECT
--   uf,
--   cargo_interesse,
--   COUNT(*)                        AS n_alertas,
--   SUM(emenda_total_hist)          AS volume_total_mapeado
-- FROM public.ele2026_alertas
-- GROUP BY uf, cargo_interesse
-- ORDER BY volume_total_mapeado DESC NULLS LAST;
--
-- Total esperado: 34 entradas cobrindo 5 ecossistemas
-- ─────────────────────────────────────────────────────────────────────────────
