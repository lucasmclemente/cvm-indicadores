"""Normalizacao dos fatos contabeis.

Trata as armadilhas estruturais dos arquivos da CVM:
  1. VERSAO: o mesmo documento pode aparecer em varias versoes no mesmo arquivo.
  2. ESCALA_MOEDA: parte das linhas vem em MIL e parte em UNIDADE.
  3. ORDEM_EXERC: cada arquivo traz dois exercicios (ULTIMO e PENULTIMO),
     de modo que empilhar N anos gera N-1 duplicidades de exercicio.
  4. PERIODO: a DFP e anual, o ITR e trimestral. Sem separar os dois, um
     semestre entraria no painel como se fosse ano fechado.
"""

from __future__ import annotations

import unicodedata

import pandas as pd

ESCALAS = {"MIL": 1_000.0, "UNIDADE": 1.0, "MILHAR": 1_000.0, "MILHAO": 1_000_000.0}

DEMONSTRACOES_FLUXO = ["DRE", "DFC", "DVA"]

# Qual documento manda quando os dois cobrem a mesma demonstracao no mesmo
# periodo. A DFP vence porque e a peca anual auditada; o ITR entra onde a DFP
# nao chega, que e justamente o recorte intermediario.
PREFERENCIA_ORIGEM = {"DFP": 0, "ITR": 1}

# --- Periodos ---------------------------------------------------------------
# So existe aqui o que a CVM publica. A DFP traz o exercicio fechado; o ITR traz
# o acumulado do ano ate o trimestre e, a partir do segundo, tambem o trimestre
# isolado. Nada e derivado por subtracao, entao o 4o trimestre isolado e o 2o
# semestre nao chegam a existir na pratica: a CVM nao os publica e o projeto nao
# estima o que nao tem. Os dois rotulos ficam declarados porque uma companhia
# com exercicio social deslocado pode produzi-los.
PERIODOS: dict[str, dict] = {
    "ano": {"rotulo": "Ano fechado",  "meses": 12, "ordem": 0, "recortes": ["acumulado"]},
    "1T": {"rotulo": "1º trimestre",  "meses": 3,  "ordem": 1, "recortes": ["acumulado", "trimestre"]},
    "1S": {"rotulo": "1º semestre",   "meses": 6,  "ordem": 2, "recortes": ["acumulado"]},
    "9M": {"rotulo": "9 meses",       "meses": 9,  "ordem": 3, "recortes": ["acumulado"]},
    "2T": {"rotulo": "2º trimestre",  "meses": 3,  "ordem": 4, "recortes": ["trimestre"]},
    "3T": {"rotulo": "3º trimestre",  "meses": 3,  "ordem": 5, "recortes": ["trimestre"]},
    "4T": {"rotulo": "4º trimestre",  "meses": 3,  "ordem": 6, "recortes": ["trimestre"],
           "oculto": True},
    "2S": {"rotulo": "2º semestre",   "meses": 6,  "ordem": 7, "recortes": ["acumulado"],
           "oculto": True},
}

# `oculto` nao apaga o dado: a observacao continua no painel e na planilha
# exportada. Ela apenas nao e oferecida como recorte comparavel, porque 4T e 2S
# nascem de efeito colateral do calendario. A CVM nao publica o 4o trimestre nem
# o 2o semestre; os dois so aparecem para a duzia de companhias de exercicio
# social deslocado - usinas de acucar, sobretudo - cujo trimestre
# outubro-dezembro por acaso coincide com o trimestre civil. Como corte setorial
# seriam uma amostra de 18 e 5 companhias lida como se fosse o mercado.

# Janela de datas -> periodo. A tolerancia existe porque exercicio social nao
# comeca sempre no dia primeiro e ano bissexto muda a contagem.
#   (dias minimos, dias maximos, mes de inicio -> periodo)
# O mapa None significa "qualquer mes de inicio", usado no exercicio fechado:
# companhia com exercicio social deslocado (abril a marco) continua valendo como
# ano, que era o comportamento antes de existirem periodos.
JANELAS: list[tuple[int, int, dict[int, str] | None]] = [
    (300, 400, None),
    (255, 290, {1: "9M"}),
    (165, 200, {1: "1S", 7: "2S"}),
    (75, 105, {1: "1T", 4: "2T", 7: "3T", 10: "4T"}),
]


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c)
    ).upper().strip()


def rotulo_periodo(periodo: str) -> str:
    """Nome de exibicao de um periodo, com o proprio codigo como reserva."""
    return PERIODOS.get(periodo, {}).get("rotulo", periodo)


def ordenar_periodos(periodos) -> list[str]:
    """Ordena periodos do mais longo para o mais curto, como na tabela acima."""
    return sorted(periodos, key=lambda p: PERIODOS.get(p, {}).get("ordem", 99))


