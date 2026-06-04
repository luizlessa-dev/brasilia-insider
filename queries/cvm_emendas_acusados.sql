-- PAUTA: Empresas punidas pela CVM que receberam emendas parlamentares
-- Fonte: cvm_cruzamento_emendas (view em 0015_cvm_schema.sql)
--
-- ATENÇÃO: o cruzamento é por nome normalizado — sempre confirmar o CNPJ
-- na Receita Federal antes de publicar qualquer empresa específica.

-- 1. Ranking por valor total de emendas recebidas
select
    cvm_nome                                    as empresa,
    cvm_fase                                    as fase_processo,
    cvm_situacao                                as situacao_acusado,
    cvm_data_abertura                           as processo_aberto_em,
    cvm_nup                                     as nup,
    uf,
    to_char(total_emendas, 'FM999,999,999,999') as total_emendas_brl,
    n_parlamentares                             as qtd_parlamentares_autores,
    n_transacoes
from cvm_cruzamento_emendas
order by total_emendas desc
limit 50;


-- 2. Só processos com condenação (fase "Finalizado" ≠ absolvição — filtrar manualmente)
select *
from cvm_cruzamento_emendas
where cvm_situacao ilike '%condenado%'
   or cvm_situacao ilike '%pena%'
   or cvm_situacao ilike '%multa%'
order by total_emendas desc;


-- 3. Distribuição por fase do processo
select
    cvm_fase,
    count(distinct cvm_nup)     as n_processos,
    count(distinct cvm_nome)    as n_empresas,
    sum(total_emendas)          as total_emendas
from cvm_cruzamento_emendas
group by cvm_fase
order by total_emendas desc;


-- 4. Parlamentares que mais enviaram emendas a empresas com processo CVM
-- (requer join adicional com parlamentares — ajustar conforme schema)
select
    f.autor_nome,
    f.partido,
    f.uf_autor,
    count(distinct c.cvm_nup)               as n_empresas_com_processo_cvm,
    sum(f.valor_repasse)                    as total_emendas_para_essas_empresas
from emendas_favorecidos f
join cvm_acusados a
    on upper(regexp_replace(f.favorecido_nome, '[^A-Za-zÀ-ÿ0-9 ]', '', 'g'))
       = a.nome_normalizado
join cvm_processos c on c.nup = a.nup
group by f.autor_nome, f.partido, f.uf_autor
having count(distinct c.nup) >= 2
order by total_emendas_para_essas_empresas desc
limit 30;
