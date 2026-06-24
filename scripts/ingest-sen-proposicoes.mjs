import { config } from 'dotenv';
config({ path: '.env.local' });
/**
 * ingest-sen-proposicoes.mjs
 * Ingere autorias de proposições dos 81 senadores via API do Senado Federal.
 * Fonte: legis.senado.leg.br/dadosabertos/senador/{codigo}/autorias
 * Destino: tabela `sen_proposicoes` (Supabase dados-civicos)
 */
import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://redggdtakzmsabwvjzhb.supabase.co';
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_KEY;

if (!SUPABASE_KEY) {
  console.error('❌  SUPABASE_SERVICE_KEY não configurada.');
  process.exit(1);
}

const sb = createClient(SUPABASE_URL, SUPABASE_KEY);
const sleep = ms => new Promise(r => setTimeout(r, ms));

const SENADO_API = 'https://legis.senado.leg.br/dadosabertos';
const BATCH_SIZE = 200;

async function fetchAutorias(codigo) {
  const url = `${SENADO_API}/senador/${codigo}/autorias`;
  try {
    const resp = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!resp.ok) return [];
    const data = await resp.json();
    const root = data?.MateriasAutoriaParlamentar?.Parlamentar?.Autorias?.Autoria;
    if (!root) return [];
    return Array.isArray(root) ? root : [root];
  } catch {
    return [];
  }
}

async function main() {
  console.log('╔══════════════════════════════════════════════════╗');
  console.log('║   Ingestão: Proposições dos Senadores            ║');
  console.log('╚══════════════════════════════════════════════════╝\n');

  const { data: senadores, error } = await sb
    .from('sen_senadores')
    .select('codigo, nome_completo');

  if (error || !senadores?.length) {
    console.error('❌ Erro ao buscar sen_senadores:', error?.message);
    process.exit(1);
  }
  console.log(`✅  ${senadores.length} senadores carregados\n`);

  let totalProposicoes = 0;
  let totalInseridos = 0;
  let processados = 0;

  for (const sen of senadores) {
    const autorias = await fetchAutorias(sen.codigo);

    if (autorias.length > 0) {
      const seen = new Set();
      const rows = autorias.map(a => {
        const mat = a.Materia ?? {};
        return {
          senador_codigo:    String(sen.codigo),
          sigla_materia:     mat.Sigla ?? null,
          numero:            String(mat.Numero ?? ''),
          ano:               String(mat.Ano ?? ''),
          ementa:            (mat.Ementa ?? '').substring(0, 500) || null,
          data_apresentacao: mat.Data ? mat.Data.substring(0, 10) : null,
          tipo_autoria:      a.IndicadorAutorPrincipal ?? null,
        };
      }).filter(r => {
        if (!r.numero || !r.ano) return false;
        const key = `${r.senador_codigo}|${r.sigla_materia}|${r.numero}|${r.ano}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });

      totalProposicoes += rows.length;

      for (let i = 0; i < rows.length; i += BATCH_SIZE) {
        const batch = rows.slice(i, i + BATCH_SIZE);
        const { error: uErr } = await sb
          .from('sen_proposicoes')
          .upsert(batch, { onConflict: 'senador_codigo,sigla_materia,numero,ano', ignoreDuplicates: true });
        if (uErr) {
          console.warn(`  ⚠️  ${sen.nome_completo} (batch ${i}): ${uErr.message}`);
        } else {
          totalInseridos += batch.length;
        }
      }
    }

    processados++;
    if (processados % 10 === 0 || autorias.length > 0) {
      process.stdout.write(`  [${processados}/${senadores.length}] ${(sen.nome_completo ?? '').padEnd(35)} → ${autorias.length} autorias   \r`);
    }

    await sleep(400);
  }

  const { count } = await sb
    .from('sen_proposicoes')
    .select('*', { count: 'exact', head: true });

  console.log('\n\n🏁  Concluído:');
  console.log(`   ✅  Senadores processados: ${processados}`);
  console.log(`   📊  Proposições capturadas: ${totalProposicoes.toLocaleString('pt-BR')}`);
  console.log(`   💾  Inseridas/atualizadas: ${totalInseridos.toLocaleString('pt-BR')}`);
  console.log(`   🗄️   Total no banco: ${count?.toLocaleString('pt-BR')}`);
}

main().catch(e => { console.error(e); process.exit(1); });
