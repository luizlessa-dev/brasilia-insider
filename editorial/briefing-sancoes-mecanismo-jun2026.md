# Addendum — Mecanismo de Repasse e Responsabilidade Legal
**BR Insider · jun/2026 — Terceira camada: como o dinheiro chega à empresa**
**Complementa:** briefing-sancoes-emendas-jun2026.md e briefing-sancoes-aprofundamento-jun2026.md

---

## O que o PDF da SMASAC-BH revelou

A análise de um contrato da Secretaria Municipal de Assistência Social, Segurança Alimentar e Cidadania de Belo Horizonte (SMASAC) de 2021 confirmou o **mecanismo padrão de repasse** usado na cidade:

1. O **parlamentar federal** indica a emenda para a **Prefeitura de BH** (PBH) como beneficiária
2. A **PBH/SMASAC** usa **Dispensa de Licitação** ("dispensa de chamamento público decorrente de emenda impositiva") para contratar um fornecedor sem licitação completa
3. O **fornecedor** (ex: empresa de hortifrutigranjeiros, máquinas, equipamentos) recebe o pagamento da prefeitura com os recursos da emenda federal
4. No SIAFI, o fornecedor aparece como **favorecido final** (quem recebeu o dinheiro público)

**Conclusão:** A Comercial Licita Maquinas Ltda. não recebe emendas *diretamente* do governo federal — ela recebe da PBH, que por sua vez recebeu a emenda. O governo federal paga a PBH (que não está sancionada). A PBH contrata a Comercial Licita.

---

## Quem é responsável pela verificação do CEIS/CNEP

### Confirmação técnica (SIOP/SIAFI)

O manual do SIOP (Sistema Integrado de Planejamento e Orçamento) confirma que os **impedimentos técnicos automáticos** que bloqueiam emendas no SIAFI são:
- Beneficiário e valor não indicados pelo parlamentar
- Plano de trabalho não apresentado
- Objeto incompatível com a ação orçamentária
- Problemas de viabilidade técnica/ambiental

**O CEIS/CNEP NÃO é um impedimento técnico verificado automaticamente pelo SIAFI antes do repasse para municípios.**

### A cadeia de responsabilidades

| Ator | O que verifica | O que NÃO verifica automaticamente |
|---|---|---|
| **Governo federal (SIAFI)** | Se o município beneficiário está regular (CAUC, SIAFI) | CEIS/CNEP das empresas que o município vai contratar |
| **Município (PBH/SMASAC)** | **Deve verificar CEIS/CNEP** antes de cada contrato | Nem sempre faz a verificação na prática |
| **Empresa fornecedora** | Declara estar apta a contratar | Pode omitir restrições |

**Base legal:** o Decreto 8.420/2015 (art. 9º) determina que órgãos e entidades públicas devem consultar o CEIS/CNEP antes de celebrar contratos ou transferências. A responsabilidade primária é do **órgão contratante** — no caso de emendas com execução municipal, é o **município**.

---

## Reformulação do caso 1 à luz do mecanismo

### O que realmente aconteceu (hipótese mais provável)

**Antes de 13/03/2026:**
- PBH contratou a Comercial Licita Maquinas para fornecer equipamentos (possivelmente máquinas agrícolas para hortas comunitárias, cozinhas industriais ou similares)
- Os parlamentares indicaram suas emendas para a PBH/SMASAC com esse objeto
- O contrato foi assinado quando a empresa NÃO estava no CEIS — portanto, a contratação foi regular

**A partir de 13/03/2026:**
- A SEAG/ES impôs a sanção CEIS à empresa
- Os pagamentos previstos no contrato já assinado continuaram sendo processados pelo PBH
- No SIAFI, os pagamentos aparecem como tendo a empresa como favorecida

### Duas histórias possíveis

**História A — Falha de gestão municipal:**
PBH assinou contratos APÓS 13/03/2026 sem verificar o CEIS. Nesse caso, a prefeitura violou o Decreto 8.420/2015 ao contratar empresa impedida. A responsabilidade é de servidores municipais que processaram as dispensas de licitação sem checar as restrições.

