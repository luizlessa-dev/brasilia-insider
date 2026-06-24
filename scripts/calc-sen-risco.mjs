import { config } from 'dotenv';
config({ path: '.env.local' });
/**
 * calc-sen-risco.mjs
 * Calcula as 5 dimensões G5 (0-100 cada) para os 81 senadores
 * e popula sen_parlamentar_risco (Supabase dados-civicos).
 *
 * score_total = dim_ceap*0.30 + dim_presenca*0.20 + dim_producao*0.15
 *             + dim_financiamento*0.20 + dim_rp9*0.15
 */
import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://redggdtakzmsabwvjzhb.supabase.co';
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_KEY;

if (!SUPABASE_KEY) {
  console.error('❌  SUPABASE_SERVICE_KEY não configurada.');
  process.exit(1);
}

const sb = createClient(SUPABASE_URL, SUPABASE_KEY);

function percentileRank(values, target) {
  if (values.length <= 1) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = sorted.indexOf(target);
  if (idx < 0) return 0;
  return Math.round((idx / (sorted.length - 1)) * 1000) / 10;
}

async function loadAll(table, selectCols, filters = []) {
  let rows = [];
  let from = 0;
  while (true) {
    let q = sb.from(table).select(selectCols).range(from, from + 999);
    for (const [col, op, val] of filters) {
      if (op === 'ilike') q = q.ilike(col, val);
      else if (op === 'not_null') q = q.not(col, 'is', null);
    }
    const { data, error } = await q;
    if (error) { console.error(`❌ Erro lendo ${table}: ${error.message}`); process.exit(1); }
    if (!data?.length) break;
    rows.push(...data);
    if (data.length < 1000) break;
    from += 1000;
  }
  return rows;
}

function normName(s) {
  if (!s) return '';
  return s.toUpperCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^A-Z\s]/g, '').replace(/\s+/g, ' ').trim();
}

