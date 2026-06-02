"""
Mapeamento de colunas dos CSVs do Portal da Transparência para nomes
canônicos snake_case + tipagem.

Convenções:
  - encoding origem ISO-8859-1, sep=";", decimal=",".
  - todas as colunas chegam como `string` no bronze.
  - tipagem só acontece no silver (DDL Postgres) — bronze preserva fidelidade.

Tabelas alvo:
  execucao_mensal              (stream A — /despesas-execucao/YYYYMM/)
  empenho                      (stream B — /despesas/YYYYMMDD/)
  item_empenho                 (stream B)
  liquidacao                   (stream B)
  pagamento                    (stream B)
  pagamento_empenho            (stream B — junction N:N)
  pagamento_favorecido_final   (stream B — quando OB vai pra lista)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableSchema:
    name: str                # nome canônico interno (snake_case)
    inner_filename_suffix: str  # sufixo do CSV dentro do ZIP
    columns_pt: list[str]    # colunas no header original (PT, ISO-8859-1 já decodificado)
    columns_canonical: list[str]  # nomes canônicos snake_case correspondentes
    primary_key: list[str]   # PK lógica no silver

    def rename_map(self) -> dict[str, str]:
        return dict(zip(self.columns_pt, self.columns_canonical))


# ─────────────────────────────────────────────────────────────────────────────
# STREAM A — Execução mensal agregada
# ─────────────────────────────────────────────────────────────────────────────

EXECUCAO_MENSAL = TableSchema(
    name="execucao_mensal",
    inner_filename_suffix="_Despesas.csv",
    columns_pt=[
        "Ano e mês do lançamento",
        "Código Órgão Superior",
        "Nome Órgão Superior",
        "Código Órgão Subordinado",
        "Nome Órgão Subordinado",
        "Código Unidade Gestora",
        "Nome Unidade Gestora",
        "Código Gestão",
        "Nome Gestão",
        "Código Unidade Orçamentária",
        "Nome Unidade Orçamentária",
        "Código Função",
        "Nome Função",
        "Código Subfução",
        "Nome Subfunção",
        "Código Programa Orçamentário",
        "Nome Programa Orçamentário",
        "Código Ação",
        "Nome Ação",
        "Código Plano Orçamentário",
        "Plano Orçamentário",
        "Código Programa Governo",
        "Nome Programa Governo",
        "UF",
        "Município",
        "Código Subtítulo",
        "Nome Subtítulo",
        "Código Localizador",
        "Nome Localizador",
        "Sigla Localizador",
        "Descrição Complementar Localizador",
        "Código Autor Emenda",
        "Nome Autor Emenda",
        "Código Categoria Econômica",
        "Nome Categoria Econômica",
        "Código Grupo de Despesa",
        "Nome Grupo de Despesa",
        "Código Elemento de Despesa",
        "Nome Elemento de Despesa",
        "Código Modalidade da Despesa",
        "Modalidade da Despesa",
        "Valor Empenhado (R$)",
        "Valor Liquidado (R$)",
        "Valor Pago (R$)",
        "Valor Restos a Pagar Inscritos (R$)",
        "Valor Restos a Pagar Cancelado (R$)",
        "Valor Restos a Pagar Pagos (R$)",
    ],
    columns_canonical=[
        "competencia",
        "cod_orgao_superior",
        "nome_orgao_superior",
        "cod_orgao_subordinado",
        "nome_orgao_subordinado",
        "cod_ug",
        "nome_ug",
        "cod_gestao",
        "nome_gestao",
        "cod_unidade_orcamentaria",
        "nome_unidade_orcamentaria",
        "cod_funcao",
        "nome_funcao",
        "cod_subfuncao",
        "nome_subfuncao",
        "cod_programa_orcamentario",
        "nome_programa_orcamentario",
        "cod_acao",
        "nome_acao",
        "cod_plano_orcamentario",
        "plano_orcamentario",
        "cod_programa_governo",
        "nome_programa_governo",
        "uf",
        "municipio",
        "cod_subtitulo",
        "nome_subtitulo",
        "cod_localizador",
        "nome_localizador",
        "sigla_localizador",
        "descricao_complementar_localizador",
        "cod_autor_emenda",
        "nome_autor_emenda",
        "cod_categoria_economica",
        "nome_categoria_economica",
        "cod_grupo_despesa",
        "nome_grupo_despesa",
        "cod_elemento_despesa",
        "nome_elemento_despesa",
        "cod_modalidade_despesa",
        "modalidade_despesa",
        "valor_empenhado",
        "valor_liquidado",
        "valor_pago",
        "valor_restos_pagar_inscritos",
        "valor_restos_pagar_cancelado",
        "valor_restos_pagar_pagos",
    ],
    primary_key=[
        "competencia",
        "cod_ug",
        "cod_programa_orcamentario",
        "cod_acao",
        "cod_plano_orcamentario",
        "cod_elemento_despesa",
        "cod_modalidade_despesa",
        "cod_autor_emenda",
        "cod_subtitulo",
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# STREAM B — Snapshot diário (tabelas operacionais)
# ─────────────────────────────────────────────────────────────────────────────

EMPENHO = TableSchema(
    name="empenho",
    inner_filename_suffix="_Despesas_Empenho.csv",
    columns_pt=[
        "Id Empenho", "Código Empenho", "Código Empenho Resumido",
        "Data Emissão", "Código Tipo Documento", "Tipo Documento",
        "Tipo Empenho", "Espécie Empenho",
        "Código Órgão Superior", "Órgão Superior",
        "Código Órgão", "Órgão",
        "Código Unidade Gestora", "Unidade Gestora",
        "Código Gestão", "Gestão",
        "Código Favorecido", "Favorecido",
        "Observação",
        "Código Esfera Orçamentária", "Esfera Orçamentária",
        "Código Tipo Crédito", "Tipo Crédito",
        "Código Grupo Fonte Recurso", "Grupo Fonte Recurso",
        "Código Fonte Recurso", "Fonte Recurso",
        "Código Unidade Orçamentária", "Unidade Orçamentária",
        "Código Função", "Função",
        "Código SubFunção", "SubFunção",
        "Código Programa", "Programa",
        "Código Ação", "Ação",
        "Linguagem Cidadã",
        "Código Subtítulo (Localizador)", "Subtítulo (Localizador)",
        "Código Plano Orçamentário", "Plano Orçamentário",
        "Código Programa Governo", "Nome Programa Governo",
        "Autor Emenda",
        "Código Categoria de Despesa", "Categoria de Despesa",
        "Código Grupo de Despesa", "Grupo de Despesa",
        "Código Modalidade de Aplicação", "Modalidade de Aplicação",
        "Código Elemento de Despesa", "Elemento de Despesa",
        "Processo", "Modalidade de Licitação", "Inciso", "Amparo",
        "Referência de Dispensa ou Inexigibilidade",
        "Código Convênio", "Contrato de Repasse / Termo de Parceria / Outros",
        "Valor Original do Empenho", "Valor do Empenho Convertido pra R$",
        "Valor Utilizado na Conversão",
    ],
    columns_canonical=[
        "id_empenho", "codigo_empenho", "codigo_empenho_resumido",
        "data_emissao", "cod_tipo_documento", "tipo_documento",
        "tipo_empenho", "especie_empenho",
        "cod_orgao_superior", "nome_orgao_superior",
        "cod_orgao", "nome_orgao",
        "cod_ug", "nome_ug",
        "cod_gestao", "nome_gestao",
        "cnpj_favorecido", "nome_favorecido",
        "observacao",
        "cod_esfera_orcamentaria", "esfera_orcamentaria",
        "cod_tipo_credito", "tipo_credito",
        "cod_grupo_fonte_recurso", "nome_grupo_fonte_recurso",
        "cod_fonte_recurso", "nome_fonte_recurso",
        "cod_unidade_orcamentaria", "nome_unidade_orcamentaria",
        "cod_funcao", "nome_funcao",
        "cod_subfuncao", "nome_subfuncao",
        "cod_programa", "nome_programa",
        "cod_acao", "nome_acao",
        "linguagem_cidada",
        "cod_subtitulo", "nome_subtitulo",
        "cod_plano_orcamentario", "plano_orcamentario",
        "cod_programa_governo", "nome_programa_governo",
        "autor_emenda",
        "cod_categoria_despesa", "categoria_despesa",
        "cod_grupo_despesa", "grupo_despesa",
        "cod_modalidade_aplicacao", "modalidade_aplicacao",
        "cod_elemento_despesa", "elemento_despesa",
        "processo", "modalidade_licitacao", "inciso", "amparo",
        "ref_dispensa_inexigibilidade",
        "cod_convenio", "contrato_repasse",
        "valor_original_empenho", "valor_empenho_brl",
        "valor_utilizado_conversao",
    ],
    primary_key=["id_empenho"],
)


ITEM_EMPENHO = TableSchema(
    name="item_empenho",
    inner_filename_suffix="_Despesas_ItemEmpenho.csv",
    columns_pt=[
        "Id Empenho", "Código Empenho",
        "Código Categoria de Despesa", "Categoria de Despesa",
        "Código Grupo de Despesa", "Grupo de Despesa",
        "Código Modalidade de Aplicação", "Modalidade de Aplicação",
        "Código Elemento de Despesa", "Elemento de Despesa",
        "Código SubElemento de Despesa", "SubElemento de Despesa",
        "Descrição", "Quantidade", "Valor Unitário", "Valor Total",
        "Sequencial", "Valor Atual",
    ],
    columns_canonical=[
        "id_empenho", "codigo_empenho",
        "cod_categoria_despesa", "categoria_despesa",
        "cod_grupo_despesa", "grupo_despesa",
        "cod_modalidade_aplicacao", "modalidade_aplicacao",
        "cod_elemento_despesa", "elemento_despesa",
        "cod_subelemento_despesa", "subelemento_despesa",
        "descricao", "quantidade", "valor_unitario", "valor_total",
        "sequencial", "valor_atual",
    ],
    primary_key=["id_empenho", "sequencial"],
)


LIQUIDACAO = TableSchema(
    name="liquidacao",
    inner_filename_suffix="_Despesas_Liquidacao.csv",
    columns_pt=[
        "Código Liquidação", "Código Liquidação Resumido",
        "Data Emissão", "Código Tipo Documento", "Tipo Documento",
        "Código Órgão Superior", "Órgão Superior",
        "Código Órgão", "Órgão",
        "Código Unidade Gestora", "Unidade Gestora",
        "Código Gestão", "Gestão",
        "Código Favorecido", "Favorecido",
        "Observação",
        "Código Categoria de Despesa", "Categoria de Despesa",
        "Código Grupo de Despesa", "Grupo de Despesa",
        "Código Modalidade de Aplicação", "Modalidade de Aplicação",
        "Código Elemento de Despesa", "Elemento de Despesa",
        "Código Plano Orçamentário", "Plano Orçamentário",
        "Código Programa Governo", "Nome Programa Governo",
    ],
    columns_canonical=[
        "codigo_liquidacao", "codigo_liquidacao_resumido",
        "data_emissao", "cod_tipo_documento", "tipo_documento",
        "cod_orgao_superior", "nome_orgao_superior",
        "cod_orgao", "nome_orgao",
        "cod_ug", "nome_ug",
        "cod_gestao", "nome_gestao",
        "cnpj_favorecido", "nome_favorecido",
        "observacao",
        "cod_categoria_despesa", "categoria_despesa",
        "cod_grupo_despesa", "grupo_despesa",
        "cod_modalidade_aplicacao", "modalidade_aplicacao",
        "cod_elemento_despesa", "elemento_despesa",
        "cod_plano_orcamentario", "plano_orcamentario",
        "cod_programa_governo", "nome_programa_governo",
    ],
    primary_key=["codigo_liquidacao"],
)


PAGAMENTO = TableSchema(
    name="pagamento",
    inner_filename_suffix="_Despesas_Pagamento.csv",
    columns_pt=[
        "Código Pagamento", "Código Pagamento Resumido",
        "Data Emissão", "Código Tipo Documento", "Tipo Documento",
        "Tipo OB", "Extraorçamentário",
        "Código Órgão Superior", "Órgão Superior",
        "Código Órgão", "Órgão",
        "Código Unidade Gestora", "Unidade Gestora",
        "Código Gestão", "Gestão",
        "Código Favorecido", "Favorecido",
        "Observação", "Processo",
        "Código Categoria de Despesa", "Categoria de Despesa",
        "Código Grupo de Despesa", "Grupo de Despesa",
        "Código Modalidade de Aplicação", "Modalidade de Aplicação",
        "Código Elemento de Despesa", "Elemento de Despesa",
        "Código Plano Orçamentário", "Plano Orçamentário",
        "Código Programa Governo", "Nome Programa Governo",
        "Valor Original do Pagamento", "Valor do Pagamento Convertido pra R$",
        "Valor Utilizado na Conversão",
    ],
    columns_canonical=[
        "codigo_pagamento", "codigo_pagamento_resumido",
        "data_emissao", "cod_tipo_documento", "tipo_documento",
        "tipo_ob", "extra_orcamentario",
        "cod_orgao_superior", "nome_orgao_superior",
        "cod_orgao", "nome_orgao",
        "cod_ug", "nome_ug",
        "cod_gestao", "nome_gestao",
        "cnpj_favorecido", "nome_favorecido",
        "observacao", "processo",
        "cod_categoria_despesa", "categoria_despesa",
        "cod_grupo_despesa", "grupo_despesa",
        "cod_modalidade_aplicacao", "modalidade_aplicacao",
        "cod_elemento_despesa", "elemento_despesa",
        "cod_plano_orcamentario", "plano_orcamentario",
        "cod_programa_governo", "nome_programa_governo",
        "valor_original_pagamento", "valor_pagamento_brl",
        "valor_utilizado_conversao",
    ],
    primary_key=["codigo_pagamento"],
)


PAGAMENTO_EMPENHO = TableSchema(
    name="pagamento_empenho",
    inner_filename_suffix="_Despesas_Pagamento_EmpenhosImpactados.csv",
    columns_pt=[
        "Código Pagamento", "Código Empenho",
        "Código Natureza Despesa Completa", "Subitem",
        "Valor Pago (R$)",
        "Valor Restos a Pagar Inscritos (R$)",
        "Valor Restos a Pagar Cancelado (R$)",
        "Valor Restos a Pagar Pagos (R$)",
    ],
    columns_canonical=[
        "codigo_pagamento", "codigo_empenho",
        "cod_natureza_despesa", "subitem",
        "valor_pago",
        "valor_restos_pagar_inscritos",
        "valor_restos_pagar_cancelado",
        "valor_restos_pagar_pagos",
    ],
    primary_key=["codigo_pagamento", "codigo_empenho", "subitem"],
)


PAGAMENTO_FAVORECIDO_FINAL = TableSchema(
    name="pagamento_favorecido_final",
    inner_filename_suffix="_Despesas_Pagamento_FavorecidosFinais.csv",
    columns_pt=[
        "Código Pagamento", "Código Lista", "Data Emissão",
        "Código Favorecido", "Favorecido", "Valor do Pagamento em R$",
    ],
    columns_canonical=[
        "codigo_pagamento", "codigo_lista", "data_emissao",
        "cnpj_favorecido_final", "nome_favorecido_final",
        "valor_pagamento_brl",
    ],
    primary_key=["codigo_pagamento", "codigo_lista", "cnpj_favorecido_final"],
)


# Registry: name -> TableSchema
SNAPSHOT_TABLES: dict[str, TableSchema] = {
    t.name: t
    for t in [EMPENHO, ITEM_EMPENHO, LIQUIDACAO, PAGAMENTO,
              PAGAMENTO_EMPENHO, PAGAMENTO_FAVORECIDO_FINAL]
}
