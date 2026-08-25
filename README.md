# Indicadores setoriais de companhias abertas (CVM)

Sistema para empilhar as Demonstrações Financeiras Padronizadas (DFP) de vários
anos e calcular indicadores de liquidez, margens, endividamento e rentabilidade
por setor de atividade.

---

## Instalação e execução

```bash
pip install -r requirements.txt
streamlit run app.py
```

O navegador abre em `http://localhost:8501`.

Duas formas de carregar dados:

- **Enviar arquivos** — arraste os CSVs ou o ZIP anual completo da CVM na barra lateral.
- **Ler de uma pasta** — coloque tudo em `./dados` e informe o caminho. É o modo
  prático quando você carrega dez anos de uma vez.

## Arquivos aceitos

| Arquivo | Uso |
|---|---|
| `dfp_cia_aberta_BPA_con_AAAA.csv` | Ativo |
| `dfp_cia_aberta_BPP_con_AAAA.csv` | Passivo e patrimônio líquido |
| `dfp_cia_aberta_DRE_con_AAAA.csv` | Resultado |
| `dfp_cia_aberta_DFC_MI_con_AAAA.csv` | Fluxo de caixa, método indireto |
| `dfp_cia_aberta_DFC_MD_con_AAAA.csv` | Fluxo de caixa, método direto |
| `Base_Cadastral.csv` ou `fca_cia_aberta_geral_AAAA.csv` | Setor de atividade |

Arquivos `_ind_` (demonstrações individuais) são **ignorados de propósito**:
misturar DF individual e consolidada da mesma companhia duplicaria a empresa na
estatística setorial. Arquivos ITR também são reconhecidos, mas o motor descarta
períodos não anuais.

Download em: https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp

---

## Arquitetura

```
app.py                      interface Streamlit
core/
  ingestao.py               detecta tipo de arquivo, resolve encoding, abre ZIPs
  normalizacao.py           versão, escala monetária, exercícios, setores
  conceitos.py              plano de contas → conceitos canônicos
  indicadores.py            motor de fórmulas
  agregacao.py              estatísticas setoriais
config/
  conceitos.yaml            CD_CONTA → conceito, por plano de contas
  indicadores.yaml          dicionário de indicadores (numerador / denominador)
  setores.yaml              normalização de setores
```

O código não conhece nenhum indicador. Tudo o que é fórmula, código de conta ou
rótulo de setor vive em `config/`. Para adicionar um indicador, edite
`config/indicadores.yaml` e recarregue — nenhuma linha de Python muda.

### Uso como biblioteca

```python
import core
res = core.executar_pasta("./dados")

res.painel                      # conceitos por empresa/ano
res.indicadores                 # indicadores por empresa/ano
res.setorial_nao_financeira     # mediana, quartis e agregado por setor/ano
res.setorial_financeira
res.diagnostico                 # cobertura e descartes
res.conflitos                   # divergências entre divulgação e reapresentação
```

---

## Decisões metodológicas

Estas escolhas afetam os números. Elas estão aqui para poder ser contestadas.

**Plano de contas detectado pela estrutura, não pelo cadastro.** O `CD_CONTA` da
CVM não tem significado único: `2.03` é patrimônio líquido numa indústria,
provisões num banco e passivos financeiros ao custo amortizado numa seguradora.
O sistema identifica o plano pela assinatura do balanço que a própria companhia
entregou, porque o setor cadastral pode estar desatualizado ou ausente.

**Financeiras em relatório separado.** Bancos e seguradoras não segregam
circulante e não circulante, então liquidez corrente e margem bruta não existem
nesse plano. Elas recebem ROE, ROA, alavancagem e margem de intermediação, numa
aba própria.

**Apenas contas padronizadas.** Só entram linhas com `ST_CONTA_FIXA = 'S'`.
Contas livres criadas por cada empresa não são comparáveis entre companhias.

**Mediana em vez de média.** Indicadores financeiros têm cauda pesada. Uma
empresa com patrimônio líquido residual produz ROE de três dígitos e desloca a
média do setor inteiro. O painel mostra mediana, quartis e, separadamente, o
**agregado do setor** (soma dos numeradores dividida pela soma dos
denominadores), que é a leitura ponderada pelo tamanho.

**Saneamento antes da agregação.** Observações com denominador abaixo de
R$ 100 mil são descartadas, assim como razões fora de limites de plausibilidade.
Toda observação descartada aparece contabilizada na aba de qualidade — nada some
em silêncio.

**Desempate entre exercícios repetidos.** Cada arquivo anual traz dois exercícios
(`ÚLTIMO` e `PENÚLTIMO`), então o exercício N aparece no arquivo N e no N+1. O
padrão usa o número como divulgado no próprio ano; a barra lateral permite trocar
para o número reapresentado. As divergências ficam listadas na aba de qualidade.

---

## Limitações conhecidas

**EBITDA é estimado.** A DFP não tem conta padronizada para depreciação e
amortização. O sistema procura, dentro das atividades operacionais da DFC, linhas
cuja descrição mencione depreciação ou amortização, e descarta contas-pai para
não contar duas vezes. Consequência prática: **sem o arquivo `DFC_MI`, a cobertura
de EBITDA, Dívida Líquida/EBITDA e conversão de caixa fica próxima de zero**,
porque a ampla maioria das companhias usa o método indireto.

**Setor é um retrato do presente.** A base cadastral é um instantâneo. O setor
atribuído a uma companhia vale para todos os anos do painel; reclassificações
setoriais passadas não são recuperáveis a partir desses arquivos.

**ROE usa o resultado consolidado.** O numerador inclui a parcela de acionistas
não controladores, assim como o denominador. Para ROE atribuível ao controlador,
subtraia `participacao_nao_controladores`, que já está extraído no painel.

**Cobertura de juros é aproximada.** A DFP não isola a despesa de juros dentro do
resultado financeiro. O indicador usa o resultado financeiro líquido, o que
subestima a cobertura de empresas com receita financeira relevante.

**Composição setorial muda entre anos.** Empresas abrem e fecham capital. Uma
variação na mediana do setor pode refletir mudança de composição, não desempenho.
A contagem de empresas acompanha todas as tabelas por esse motivo.

---

## Fonte dos dados

COMISSÃO DE VALORES MOBILIÁRIOS. *Dados Abertos CVM: Demonstrações Financeiras
Padronizadas (DFP) e Formulário Cadastral (FCA)*. Rio de Janeiro: CVM. Disponível
em: https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp. Acesso em: 25 ago. 2026.
