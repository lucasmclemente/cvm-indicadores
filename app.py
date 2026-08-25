"""Indicadores setoriais das companhias abertas — interface Streamlit.

Executar:  streamlit run app.py
"""

from __future__ import annotations

import io
import os
import sys

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
from core.agregacao import formatar_valor, posicao_relativa, ranking_empresas  # noqa: E402

DIR_SNAPSHOT = core.snapshot.DIR_PADRAO
TEM_SNAPSHOT = core.snapshot.existe(DIR_SNAPSHOT)

TINTA = "#16202A"
VERDE = "#1F6F6B"
AMBAR = "#B4741A"
CINZA = "#8A949E"

st.set_page_config(
    page_title="Indicadores setoriais — CVM",
    page_icon="▚",
    layout="wide",
)

st.markdown(
    f"""
    <style>
      .block-container {{ padding-top: 2.2rem; max-width: 1320px; }}
      h1, h2, h3 {{ letter-spacing: -0.01em; color: {TINTA}; }}
      .eyebrow {{
        font-family: ui-monospace, "SF Mono", Menlo, monospace;
        font-size: 0.72rem; letter-spacing: 0.14em; text-transform: uppercase;
        color: {CINZA}; margin-bottom: 0.2rem;
      }}
      .nota {{ color: {CINZA}; font-size: 0.85rem; line-height: 1.5; }}
      div[data-testid="stMetricValue"] {{ font-size: 1.55rem; }}
      thead tr th {{ background: #F0F1ED !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=3)
def _rodar(arquivos: list[tuple[str, bytes]], prioridade: str):
    return core.executar(arquivos, prioridade=prioridade)


@st.cache_data(show_spinner=False, max_entries=1)
def _ler_snapshot(diretorio: str, _assinatura: float):
    """Le o painel ja calculado. A assinatura entra so como chave de cache:
    quando o snapshot e republicado, o mtime muda e o cache cai sozinho."""
    return core.snapshot.carregar(diretorio)


def _data_legivel(iso: str) -> str:
    """2026-08-25T16:40:00-03:00 -> 25/08/2026."""
    try:
        a, m, d = iso[:10].split("-")
        return f"{d}/{m}/{a}"
    except ValueError:
        return iso


def _grafico_faixa(dados: pd.DataFrame, formato: str, rotulo: str) -> alt.Chart:
    """Faixa interquartil por setor, com a mediana marcada.

    A escolha do intervalo em vez de uma barra unica e deliberada: a dispersao
    dentro do setor costuma dizer mais do que o ponto central. Um setor com
    mediana 1,4 e quartis em 0,9 e 2,6 nao e o mesmo que um com quartis em
    1,3 e 1,5, ainda que a mediana coincida.
    """
    escala = 100 if formato == "percentual" else (1 / 1_000_000 if formato == "moeda" else 1)
    sufixo = "%" if formato == "percentual" else (" (R$ mi)" if formato == "moeda" else "")
    d = dados.copy()
    for col in ["p25", "p75", "mediana", "agregado"]:
        d[col] = d[col] * escala

    ordem = alt.EncodingSortField(field="mediana", order="descending")
    base = alt.Chart(d).encode(
        y=alt.Y("setor:N", sort=ordem, title=None,
                axis=alt.Axis(labelLimit=280, labelFontSize=12))
    )
    faixa = base.mark_rule(strokeWidth=7, opacity=0.28, color=VERDE).encode(
        x=alt.X("p25:Q", title=f"{rotulo}{sufixo}"), x2="p75:Q"
    )
    mediana = base.mark_point(shape="diamond", size=110, filled=True).encode(
        x="mediana:Q",
        color=alt.condition(
            alt.datum.amostra_reduzida, alt.value(AMBAR), alt.value(TINTA)
        ),
        tooltip=[
            alt.Tooltip("setor:N", title="Setor"),
            alt.Tooltip("n_empresas:Q", title="Empresas"),
            alt.Tooltip("p25:Q", title="1º quartil", format=".2f"),
            alt.Tooltip("mediana:Q", title="Mediana", format=".2f"),
            alt.Tooltip("p75:Q", title="3º quartil", format=".2f"),
            alt.Tooltip("agregado:Q", title="Agregado do setor", format=".2f"),
        ],
    )
    return (faixa + mediana).properties(height=max(280, 26 * len(d)))


def _exportar_excel(res: core.Resultado) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        res.setorial_nao_financeira.to_excel(writer, sheet_name="Setorial não-financeiras", index=False)
        res.setorial_financeira.to_excel(writer, sheet_name="Setorial financeiras", index=False)
        res.indicadores.to_excel(writer, sheet_name="Indicadores por empresa", index=False)
        res.painel.to_excel(writer, sheet_name="Painel de conceitos", index=False)
        res.diagnostico.to_excel(writer, sheet_name="Cobertura", index=False)
        res.planos.to_excel(writer, sheet_name="Planos de contas", index=False)
        if not res.conflitos.empty:
            res.conflitos.head(5000).to_excel(writer, sheet_name="Reapresentações", index=False)
    return buffer.getvalue()


# ----------------------------------------------------------------------------
# Barra lateral — carga dos dados
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown('<div class="eyebrow">Bases</div>', unsafe_allow_html=True)
    st.markdown("### Carregar dados")

    origens = ["Enviar arquivos", "Ler de uma pasta"]
    if TEM_SNAPSHOT:
        origens.insert(0, "Painel publicado")

    modo = st.radio(
        "Origem dos arquivos",
        origens,
        label_visibility="collapsed",
    )

    arquivos: list[tuple[str, bytes]] = []
    if modo == "Painel publicado":
        meta = core.snapshot.ler_meta(DIR_SNAPSHOT)
        anos_pub = meta.get("anos") or []
        if anos_pub:
            st.caption(
                f"Exercícios {min(anos_pub)}–{max(anos_pub)} · "
                f"{meta.get('companhias', 0)} companhias · "
                f"{meta.get('setores', 0)} setores"
            )
        if meta.get("rotulo"):
            st.caption(meta["rotulo"])
        st.caption(f"Publicado em {_data_legivel(meta.get('gerado_em', ''))}.")
        st.caption(
            "Para recalcular a partir dos arquivos da CVM, troque a origem acima."
        )
    elif modo == "Enviar arquivos":
        enviados = st.file_uploader(
            "CSVs da CVM ou o ZIP anual completo",
            type=["csv", "zip"],
            accept_multiple_files=True,
            help="Aceita dfp_cia_aberta_BPA/BPP/DRE/DFC_*_con de qualquer ano, "
                 "mais a base cadastral. Arquivos de DF individual são ignorados.",
        )
        arquivos = [(f.name, f.getvalue()) for f in enviados] if enviados else []
    else:
        pasta = st.text_input("Caminho da pasta", value="./dados")
        if pasta and os.path.isdir(pasta):
            for raiz, _d, nomes in os.walk(pasta):
                for nome in nomes:
                    if nome.lower().endswith((".csv", ".zip")):
                        with open(os.path.join(raiz, nome), "rb") as fh:
                            arquivos.append((nome, fh.read()))
            st.caption(f"{len(arquivos)} arquivo(s) encontrado(s).")
        elif pasta:
            st.warning("Pasta não encontrada.")

    st.divider()
    st.markdown('<div class="eyebrow">Regra de desempate</div>', unsafe_allow_html=True)
    prioridade = st.radio(
        "Quando o mesmo exercício aparece em dois arquivos",
        ["ultimo", "reapresentado"],
        format_func=lambda x: (
            "Número original do ano" if x == "ultimo" else "Número reapresentado"
        ),
        help="Cada arquivo anual traz dois exercícios. Ao carregar vários anos, "
             "o exercício N aparece como ÚLTIMO no arquivo N e como PENÚLTIMO no "
             "arquivo N+1, às vezes com valor diferente por reapresentação contábil.",
    )

    if modo == "Painel publicado":
        processar = False
    else:
        processar = st.button("Processar bases", type="primary",
                              use_container_width=True, disabled=not arquivos)

if processar:
    with st.spinner("Lendo, normalizando e calculando…"):
        st.session_state["res"] = _rodar(arquivos, prioridade)
elif st.session_state.get("res") is None and TEM_SNAPSHOT:
    with st.spinner("Carregando painel publicado…"):
        st.session_state["res"] = _ler_snapshot(
            DIR_SNAPSHOT, core.snapshot.assinatura(DIR_SNAPSHOT)
        )

res: core.Resultado | None = st.session_state.get("res")

# ----------------------------------------------------------------------------
# Tela inicial
# ----------------------------------------------------------------------------
if res is None:
    st.markdown('<div class="eyebrow">CVM · Demonstrações Financeiras Padronizadas</div>',
                unsafe_allow_html=True)
    st.title("Indicadores setoriais de companhias abertas")
    st.markdown(
        """
        Envie os arquivos DFP de quantos anos quiser. O sistema empilha as bases,
        resolve as diferenças entre os três planos de contas da CVM e devolve
        liquidez, margens, endividamento e rentabilidade por setor.

        **O que ele trata sozinho**

        | Armadilha | Tratamento |
        |---|---|
        | Mesma conta com significados diferentes por tipo de empresa | Plano de contas detectado pela estrutura do balanço |
        | Versões múltiplas do mesmo documento | Última versão vence |
        | Escala em MIL e em UNIDADE na mesma coluna | Tudo convertido para reais |
        | Dois exercícios por arquivo anual | Desduplicação com regra explícita |
        | Setores duplicados por holdings | 53 rótulos consolidados em ~29 setores |

        Comece pela barra lateral.
        """
    )
    st.stop()

if res.painel.empty:
    st.error("Nenhum dado contábil foi reconhecido nos arquivos enviados.")
    st.dataframe(res.log_ingestao, use_container_width=True, hide_index=True)
    st.stop()

# ----------------------------------------------------------------------------
# Cabeçalho
# ----------------------------------------------------------------------------
anos = res.anos
n_emp = res.painel["cnpj"].nunique()
n_fin = res.painel.loc[res.painel["familia"] == "financeira", "cnpj"].nunique()

st.markdown('<div class="eyebrow">CVM · Demonstrações Financeiras Padronizadas</div>',
            unsafe_allow_html=True)
st.title("Indicadores setoriais de companhias abertas")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Companhias", f"{n_emp:,}".replace(",", "."))
c2.metric("Exercícios", f"{min(anos)}–{max(anos)}" if len(anos) > 1 else str(anos[0]))
c3.metric("Setores", res.painel["setor"].nunique())
c4.metric("Financeiras (à parte)", n_fin)

abas = st.tabs([
    "Panorama setorial", "Evolução", "Empresa",
    "Financeiras", "Qualidade dos dados", "Exportar",
])

setorial = res.setorial_nao_financeira

# ----------------------------------------------------------------------------
# 1. Panorama setorial
# ----------------------------------------------------------------------------
with abas[0]:
    if setorial.empty:
        st.info("Sem indicadores para companhias não-financeiras nas bases carregadas.")
    else:
        opcoes = (
            setorial[["indicador", "rotulo", "grupo"]]
            .drop_duplicates().sort_values(["grupo", "rotulo"])
        )
        e1, e2, e3 = st.columns([2, 2, 1])
        grupo = e1.selectbox("Grupo", sorted(opcoes["grupo"].unique()))
        sub = opcoes[opcoes["grupo"] == grupo]
        rotulo = e2.selectbox("Indicador", sub["rotulo"].tolist())
        ano = e3.selectbox("Exercício", sorted(setorial["ano"].unique(), reverse=True))

        chave = sub.loc[sub["rotulo"] == rotulo, "indicador"].iloc[0]
        dados = setorial[(setorial["indicador"] == chave) & (setorial["ano"] == ano)]
        n_min = st.slider("Mínimo de empresas por setor", 1, 20, 3)
        dados = dados[dados["n_empresas"] >= n_min]

        spec = core.carregar_config()["indicadores"]["indicadores"][chave]
        st.caption(spec.get("interpretacao", ""))

        if dados.empty:
            st.info("Nenhum setor atende ao corte de amostra escolhido.")
        else:
            formato = dados["formato"].iloc[0]
            st.altair_chart(_grafico_faixa(dados, formato, rotulo), use_container_width=True)
            st.markdown(
                f'<div class="nota">A barra vai do 1º ao 3º quartil; o losango é a '
                f'mediana. Losango em <span style="color:{AMBAR}">âmbar</span> marca '
                f'setor com menos de 5 empresas, onde a mediana é frágil.</div>',
                unsafe_allow_html=True,
            )

            st.markdown("#### Tabela")
            tabela = dados[["setor", "n_empresas", "p25", "mediana", "p75", "agregado"]].copy()
            for col in ["p25", "mediana", "p75", "agregado"]:
                tabela[col] = tabela[col].map(lambda v: formatar_valor(v, formato))
            tabela.columns = ["Setor", "Empresas", "1º quartil", "Mediana",
                              "3º quartil", "Agregado do setor"]
            st.dataframe(tabela.sort_values("Setor"), use_container_width=True,
                         hide_index=True)
            st.markdown(
                '<div class="nota">"Agregado do setor" soma numeradores e '
                'denominadores de todas as empresas: é o setor lido como uma única '
                'companhia, portanto dominado pelas maiores. Divergência grande '
                'entre mediana e agregado indica setor concentrado.</div>',
                unsafe_allow_html=True,
            )

            st.markdown("#### Empresas do setor")
            setor_alvo = st.selectbox("Setor", sorted(dados["setor"].unique()))
            rk = ranking_empresas(res.indicadores, chave, ano, setor_alvo)
            rk["valor"] = rk["valor"].map(lambda v: formatar_valor(v, formato))
            st.dataframe(
                rk[["empresa", "valor"]].rename(columns={"empresa": "Companhia",
                                                         "valor": rotulo}),
                use_container_width=True, hide_index=True, height=320,
            )

# ----------------------------------------------------------------------------
# 2. Evolução
# ----------------------------------------------------------------------------
with abas[1]:
    if len(anos) < 2:
        st.info(
            "A evolução precisa de pelo menos dois exercícios. Carregue os arquivos "
            "DFP de outros anos na barra lateral."
        )
    elif setorial.empty:
        st.info("Sem dados setoriais.")
    else:
        opcoes = setorial[["indicador", "rotulo"]].drop_duplicates().sort_values("rotulo")
        rotulo = st.selectbox("Indicador", opcoes["rotulo"].tolist(), key="ev_ind")
        chave = opcoes.loc[opcoes["rotulo"] == rotulo, "indicador"].iloc[0]
        serie = setorial[setorial["indicador"] == chave]

        maiores = (
            serie.groupby("setor")["n_empresas"].max().nlargest(8).index.tolist()
        )
        escolhidos = st.multiselect(
            "Setores", sorted(serie["setor"].unique()), default=maiores[:5]
        )
        d = serie[serie["setor"].isin(escolhidos)].copy()
        if d.empty:
            st.info("Selecione ao menos um setor.")
        else:
            formato = d["formato"].iloc[0]
            escala = 100 if formato == "percentual" else (
                1 / 1_000_000 if formato == "moeda" else 1)
            d["mediana"] = d["mediana"] * escala
            grafico = (
                alt.Chart(d)
                .mark_line(point=alt.OverlayMarkDef(size=55), strokeWidth=2)
                .encode(
                    x=alt.X("ano:O", title="Exercício"),
                    y=alt.Y("mediana:Q", title=f"Mediana — {rotulo}"),
                    color=alt.Color("setor:N", title=None,
                                    legend=alt.Legend(orient="bottom", columns=2)),
                    tooltip=["setor", "ano",
                             alt.Tooltip("mediana:Q", format=".2f"),
                             alt.Tooltip("n_empresas:Q", title="Empresas")],
                )
                .properties(height=430)
            )
            st.altair_chart(grafico, use_container_width=True)
            st.markdown(
                '<div class="nota">A composição de cada setor muda ano a ano '
                '(empresas que abrem ou fecham capital). Confira a coluna de '
                'empresas antes de ler uma variação como tendência do setor.</div>',
                unsafe_allow_html=True,
            )

# ----------------------------------------------------------------------------
# 3. Empresa
# ----------------------------------------------------------------------------
with abas[2]:
    empresas = res.painel[["cnpj", "empresa", "setor"]].drop_duplicates("cnpj")
    empresas = empresas.sort_values("empresa")
    nome = st.selectbox("Companhia", empresas["empresa"].tolist())
    cnpj = empresas.loc[empresas["empresa"] == nome, "cnpj"].iloc[0]
    linha = res.painel[res.painel["cnpj"] == cnpj].iloc[-1]

    m1, m2, m3 = st.columns(3)
    m1.metric("Setor", linha["setor"])
    m2.metric("Plano de contas", linha["plano_rotulo"])
    m3.metric("CNPJ", cnpj)

    comp = posicao_relativa(res.indicadores, cnpj)
    if comp.empty:
        st.info("Sem indicadores calculáveis para esta companhia.")
    else:
        ano_emp = st.selectbox("Exercício", sorted(comp["ano"].unique(), reverse=True),
                               key="emp_ano")
        c = comp[comp["ano"] == ano_emp].copy()
        c["Empresa"] = [formatar_valor(v, f) for v, f in zip(c["valor"], c["formato"])]
        c["Mediana do setor"] = [
            formatar_valor(v, f) for v, f in zip(c["mediana_setor"], c["formato"])
        ]
        c["Percentil no setor"] = (c["percentil"] * 100).round(0).astype("Int64").astype(str) + "º"
        c.loc[c["n_setor"] < 5, "Percentil no setor"] = "amostra < 5"
        st.dataframe(
            c[["grupo", "rotulo", "Empresa", "Mediana do setor",
               "Percentil no setor", "n_setor"]]
            .rename(columns={"grupo": "Grupo", "rotulo": "Indicador",
                             "n_setor": "Empresas na comparação"}),
            use_container_width=True, hide_index=True, height=560,
        )

    with st.expander("Conceitos contábeis extraídos"):
        conceitos = res.painel[res.painel["cnpj"] == cnpj].set_index("ano")
        numericas = conceitos.select_dtypes("number").drop(columns=["ano"], errors="ignore")
        st.dataframe((numericas.T / 1_000_000).round(2), use_container_width=True)
        st.caption("Valores em R$ milhões.")

# ----------------------------------------------------------------------------
# 4. Financeiras
# ----------------------------------------------------------------------------
with abas[3]:
    st.markdown(
        "Bancos, seguradoras e assemelhados aparecem aqui, e não no panorama "
        "principal, porque o balanço deles não segrega circulante e não circulante. "
        "Liquidez corrente e margem bruta simplesmente não existem nesse plano de "
        "contas; forçar o cálculo produziria número, não informação."
    )
    fin = res.setorial_financeira
    if fin.empty:
        st.info("Nenhuma companhia financeira nas bases carregadas.")
    else:
        f1, f2 = st.columns(2)
        opcoes = fin[["indicador", "rotulo"]].drop_duplicates().sort_values("rotulo")
        rotulo = f1.selectbox("Indicador", opcoes["rotulo"].tolist(), key="fin_ind")
        ano_f = f2.selectbox("Exercício", sorted(fin["ano"].unique(), reverse=True),
                             key="fin_ano")
        chave = opcoes.loc[opcoes["rotulo"] == rotulo, "indicador"].iloc[0]

        emp = res.indicadores[
            (res.indicadores["familia"] == "financeira")
            & (res.indicadores["indicador"] == chave)
            & (res.indicadores["ano"] == ano_f)
        ].copy()
        if emp.empty:
            st.info("Sem observações para este recorte.")
        else:
            formato = emp["formato"].iloc[0]
            escala = 100 if formato == "percentual" else 1
            emp["v"] = emp["valor"] * escala
            grafico = (
                alt.Chart(emp)
                .mark_bar(color=VERDE, opacity=0.85, height=17)
                .encode(
                    x=alt.X("v:Q", title=rotulo),
                    y=alt.Y("empresa:N", sort="-x", title=None,
                            axis=alt.Axis(labelLimit=300)),
                    tooltip=["empresa", "setor", alt.Tooltip("v:Q", format=".2f")],
                )
                .properties(height=max(240, 24 * len(emp)))
            )
            st.altair_chart(grafico, use_container_width=True)

            tab = emp[["empresa", "setor", "valor"]].copy()
            tab["valor"] = tab["valor"].map(lambda v: formatar_valor(v, formato))
            st.dataframe(
                tab.rename(columns={"empresa": "Companhia", "setor": "Setor",
                                    "valor": rotulo}),
                use_container_width=True, hide_index=True,
            )

# ----------------------------------------------------------------------------
# 5. Qualidade dos dados
# ----------------------------------------------------------------------------
with abas[4]:
    st.markdown("#### Leitura dos arquivos")
    st.dataframe(res.log_ingestao, use_container_width=True, hide_index=True)

    st.markdown("#### Cobertura por indicador")
    st.caption(
        "Cobertura baixa não significa erro: quer dizer que muitas companhias não "
        "têm o conceito necessário na base carregada. EBITDA e derivados dependem "
        "da DFC, cujo método indireto vem em arquivo separado."
    )
    diag = res.diagnostico.copy()
    diag["cobertura"] = (diag["cobertura"] * 100).round(1).astype(str) + "%"
    st.dataframe(
        diag.rename(columns={
            "rotulo": "Indicador", "empresas_aplicaveis": "Empresas aplicáveis",
            "observacoes_com_dados": "Obs. com dados",
            "descartes_denominador": "Descartes (denominador)",
            "descartes_outlier": "Descartes (limite)",
            "observacoes_validas": "Obs. válidas", "cobertura": "Cobertura",
        }).drop(columns=["indicador"]),
        use_container_width=True, hide_index=True, height=400,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Planos de contas detectados")
        st.dataframe(
            res.planos["plano_rotulo"].value_counts().rename_axis("Plano")
            .reset_index(name="Companhias"),
            use_container_width=True, hide_index=True,
        )
        desconhecidos = res.planos[res.planos["plano"] == "desconhecido"]
        if not desconhecidos.empty:
            st.warning(
                f"{len(desconhecidos)} companhia(s) sem plano identificado — ficam "
                "fora de todos os indicadores."
            )

    with c2:
        st.markdown("#### Companhias sem cadastro")
        sem_cad = res.painel[res.painel["setor"] == "Sem classificação cadastral"]
        sem_cad = sem_cad[["cnpj", "empresa"]].drop_duplicates()
        if sem_cad.empty:
            st.success("Todas as companhias foram classificadas por setor.")
        else:
            st.dataframe(sem_cad, use_container_width=True, hide_index=True)
            st.caption(
                "Presentes nas demonstrações, ausentes da base cadastral. Carregue "
                "uma base cadastral mais recente para classificá-las."
            )

    st.markdown("#### Reapresentações detectadas")
    if res.conflitos.empty:
        st.success(
            "Nenhum exercício apareceu com valores divergentes entre arquivos. "
            "Com um único ano carregado isso é esperado."
        )
    else:
        st.caption(
            f"{len(res.conflitos)} conta(s) mudaram de valor entre a divulgação "
            "original e a reapresentação. As 200 maiores divergências:"
        )
        st.dataframe(res.conflitos.head(200), use_container_width=True, hide_index=True)

# ----------------------------------------------------------------------------
# 6. Exportar
# ----------------------------------------------------------------------------
with abas[5]:
    st.markdown(
        "Todas as tabelas do painel, mais os artefatos de auditoria, em um arquivo."
    )
    st.download_button(
        "Baixar planilha completa",
        data=_exportar_excel(res),
        file_name=f"indicadores_setoriais_{min(anos)}_{max(anos)}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
    st.divider()
    col1, col2 = st.columns(2)
    col1.download_button(
        "Setorial não-financeiras (CSV)",
        res.setorial_nao_financeira.to_csv(index=False).encode("utf-8-sig"),
        "setorial_nao_financeiras.csv", "text/csv", use_container_width=True,
    )
    col2.download_button(
        "Indicadores por empresa (CSV)",
        res.indicadores.to_csv(index=False).encode("utf-8-sig"),
        "indicadores_empresa.csv", "text/csv", use_container_width=True,
    )
