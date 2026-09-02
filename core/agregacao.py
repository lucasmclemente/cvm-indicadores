"""Agregacao setorial dos indicadores.

Duas leituras convivem aqui, e elas respondem perguntas diferentes:

  - MEDIANA e quartis: como se comporta a companhia tipica do setor. Robusta a
    outliers, que sao a regra em indicadores financeiros.
  - AGREGADO (soma dos numeradores / soma dos denominadores): como se comporta
    o setor visto como uma unica companhia. Dominado pelos grandes.

Media aritmetica simples nao entra: uma empresa com patrimonio liquido proximo
de zero produz um ROE de tres digitos que desloca a media do setor inteiro,
mesmo depois do saneamento.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def agregar_por_setor(
    indicadores: pd.DataFrame, cfg: dict, familia: str | None = None
) -> pd.DataFrame:
    """Consolida indicadores por setor, ano e periodo.

    O periodo entra na chave, nunca dentro do mesmo grupo: a mediana de um setor
    no 1o semestre e uma leitura, a do exercicio fechado e outra, e somar as duas
    numa unica estatistica seria comparar seis meses com doze.
    """
    if indicadores.empty:
        return pd.DataFrame()

    df = indicadores if familia is None else indicadores[indicadores["familia"] == familia]
    if df.empty:
        return pd.DataFrame()

    n_min = int(cfg.get("saneamento", {}).get("n_minimo_setor", 5))

    dist = (
        df.groupby(
            ["setor", "ano", "periodo", "indicador", "rotulo", "grupo", "formato"]
        )
        .agg(
            n_empresas=("cnpj", "nunique"),
            p25=("valor", lambda s: s.quantile(0.25)),
            mediana=("valor", "median"),
            p75=("valor", lambda s: s.quantile(0.75)),
            minimo=("valor", "min"),
            maximo=("valor", "max"),
            soma_num=("numerador", "sum"),
            soma_den=("denominador", "sum"),
            soma_simples=("agregacao_soma", "first"),
        )
        .reset_index()
    )

    # Para indicadores em reais o "agregado" e a soma direta, nao uma razao.
    dist["agregado"] = np.where(
        dist["soma_simples"],
        dist["soma_num"],
        dist["soma_num"] / dist["soma_den"].replace(0, np.nan),
    )
    dist["amplitude_interquartil"] = dist["p75"] - dist["p25"]
    dist["amostra_reduzida"] = dist["n_empresas"] < n_min

    return dist.drop(columns=["soma_simples"]).sort_values(
        ["grupo", "rotulo", "periodo", "ano", "setor"]
    ).reset_index(drop=True)


def ranking_empresas(
    indicadores: pd.DataFrame, indicador: str, ano: int,
    setor: str | None = None, periodo: str = "ano",
) -> pd.DataFrame:
    """Ordena as companhias por um indicador, opcionalmente dentro de um setor."""
    df = indicadores[
        (indicadores["indicador"] == indicador)
        & (indicadores["ano"] == ano)
        & (indicadores["periodo"] == periodo)
    ]
    if setor:
        df = df[df["setor"] == setor]
    return (
        df[["empresa", "setor", "valor", "numerador", "denominador"]]
        .sort_values("valor", ascending=False)
        .reset_index(drop=True)
    )


def posicao_relativa(indicadores: pd.DataFrame, cnpj: str) -> pd.DataFrame:
    """Compara uma companhia com a mediana do seu setor, ano a ano.

    O percentil e calculado dentro do par (setor, ano) e so faz sentido quando
    ha empresas suficientes na comparacao, por isso `n_setor` acompanha o
    resultado.
    """
    if indicadores.empty:
        return pd.DataFrame()

    grupo = indicadores.groupby(["setor", "ano", "periodo", "indicador"])["valor"]
    base = indicadores.assign(
        mediana_setor=grupo.transform("median"),
        n_setor=grupo.transform("count"),
        percentil=grupo.rank(pct=True),
    )
    alvo = base[base["cnpj"] == cnpj]
    return alvo[
        ["ano", "periodo", "grupo", "rotulo", "indicador", "formato", "valor",
         "mediana_setor", "percentil", "n_setor", "setor"]
    ].sort_values(["ano", "periodo", "grupo", "rotulo"]).reset_index(drop=True)


def formatar_valor(valor: float, formato: str) -> str:
    """Formatacao para exibicao. Reais aparecem em milhoes por legibilidade."""
    if pd.isna(valor):
        return "–"
    if formato == "percentual":
        return f"{valor * 100:,.1f}%".replace(",", "·").replace(".", ",").replace("·", ".")
    if formato == "moeda":
        texto = f"{valor / 1_000_000:,.1f}"
        return "R$ " + texto.replace(",", "·").replace(".", ",").replace("·", ".") + " mi"
    return f"{valor:,.2f}".replace(",", "·").replace(".", ",").replace("·", ".")