def periodos_visiveis(periodos) -> list[str]:
    """Os recortes que valem como comparacao setorial, ja ordenados.

    Se sobrar nenhum - carga que so tenha 4T, por exemplo - devolve o que veio,
    porque um painel vazio esconderia mais do que informa.
    """
    ordenados = ordenar_periodos(periodos)
    visiveis = [p for p in ordenados if not PERIODOS.get(p, {}).get("oculto")]
    return visiveis or ordenados


def _classificar_janela(duracao: pd.Series, mes_inicio: pd.Series) -> pd.Series:
    """Traduz (duracao em dias, mes de inicio) no rotulo do periodo."""
    periodo = pd.Series(pd.NA, index=duracao.index, dtype="object")
    for minimo, maximo, mapa in JANELAS:
        alvo = duracao.between(minimo, maximo) & periodo.isna()
        if not bool(alvo.any()):
            continue
        if mapa is None:
            periodo[alvo] = "ano"
        else:
            periodo[alvo] = mes_inicio[alvo].map(mapa)
    return periodo


def _periodo_dos_estoques(
    estoque: pd.DataFrame, fluxo: pd.DataFrame
) -> tuple[pd.DataFrame, int]:
    """Atribui periodo as contas de balanco, que nao trazem data de inicio.

    Um saldo e a fotografia de uma data, nao um intervalo: o mesmo balanco de 30
    de junho serve ao primeiro semestre e ao segundo trimestre. Por isso a linha
    e replicada para cada periodo da companhia que termina naquela data.

    Sem nenhuma demonstracao de fluxo naquela data, o saldo so e aceito como ano
    fechado se cair no mes em que a companhia costuma encerrar o exercicio - e o
    que mantem no painel a companhia que entregou balanco sem DRE. Fora disso ele
    e descartado, e o descarte volta contado.
    """
    if estoque.empty:
        estoque["periodo"] = pd.Series(dtype="object")
        return estoque, 0

    mapa = fluxo[["CNPJ_CIA", "dt_fim", "periodo"]].drop_duplicates()
    casado = estoque.merge(mapa, on=["CNPJ_CIA", "dt_fim"], how="left")

    anuais = fluxo[fluxo["periodo"] == "ano"]
    if anuais.empty:
        fechamento = pd.Series(dtype="float64")
    else:
        fechamento = anuais.groupby("CNPJ_CIA")["dt_fim"].agg(
            lambda s: s.dt.month.mode().iat[0]
        )

    no_fechamento = (
        casado["dt_fim"].dt.month == casado["CNPJ_CIA"].map(fechamento).fillna(12)
    )
    sem_fluxo = casado["periodo"].isna()
    casado.loc[sem_fluxo & no_fechamento, "periodo"] = "ano"
    orfas = int((sem_fluxo & ~no_fechamento).sum())

    validos = casado[casado["periodo"].notna()].copy()
    validos["_fechamento"] = no_fechamento.reindex(validos.index, fill_value=False)

    # O balanco da data de encerramento vale como exercicio fechado mesmo quando
    # um trimestre termina no mesmo dia: 31 de dezembro e ao mesmo tempo o fim
    # do 4o trimestre civil e o fim do exercicio. Sem esta copia, a companhia que
    # entregou balanco anual sem DRE sumiria do recorte anual assim que o ITR
    # trouxesse um trimestre encerrado na mesma data.
    extras = validos[validos["_fechamento"] & (validos["periodo"] != "ano")].copy()
    extras["periodo"] = "ano"

    saida = pd.concat([validos, extras], ignore_index=True)
    return saida.drop(columns=["_fechamento"]), orfas