async function main() {
  console.log('╔═══════════════════════════════════════════════════════════╗');
  console.log('║   Score G5 — Senadores (sen_parlamentar_risco)           ║');
  console.log('╚═══════════════════════════════════════════════════════════╝\n');

  // 1. Senadores base
  console.log('📋  Carregando sen_senadores...');
  const { data: senadores, error: senErr } = await sb
    .from('sen_senadores')
    .select('codigo, nome_completo, nome_norm, partido, uf');
  if (senErr || !senadores?.length) {
    console.error('❌', senErr?.message);
    process.exit(1);
  }
  console.log(`   ${senadores.length} senadores.\n`);

  // 2. Ponte parlamentares → id_senado + uuid + cpf
  console.log('🔗  Carregando parlamentares (ponte id_senado → uuid)...');
  const { data: parls } = await sb
    .from('parlamentares')
    .select('id, id_senado, cpf')
    .eq('casa_legislativa', 'senado')
    .not('id_senado', 'is', null);

  const codigoToUuid = new Map((parls ?? []).map(p => [String(p.id_senado), p.id]));
  const uuidToCodigo = new Map((parls ?? []).map(p => [p.id, String(p.id_senado)]));
  console.log(`   ${parls?.length ?? 0} parlamentares no senado.\n`);

  // 3. dim_ceap
  console.log('💳  dim_ceap: agregando senado_ceaps_despesa...');
  const ceapsRows = await loadAll('senado_ceaps_despesa', 'cod_senador,valor_reembolsado');
  const ceapMap = new Map();
  for (const r of ceapsRows) {
    const cod = String(r.cod_senador);
    ceapMap.set(cod, (ceapMap.get(cod) || 0) + (parseFloat(r.valor_reembolsado) || 0));
  }
  const ceapValues = [...ceapMap.values()];
  console.log(`   ${ceapsRows.length.toLocaleString('pt-BR')} linhas, ${ceapMap.size} senadores com CEAP.\n`);

  // 4. dim_presenca
  console.log('📅  dim_presenca: contando sessões em votacoes_senado...');
  const votRows = await loadAll('votacoes_senado', 'parlamentar_id,id_sessao');
  const totalSessoes = new Set(votRows.map(r => r.id_sessao)).size;
  const sessoesPerUuid = new Map();
  for (const r of votRows) {
    if (!sessoesPerUuid.has(r.parlamentar_id)) sessoesPerUuid.set(r.parlamentar_id, new Set());
    sessoesPerUuid.get(r.parlamentar_id).add(r.id_sessao);
  }
  console.log(`   ${totalSessoes} sessões distintas, ${sessoesPerUuid.size} senadores com votos.\n`);

  // 5. dim_producao
  console.log('📝  dim_producao: contando sen_proposicoes...');
  const propRows = await loadAll('sen_proposicoes', 'senador_codigo');
  const propMap = new Map();
  for (const r of propRows) {
    const cod = String(r.senador_codigo);
    propMap.set(cod, (propMap.get(cod) || 0) + 1);
  }
  const propValues = [...propMap.values()];
  console.log(`   ${propRows.length.toLocaleString('pt-BR')} proposições, ${propMap.size} senadores autores.\n`);

  // 6. dim_financiamento
  console.log('💰  dim_financiamento: agregando tse_receitas_brutas por CPF...');
  // Monta mapa cpf → uuid
  const cpfToUuid = new Map((parls ?? []).filter(p => p.cpf).map(p => [p.cpf, p.id]));
  const cpfs = [...cpfToUuid.keys()];
  const finRows = [];
  let finFrom = 0;
  while (true) {
    const { data, error } = await sb
      .from('tse_receitas_brutas')
      .select('nr_cpf_candidato,vr_receita')
      .in('nr_cpf_candidato', cpfs)
      .range(finFrom, finFrom + 999);
    if (error) { console.warn(`  ⚠️  tse_receitas_brutas: ${error.message}`); break; }
    if (!data?.length) break;
    finRows.push(...data);
    if (data.length < 1000) break;
    finFrom += 1000;
  }
  const finMapByCpf = new Map();
  for (const r of finRows) {
    const cpf = r.nr_cpf_candidato;
    finMapByCpf.set(cpf, (finMapByCpf.get(cpf) || 0) + (parseFloat(r.vr_receita) || 0));
  }
  const finValues = [...finMapByCpf.values()];
  console.log(`   ${finRows.length.toLocaleString('pt-BR')} linhas, ${finMapByCpf.size} senadores com financiamento.\n`);

  // 7. dim_rp9
  console.log('🔴  dim_rp9: agregando emendas_rp9_apoiamento (cargo=senador)...');
  const rp9Rows = await loadAll(
    'emendas_rp9_apoiamento',
    'nome_apoiador,cargo_apoiador',
    [['cargo_apoiador', 'ilike', '%senad%']]
  );
  const rp9ByNome = new Map();
  for (const r of rp9Rows) {
    const k = normName(r.nome_apoiador);
    rp9ByNome.set(k, (rp9ByNome.get(k) || 0) + 1);
  }
  const maxRp9 = Math.max(...rp9ByNome.values(), 1);
  console.log(`   ${rp9Rows.length} vínculos, ${rp9ByNome.size} senadores RP-9.\n`);

  // 8. Compor scores
  console.log('🧮  Calculando dimensões e score_total...');

  const updates = senadores.map(sen => {
    const cod  = String(sen.codigo);
    const uuid = codigoToUuid.get(cod);
    const cpf  = parls?.find(p => String(p.id_senado) === cod)?.cpf;

    const ceapTotal     = ceapMap.get(cod) || 0;
    const dim_ceap      = percentileRank([...ceapValues, ceapTotal], ceapTotal);

    const sessoesVotadas = uuid ? (sessoesPerUuid.get(uuid)?.size ?? 0) : 0;
    const presenca_pct   = totalSessoes > 0 ? Math.round((sessoesVotadas / totalSessoes) * 1000) / 10 : 0;
    const dim_presenca   = presenca_pct;

    const totalProp    = propMap.get(cod) || 0;
    const dim_producao = percentileRank([...propValues, totalProp], totalProp);

    const finTotal          = cpf ? (finMapByCpf.get(cpf) || 0) : 0;
    const dim_financiamento = percentileRank([...finValues, finTotal], finTotal);

    const nomeNorm = normName(sen.nome_norm || sen.nome_completo);
    const rp9Vinc  = rp9ByNome.get(nomeNorm) || 0;
    const dim_rp9  = rp9Vinc > 0
      ? Math.min(100, Math.round((Math.sqrt(rp9Vinc) / Math.sqrt(maxRp9)) * 1000) / 10)
      : 0;

    const score_total = Math.round((
      dim_ceap          * 0.30 +
      dim_presenca      * 0.20 +
      dim_producao      * 0.15 +
      dim_financiamento * 0.20 +
      dim_rp9           * 0.15
    ) * 10) / 10;

    return {
      senador_codigo:      cod,
      nome:                sen.nome_completo,
      partido:             sen.partido,
      uf:                  sen.uf,
      dim_ceap,
      dim_presenca,
      dim_producao,
      dim_financiamento,
      dim_rp9,
      score_total,
      ceap_total:          Math.round(ceapTotal * 100) / 100,
      presenca_pct,
      total_proposicoes:   totalProp,
      financiamento_total: Math.round(finTotal * 100) / 100,
      rp9_vinculos:        rp9Vinc,
      updated_at:          new Date().toISOString(),
    };
  });

  // 9. Upsert em lotes
  console.log('💾  Salvando sen_parlamentar_risco...');
  const BATCH = 200;
  let ok = 0;
  for (let i = 0; i < updates.length; i += BATCH) {
    const batch = updates.slice(i, i + BATCH);
    const { error } = await sb
      .from('sen_parlamentar_risco')
      .upsert(batch, { onConflict: 'senador_codigo' });
    if (error) { console.error(`❌ Lote ${i}: ${error.message}`); process.exit(1); }
    ok += batch.length;
    process.stdout.write(`\r   ✅ ${ok}/${updates.length}`);
  }
  console.log('\n');

  // 10. Top 5
  const { data: top5 } = await sb
    .from('sen_parlamentar_risco')
    .select('nome, score_total, dim_ceap, dim_presenca, dim_rp9')
    .order('score_total', { ascending: false })
    .limit(5);

  console.log('🏆  Top 5 por score_total:');
  for (const r of top5 ?? []) {
    console.log(`   ${(r.nome ?? '').padEnd(30)} score=${r.score_total}  ceap=${r.dim_ceap}  pres=${r.dim_presenca}  rp9=${r.dim_rp9}`);
  }
  console.log('\n🏁  Concluído.');
}

main().catch(e => { console.error(e); process.exit(1); });
