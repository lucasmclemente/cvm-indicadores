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
| `itr_cia_aberta_BPA/BPP/DRE/DFC_*_con_AAAA.csv` | Mesmas demonstrações, trimestrais |
| `Base_Cadastral.csv` ou `fca_cia_aberta_geral_AAAA.csv` | Setor de atividade |

Arquivos `_ind_` (demonstrações individuais) são **ignorados de propósito**:
misturar DF individual e consolidada da mesma companhia duplicaria a empresa na
estatística setorial.

Download em:
https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp (anual) ·
https://dados.cvm.gov.br/dataset/cia_aberta-doc-itr (trimestral)

### Períodos

DFP e ITR convivem no mesmo painel sem se misturar. Cada observação pertence a um
**recorte**, escolhido na barra lateral, e a comparação acontece sempre dentro do
mesmo recorte, entre anos diferentes:

| Recorte | Janela | Vem de |
|---|---|---|
| Ano fechado | 12 meses | DFP |
| 1º trimestre | jan–mar | ITR do 1º trimestre |
| 1º semestre | jan–jun (acumulado) | ITR do 2º trimestre |
| 9 meses | jan–set (acumulado) | ITR do 3º trimestre |
| 2º trimestre | abr–jun (isolado) | ITR do 2º trimestre |
| 3º trimestre | jul–set (isolado) | ITR do 3º trimestre |

Só existe aqui o que a CVM publica. Nada é derivado por subtração: obter um 4º
trimestre a partir do exercício fechado menos o acumulado de nove meses juntaria
duas safras de reapresentação contábil e produziria número sem lastro.

**4º trimestre e 2º semestre não são oferecidos como recorte.** Eles existem no
painel, mas por efeito colateral do calendário: a classificação usa a janela
civil, então o trimestre outubro–dezembro de uma companhia que fecha o exercício
em março é, civilmente, um 4º trimestre. Isso reúne 18 companhias no 4T e 5 no
2S — usinas de açúcar e álcool, sobretudo. Como corte setorial seria uma amostra
mínima lida como se fosse o mercado, então `normalizacao.PERIODOS` marca os dois
como `oculto`. O dado continua no painel e na planilha exportada; só não aparece
no seletor.

---

## Arquitetura