def normalizar_fatos(
    demonstracoes: dict[str, pd.DataFrame],
    prioridade: str = "ultimo",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Empilha e desduplica as demonstracoes num unico painel de fatos.

    Args:
        demonstracoes: saida de core.ingestao.ingerir.
        prioridade: qual fonte vence quando o mesmo exercicio aparece em mais
            de um arquivo.
            "ultimo"        -> usa o numero como divulgado no proprio ano
                               (coluna ORDEM_EXERC = ULTIMO).
            "reapresentado" -> usa o numero mais recente disponivel, que pode
                               ter sido reapresentado no ano seguinte.

    Returns:
        (fatos, conflitos, descartes) onde `fatos` tem uma linha por
        (cnpj, ano, periodo, demonstracao, cd_conta), `conflitos` lista os casos
        em que as duas fontes divergiram e `descartes` conta o que ficou de fora
        na classificacao de periodo.
    """
    vazio_descartes = pd.DataFrame(columns=["motivo", "linhas"])
    if not demonstracoes:
        return pd.DataFrame(), pd.DataFrame(), vazio_descartes

    df = pd.concat(demonstracoes.values(), ignore_index=True)

    # --- tipagem -------------------------------------------------------------
    df["valor_bruto"] = pd.to_numeric(
        df["VL_CONTA"].astype(str).str.replace(",", ".", regex=False), errors="coerce"
    )
    df["versao"] = pd.to_numeric(df["VERSAO"], errors="coerce").fillna(0).astype(int)
    df["dt_refer"] = pd.to_datetime(df["DT_REFER"], errors="coerce", dayfirst=False)
    df["dt_fim"] = pd.to_datetime(df["DT_FIM_EXERC"], errors="coerce", dayfirst=False)
    df["dt_ini"] = pd.to_datetime(df["DT_INI_EXERC"], errors="coerce", dayfirst=False)
    df = df.dropna(subset=["valor_bruto", "dt_fim", "CNPJ_CIA", "CD_CONTA"])

    # --- 2. escala monetaria -------------------------------------------------
    fator = df["ESCALA_MOEDA"].map(lambda x: ESCALAS.get(_sem_acento(x), 1.0))
    df["valor"] = df["valor_bruto"] * fator

    # --- exercicio de referencia --------------------------------------------
    df["ano"] = df["dt_fim"].dt.year

    # --- 4. periodo ----------------------------------------------------------
    contagem: list[dict] = []
    e_fluxo = df["DEMONSTRACAO"].isin(DEMONSTRACOES_FLUXO)
    fluxo = df[e_fluxo].copy()
    duracao = (fluxo["dt_fim"] - fluxo["dt_ini"]).dt.days
    fluxo["periodo"] = _classificar_janela(duracao, fluxo["dt_ini"].dt.month)

    # Layout antigo da DFP omitia DT_INI_EXERC. Sem intervalo nao ha janela para
    # medir, e a unica leitura possivel e a que valia antes de o ITR entrar:
    # arquivo anual, exercicio fechado.
    sem_intervalo = fluxo["periodo"].isna() & fluxo["dt_ini"].isna()
    n_sem_intervalo = int(sem_intervalo.sum())
    if n_sem_intervalo:
        contagem.append({
            "motivo": "Sem data de inicio - assumido ano fechado",
            "linhas": n_sem_intervalo,
        })
        fluxo.loc[sem_intervalo, "periodo"] = "ano"

    n_fora = int(fluxo["periodo"].isna().sum())
    if n_fora:
        contagem.append({
            "motivo": "Janela de datas nao reconhecida - descartado",
            "linhas": n_fora,
        })
    fluxo = fluxo[fluxo["periodo"].notna()]

    estoque, orfas = _periodo_dos_estoques(df[~e_fluxo].copy(), fluxo)
    if orfas:
        contagem.append({
            "motivo": "Saldo de balanco sem periodo correspondente - descartado",
            "linhas": orfas,
        })

    df = pd.concat([fluxo, estoque], ignore_index=True)

    # Uma demonstracao de um periodo vem de um documento so. Os dois pacotes da
    # CVM se sobrepoem: o ITR carrega algumas janelas anuais, e a MESMA conta
    # pode chegar com CD_CONTA diferente em cada um - o que nao e duplicidade
    # para a desduplicacao (a chave inclui o codigo), mas vira soma dobrada nos
    # conceitos heuristicos, que casam pela descricao. Misturar as duas fontes
    # tambem juntaria numeros de safras de reapresentacao diferentes.
    if "ORIGEM" in df.columns:
        rank = df["ORIGEM"].map(PREFERENCIA_ORIGEM).fillna(99)
        melhor = rank.groupby(
            [df["CNPJ_CIA"], df["ano"], df["periodo"], df["DEMONSTRACAO"]]
        ).transform("min")
        preterida = rank > melhor
        n_preterida = int(preterida.sum())
        if n_preterida:
            contagem.append({
                "motivo": "Linha de ITR preterida - a DFP cobre a mesma janela",
                "linhas": n_preterida,
            })
            df = df[~preterida]

    descartes = pd.DataFrame(contagem) if contagem else vazio_descartes

    # --- 3. ORDEM_EXERC ------------------------------------------------------
    ordem = df["ORDEM_EXERC"].map(_sem_acento)
    if prioridade == "reapresentado":
        # o arquivo mais recente vence, seja qual for a ordem do exercicio
        df["prioridade"] = 0
    else:
        df["prioridade"] = (ordem != "ULTIMO").astype(int)

    # --- 1. versao -----------------------------------------------------------
    df = df.sort_values(
        ["prioridade", "dt_refer", "versao"], ascending=[True, False, False]
    )

    chave = ["CNPJ_CIA", "ano", "periodo", "DEMONSTRACAO", "CD_CONTA", "DS_CONTA"]
    conflitos = _detectar_conflitos(df, chave)
    fatos = df.drop_duplicates(subset=chave, keep="first")

    fatos = fatos.rename(
        columns={
            "CNPJ_CIA": "cnpj", "DENOM_CIA": "denominacao", "CD_CVM": "cd_cvm",
            "CD_CONTA": "cd_conta", "DS_CONTA": "ds_conta",
            "ST_CONTA_FIXA": "conta_fixa", "DEMONSTRACAO": "demonstracao",
            "ORIGEM": "origem",
        }
    )
    if "origem" not in fatos.columns:
        fatos["origem"] = "DFP"
    colunas = [
        "cnpj", "cd_cvm", "denominacao", "ano", "periodo", "dt_fim", "origem",
        "demonstracao", "cd_conta", "ds_conta", "conta_fixa", "valor",
        "versao", "dt_refer",
    ]
    return fatos[colunas].reset_index(drop=True), conflitos, descartes


def _detectar_conflitos(df: pd.DataFrame, chave: list[str]) -> pd.DataFrame:
    """Identifica exercicios cujo valor mudou entre a divulgacao original e a
    reapresentacao. Divergencia relevante costuma indicar reapresentacao
    contabil, e o usuario precisa saber que ela existe."""
    grupo = df.groupby(chave, dropna=False)["valor"]
    resumo = grupo.agg(["first", "last", "nunique", "size"])
    divergentes = resumo[(resumo["nunique"] > 1)].copy()
    if divergentes.empty:
        return pd.DataFrame(
            columns=[*chave, "valor_utilizado", "valor_alternativo", "variacao_pct"]
        )
    divergentes = divergentes.reset_index().rename(
        columns={"first": "valor_utilizado", "last": "valor_alternativo"}
    )
    base = divergentes["valor_utilizado"].abs().replace(0, pd.NA)
    divergentes["variacao_pct"] = (
        (divergentes["valor_alternativo"] - divergentes["valor_utilizado"]) / base
    )
    return divergentes.drop(columns=["nunique", "size"]).sort_values(
        "variacao_pct", key=lambda s: s.abs(), ascending=False
    )


def normalizar_cadastro(cadastro: pd.DataFrame | None, cfg_setores: dict) -> pd.DataFrame:
    """Consolida o cadastro e normaliza os setores.

    O cadastro da CVM e um retrato do momento da extracao, nao uma serie
    historica: o setor atribuido a uma companhia vale para todos os anos do
    painel. Reclassificacoes setoriais passadas nao sao recuperaveis por aqui.
    """
    colunas = ["cnpj", "nome_empresarial", "setor_original", "setor",
               "controle_acionario", "situacao_emissor", "familia_cadastral"]
    if cadastro is None or cadastro.empty:
        return pd.DataFrame(columns=colunas)

    cad = cadastro.rename(columns=lambda c: c.strip())
    cad = cad.rename(
        columns={
            "CNPJ_Companhia": "cnpj",
            "Nome_Empresarial": "nome_empresarial",
            "Setor_Atividade": "setor_original",
            "Especie_Controle_Acionario": "controle_acionario",
            "Situacao_Emissor": "situacao_emissor",
            "Data_Referencia": "data_referencia",
        }
    )
    for col in ["controle_acionario", "situacao_emissor", "data_referencia"]:
        if col not in cad.columns:
            cad[col] = pd.NA

    # Mantem o registro cadastral mais recente de cada companhia.
    cad["_ord"] = pd.to_datetime(cad["data_referencia"], errors="coerce", dayfirst=True)
    cad = cad.sort_values("_ord", ascending=False).drop_duplicates("cnpj", keep="first")

    prefixo = cfg_setores.get("prefixo_holding", "")
    aliases = cfg_setores.get("aliases", {})
    holding_pura = cfg_setores.get("setor_holding_pura")
    rotulo_holding = cfg_setores.get("rotulo_holding_pura", "Holdings")

    def _normalizar(setor) -> str:
        if pd.isna(setor):
            return cfg_setores.get("setor_desconhecido", "Sem classificacao")
        setor = str(setor).strip()
        if holding_pura and setor == holding_pura:
            return rotulo_holding
        if prefixo and setor.startswith(prefixo):
            setor = setor[len(prefixo):].strip()
        return aliases.get(setor, setor)

    cad["setor"] = cad["setor_original"].map(_normalizar)
    financeiros = set(cfg_setores.get("setores_financeiros", []))
    cad["familia_cadastral"] = cad["setor"].map(
        lambda s: "financeira" if s in financeiros else "nao_financeira"
    )
    return cad[colunas].reset_index(drop=True)
