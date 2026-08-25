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
app.py          interface Streamlit (528 linhas, 6 abas)
core/           motor, usável como biblioteca independente da UI
  ingestao      detecta tipo de arquivo, resolve encoding, abre ZIPs
  normalizacao  versão, escala monetária, exercícios, setores
  conceitos     plano de contas → conceitos canônicos
  indicadores   motor de fórmulas
  agregacao     estatísticas setoriais
config/         conceitos.yaml · indicadores.yaml · setores.yaml
dados/          arquivos da CVM (fora do git)
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

**Nada é descartado em silêncio.** Toda observação filtrada (denominador pequeno,
razão implausível, período não anual, conta não padronizada) tem que aparecer
contabilizada em `diagnostico` e chegar à aba "Qualidade dos dados".

**Duas famílias de empresa.** `nao_financeira` e `financeira` são separadas em todo
o pipeline — banco não segrega circulante, então liquidez corrente e margem bruta
não existem nesse plano. O campo `familias` no YAML controla quem recebe cada
indicador; o relatório das financeiras tem aba própria.

**Mediana, não média,** para o corte setorial; o agregado do setor entra como
leitura ponderada, ao lado, não no lugar.

## Convenções de código
- Código Python é **ASCII puro** (docstrings e comentários sem acento). Acentuação
  aparece só em strings voltadas ao usuário (labels de UI) e nos YAML/Markdown.
- Nomes de funções, variáveis e colunas em **português** (`painel`, `fatos`,
  `ano`, `setor`, `executar_pasta`). Manter.
- Módulos em `core/` não importam Streamlit — a UI depende do motor, nunca o
  contrário. Cache é responsabilidade do `app.py` (`@st.cache_data`).

## Dados
- Fonte: https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp
- Os CSVs/ZIPs ficam em `dados/` e estão no `.gitignore` — **não commitar**.
- Arquivos `_ind_` (demonstrações individuais) são ignorados de propósito.
- Sem o arquivo `DFC_MI`, a cobertura de EBITDA e derivados cai a quase zero.
  Se um indicador vier vazio, checar isto antes de suspeitar do motor.

## Contexto do desenvolvedor
- Lucas Clemente — INEPAD Governança e Sucessão
- Perfil: leigo em programação, desenvolve com apoio de IA
- Sempre entregar código completo para evitar erros de edição parcial