```
app.py                      interface Streamlit
gerar_snapshot.py           congela o resultado para publicação
core/
  ingestao.py               detecta tipo de arquivo, resolve encoding, abre ZIPs
  normalizacao.py           versão, escala monetária, exercícios, setores
  conceitos.py              plano de contas → conceitos canônicos
  indicadores.py            motor de fórmulas
  agregacao.py              estatísticas setoriais
  snapshot.py               grava e lê o resultado em Parquet
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

## Compartilhar o painel com a equipe

O aplicativo começa vazio: quem abre precisa carregar os arquivos. Isso funciona
para quem analisa, não para quem só quer consultar. Para publicar um link em que
o painel já aparece pronto, congele o resultado num snapshot.

```bash
python gerar_snapshot.py --rotulo "DFP 2015-2025"
```

O comando roda o pipeline completo sobre `./dados` e grava `snapshot/*.parquet` —
alguns MB, contra centenas de MB dos CSVs originais. Com o snapshot presente, o
`app.py` o carrega sozinho ao abrir e mostra a origem **Painel publicado** na
barra lateral; as outras duas origens continuam funcionando para recalcular.

Para publicar no [Streamlit Community Cloud](https://share.streamlit.io):

1. `git add snapshot && git commit -m "dados: atualiza snapshot" && git push`
2. Aponte o Community Cloud para o repositório, arquivo `app.py`.
3. Quando sair o DFP do ano novo, regenere o snapshot e dê `push` — o app
   atualiza sozinho.

Os dados da CVM são públicos, então o repositório pode ser público. O plano
gratuito permite apps públicos ilimitados e apenas um app privado.

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

**Períodos nunca se misturam.** Um semestre tem metade do tempo de um exercício
fechado. Somar os dois numa mesma mediana setorial seria comparar seis meses com
doze, então o recorte entra na chave de toda estatística: a mediana do setor é
calculada dentro de (setor, ano, recorte).

**Recorte parcial não é anualizado.** Num trimestre ou semestre, indicadores que
dividem um fluxo por um saldo — ROE, ROA, ROIC, giro do ativo, dívida
líquida/EBITDA — ficam na escala do próprio período: o ROE de um trimestre é
aproximadamente um quarto do anual. Multiplicar por quatro seria uma projeção, e
projeção não é dado. O painel exibe o número como ele é e avisa. Margens e
índices de liquidez, que dividem duas grandezas do mesmo período, permanecem
diretamente comparáveis entre recortes.

**Saldo de balanço vale para a data, não para o intervalo.** O balanço de 30 de
junho serve tanto ao 1º semestre quanto ao 2º trimestre, então a linha é
replicada para os dois recortes. O saldo de abertura usado nas médias (`MED`) é o
balanço encerrado no fim do período anterior: 31 de dezembro para o 1º semestre,
31 de março para o 2º trimestre.

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

**Caixa livre distorcido por risco sacado.** Varejistas que antecipam pagamento
a fornecedores registram a entrada em atividades operacionais e a liquidação em
financiamento. O FCO — e com ele o caixa livre — infla, enquanto o financiamento
drena o mesmo valor. Magazine Luiza aparece com R$ 14,5 bi de caixa livre em 2024
e variação líquida de caixa de −R$ 0,8 bi exatamente por isso. Leia o indicador
ao lado da conta 6.05, não sozinho.

**Dividendos saem de conta livre.** A DFC não padroniza a linha de dividendos
pagos: ela aparece em dezenas de grafias dentro das atividades de financiamento.
A extração casa a descrição e soma dividendos com JCP, excluindo dividendos
*recebidos*. Cobertura de ~99% das companhias com receita, mas é estimativa.
Holdings passam de 100% da receita porque distribuem o que recebem de
controladas, não o que faturam.

**Setor é um retrato do presente.** A base cadastral é um instantâneo. O setor
atribuído a uma companhia vale para todos os anos do painel; reclassificações
setoriais passadas não são recuperáveis a partir desses arquivos.

**ROE usa o resultado consolidado.** O numerador inclui a parcela de acionistas
não controladores, assim como o denominador. Para ROE atribuível ao controlador,
subtraia `participacao_nao_controladores`, que já está extraído no painel.

**Cobertura de juros é aproximada.** A DFP não isola a despesa de juros dentro do
resultado financeiro. O indicador usa o resultado financeiro líquido, o que
subestima a cobertura de empresas com receita financeira relevante.

**Comparar trimestres entre anos exige o ITR de cada ano.** O ITR traz o mesmo
período do ano anterior na coluna `PENÚLTIMO`, mas só para as contas de
resultado: o balanço comparativo é sempre o de 31 de dezembro anterior, não o do
mesmo trimestre. Carregando só o ITR de 2026, o 1º semestre de 2025 aparece com
receita e fluxo de caixa, porém sem ativo e sem passivo — e portanto sem
liquidez, endividamento ou rentabilidade. Para a série completa, carregue o ITR
de cada ano que quiser comparar.

**Corte de denominador mínimo pesa mais nos recortes curtos.** O piso de R$ 100
mil foi calibrado para valores de exercício inteiro. Num trimestre os
denominadores são cerca de quatro vezes menores, então uma parcela maior de
companhias pequenas cai no saneamento. A contagem aparece na aba de qualidade,
aberta por recorte.

**Companhia com exercício social deslocado fica fora dos recortes parciais.** A
classificação de período assume o ano civil para trimestres e semestres. Quem
fecha o exercício em março continua íntegro no recorte anual, mas seus trimestres
fiscais começam em abril ou julho e não casam com nenhuma janela civil — o
descarte aparece contado na aba de qualidade. São cerca de dez companhias por
ano: Camil, Raízen, São Martinho, Jalles Machado, Cerradinho, CTC e assemelhadas.
A exceção é o trimestre outubro–dezembro delas, que coincide com o civil e por
isso alimenta os recortes ocultos 4T e 2S.

**Composição setorial muda entre anos.** Empresas abrem e fecham capital. Uma
variação na mediana do setor pode refletir mudança de composição, não desempenho.
A contagem de empresas acompanha todas as tabelas por esse motivo.

---

## Fonte dos dados

COMISSÃO DE VALORES MOBILIÁRIOS. *Dados Abertos CVM: Demonstrações Financeiras
Padronizadas (DFP) e Formulário Cadastral (FCA)*. Rio de Janeiro: CVM. Disponível
em: https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp. Acesso em: 25 ago. 2026.
