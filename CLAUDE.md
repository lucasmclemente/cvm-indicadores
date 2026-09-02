# CLAUDE.md — Indicadores setoriais CVM

> Arquivo de contexto do projeto. Manter enxuto: o que não é óbvio lendo o código.

## Visão geral
- **Nome:** `cvm_indicadores` — Indicadores setoriais de companhias abertas (CVM)
- **O que faz:** empilha as Demonstrações Financeiras Padronizadas (DFP) de vários
  anos e calcula liquidez, margens, endividamento e rentabilidade **por setor**.
- **Status:** importado para `C:\Users\lucas\cvm_indicadores` em 25/08/2026; git
  inicializado, sem remote.
- **Documentação de referência:** [`README.md`](README.md) — leia antes de mexer em
  metodologia. Ele registra as decisões metodológicas e as limitações conhecidas.

## Stack
- **Python 3.14** (ambiente atual: 3.14.7, dependências instaladas globalmente).
- **Streamlit** (UI), **pandas** / **numpy** (motor), **Altair** (gráficos),
  **PyYAML** (configuração), **openpyxl** (export Excel).
- Sem banco, sem backend, sem testes automatizados. Tudo roda local, em memória.

```bash
streamlit run app.py
```

## Arquitetura
```
app.py            interface Streamlit (6 abas)
gerar_snapshot.py script que congela o resultado para publicação
core/             motor, usável como biblioteca independente da UI
  ingestao        detecta tipo de arquivo, resolve encoding, abre ZIPs
  normalizacao    versão, escala monetária, exercícios, período, setores
  conceitos       plano de contas → conceitos canônicos
  indicadores     motor de fórmulas
  agregacao       estatísticas setoriais
  snapshot        grava/lê o Resultado em Parquet
config/           conceitos.yaml · indicadores.yaml · setores.yaml
dados/            arquivos brutos da CVM (fora do git)
snapshot/         resultado calculado (dentro do git — alimenta o painel público)
```

Ponto de entrada como biblioteca: `core.executar(arquivos)` ou
`core.executar_pasta("./dados")` → `core.Resultado` (dataclass com `painel`,
`indicadores`, `setorial_nao_financeira`, `setorial_financeira`, `diagnostico`,
`conflitos`, `log_ingestao`).

## Regras do projeto

**Regra de ouro: o Python não conhece nenhum indicador.** Fórmula, código de conta
e rótulo de setor vivem em `config/*.yaml`. Adicionar ou corrigir um indicador é
editar `config/indicadores.yaml` — nenhuma linha de Python muda. Se uma tarefa
parecer exigir hard-code de uma conta ou de uma fórmula em `core/`, isso é sinal de
que a solução está no YAML.

**Cada indicador é uma razão explícita `num` / `den`.** Isso não é estilo: é o que
permite calcular as duas visões setoriais a partir da mesma definição — mediana das
razões individuais **e** razão agregada do setor (`soma(num) / soma(den)`). Nunca
declarar um indicador como valor único já dividido.

**Conceito sem conta padronizada vira heurística declarada.** Alguns conceitos
não existem como conta fixa na DFP — D&A e dividendos pagos são os casos atuais.
Para esses, `conceitos_heuristicos` em `config/conceitos.yaml` declara subárvore,
regex de inclusão e de exclusão; `conceitos.extrair_heuristicos` aplica a regra e
descarta contas-pai para não contar duas vezes. O Python não conhece nenhum
desses conceitos pelo nome. Tudo que sai daí é **estimado** e precisa dizê-lo na
`interpretacao` do indicador.

**Nada é descartado em silêncio.** Toda observação filtrada (denominador pequeno,
razão implausível, período não anual, conta não padronizada) tem que aparecer
contabilizada em `diagnostico` e chegar à aba "Qualidade dos dados".

**Duas famílias de empresa.** `nao_financeira` e `financeira` são separadas em todo
o pipeline — banco não segrega circulante, então liquidez corrente e margem bruta
não existem nesse plano. O campo `familias` no YAML controla quem recebe cada
indicador; o relatório das financeiras tem aba própria.

**Mediana, não média,** para o corte setorial; o agregado do setor entra como
leitura ponderada, ao lado, não no lugar.

**Cada observação vive num período.** A chave é `(cnpj, ano, periodo)`, nunca
`(cnpj, ano)`. `periodo` vale `ano` (DFP), `1T`, `1S`, `9M` (acumulados do ITR) ou
`2T`, `3T` (trimestres isolados). Nada é derivado por subtração. `4T` e `2S`
também aparecem, mas só para as ~18 companhias de exercício deslocado cujo
trimestre outubro–dezembro coincide com o civil: ficam marcados como `oculto` em
`normalizacao.PERIODOS` e fora do seletor, sem sair dos dados. Recortes nunca se
misturam numa mesma estatística, e a UI compara sempre o mesmo recorte entre
anos. Em recorte parcial, indicadores de fluxo sobre saldo (ROE, ROA, giro)
**não são anualizados** — o painel avisa. `MED()` acha o saldo de abertura pelo
mês de encerramento do período anterior, não pela linha vizinha do painel.

**DFP e ITR não são o mesmo dicionário de contas.** A mesma companhia, no mesmo
exercício, pode reportar a conta com `CD_CONTA` diferente em cada documento — e
há banco que muda o código do patrimônio líquido entre os dois. Por isso uma
demonstração de um período vem de um documento só (`PREFERENCIA_ORIGEM`, DFP
primeiro) e o plano de contas é detectado por documento, não por companhia.
Misturar as duas fontes dobrava conceitos heurísticos e apagava conceitos
padronizados.

**O painel compartilhado lê snapshot, não CSV.** A ingestão é pesada demais para
hospedagem grátis (~1 GB de memória) e exigiria que cada pessoa subisse os
arquivos. Então `gerar_snapshot.py` roda o pipeline na máquina de quem publica e
grava `snapshot/*.parquet` — poucos MB. O `app.py` carrega esse snapshot sozinho
quando ele existe, e as origens "Enviar arquivos" / "Ler de uma pasta" continuam
disponíveis para recalcular. Nenhuma regra de negócio mora aí: é só persistência.

## Convenções de código
- Código Python é **ASCII puro** (docstrings e comentários sem acento). Acentuação
  aparece só em strings voltadas ao usuário (labels de UI) e nos YAML/Markdown.
- Nomes de funções, variáveis e colunas em **português** (`painel`, `fatos`,
  `ano`, `setor`, `executar_pasta`). Manter.
- Módulos em `core/` não importam Streamlit — a UI depende do motor, nunca o
  contrário. Cache é responsabilidade do `app.py` (`@st.cache_data`).

## Dados
- Fonte anual (DFP): https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp
- Fonte trimestral (ITR): https://dados.cvm.gov.br/dataset/cia_aberta-doc-itr
- O ITR traz o mesmo período do ano anterior em `PENÚLTIMO`, mas só na DRE/DFC —
  o balanço comparativo é o de 31/12. Para comparar trimestres entre anos, baixe
  o ITR de cada ano.
- Os CSVs/ZIPs ficam em `dados/` e estão no `.gitignore` — **não commitar**.
- Arquivos `_ind_` (demonstrações individuais) são ignorados de propósito.
- Sem o arquivo `DFC_MI`, a cobertura de EBITDA e derivados cai a quase zero.
  Se um indicador vier vazio, checar isto antes de suspeitar do motor.

## Contexto do desenvolvedor
- Lucas Clemente — INEPAD Governança e Sucessão
- Perfil: leigo em programação, desenvolve com apoio de IA
- Sempre entregar código completo para evitar erros de edição parcial