**História B — Contrato pré-existente, pagamentos continuados:**
PBH tinha contratos válidos assinados antes de 13/03/2026. Após a sanção, os pagamentos continuaram porque o instrumento jurídico já estava vigente. Nesse caso, há debate jurídico sobre se a sanção posterior anula contratos em execução — a resposta varia por tipo de sanção e órgão sancionador.

**Como distinguir:** Verificar no Portal da Transparência a data de assinatura de cada convênio/instrumento associado às emendas pagas em março-maio/2026. Se assinados após 13/03, é a História A (clara infração). Se anteriores, é a História B (zona cinzenta jurídica).

---

## O ângulo federal permanece — mas é sistêmico, não pontual

Mesmo que a responsabilidade direta seja municipal, o ângulo federal é válido e mais forte:

> **"O governo federal libera bilhões em emendas parlamentares sem que o SIAFI verifique automaticamente o CEIS/CNEP das empresas contratadas pelos municípios."**

Isso não é falha de 28 parlamentares — é uma **lacuna de arquitetura sistêmica** em que:
- O SIAFI verifica a regularidade do município (beneficiário direto)
- Mas não verifica a regularidade dos fornecedores que o município vai contratar
- O resultado: empresas sancionadas podem receber recursos federais via municípios intermediários

**Dados que suportam esse ângulo:**
- 8 empresas sancionadas receberam R$ 6,6M+ em recursos de emendas após a punição
- A mais antiga está penalizada há 7 anos (Metalúrgica Flex Fitness, desde 2017)
- Os recursos passaram por municípios em pelo menos 7 estados diferentes

**Comparação internacional:** sistemas de compras públicas como o europeu (ESPD — European Single Procurement Document) exigem verificação automática de sanções em TODA a cadeia de pagamento, incluindo subcontratados. O Brasil verifica apenas o primeiro elo.

---

## Ângulo editorial sugerido: dois textos distintos

### Texto 1 — O caso Belo Horizonte (investigação local)
**Foco:** PBH contratou empresa do CEIS para programas sociais usando emendas de 28 parlamentares
**Fonte principal:** Portal da Transparência → emendas SMASAC → contratos com a Comercial Licita → datas de assinatura vs. data da sanção
**Contraditório:** SMASAC-BH (processo de contratação), SEAG-ES (motivação da sanção)
**Ângulo:** governo municipal que gerencia verba federal de segurança alimentar não checa restrições dos fornecedores

### Texto 2 — A falha sistêmica nacional
**Foco:** SIAFI não verifica CEIS/CNEP antes de repassar emendas — 8 empresas sancionadas receberam R$ 6,6M+
**Fonte principal:** cruzamento CEIS/CNEP × emendas_favorecidos, confirmação via SIOP
**Contraditório:** Ministério do Planejamento (por que o SIAFI não integra automaticamente o CEIS?), CGU (quem é responsável pela fiscalização?)
**Ângulo:** lacuna sistêmica que permite que recursos públicos cheguem a empresas punidas via intermediários municipais

---

## Perguntas de LAI para o Ministério do Planejamento/CGU

1. "O SIAFI possui rotina automática de verificação do CEIS/CNEP antes de processar transferências de emendas parlamentares para municípios? Se sim, como funciona? Se não, qual órgão é responsável por garantir que os recursos não cheguem a empresas sancionadas?"

2. "Existe auditoria periódica cruzando o CEIS/CNEP com os favorecidos finais de emendas parlamentares (conforme aparecem no SIAFI)? Se sim, com qual frequência e quais foram os resultados dos últimos dois anos?"

3. "Qual o procedimento padrão quando um município contrata, com recursos de emenda federal, uma empresa que está no CEIS? O repasse é bloqueado? O município é notificado? Há ressarcimento?"

---

*Documento para uso editorial interno. Verificação obrigatória antes de publicação.*
