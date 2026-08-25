"""Traducao do plano de contas da CVM para conceitos canonicos.

Este e o modulo que impede o erro mais caro do dataset: tratar CD_CONTA como
se tivesse significado unico. A conta 2.03 e patrimonio liquido numa industria
e provisoes num banco. Aqui cada companhia recebe um plano de contas detectado,
e a traducao de codigo para conceito passa a depender desse plano.
"""

from __future__ import annotations

import re

import pandas as pd


def detectar_plano(fatos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Atribui um plano de contas a cada companhia.

    A deteccao usa a assinatura estrutural do balanco, nao o setor cadastral:
    o setor pode estar desatualizado ou ausente, mas a estrutura das contas
    que a propria companhia entregou nao mente.
    """
    planos_cfg = cfg["planos"]
    resultado = pd.Series("desconhecido", index=fatos["cnpj"].unique(), name="plano")

    for nome, spec in planos_cfg.items():
        d = spec["deteccao"]
        mascara = (
            (fatos["demonstracao"] == d["demonstracao"])
            & (fatos["cd_conta"] == d["codigo"])
        )
        if d.get("regex"):
            mascara &= fatos["ds_conta"].fillna("").str.contains(
                d["regex"], case=False, regex=True
            )
        candidatos = fatos.loc[mascara, "cnpj"].unique()
        ainda_sem_plano = resultado.index.isin(candidatos) & (resultado == "desconhecido")
        resultado.loc[ainda_sem_plano] = nome

    familias = {n: s.get("familia", "nao_financeira") for n, s in planos_cfg.items()}
    rotulos = {n: s.get("rotulo", n) for n, s in planos_cfg.items()}

    out = resultado.rename_axis("cnpj").reset_index()
    out["familia_plano"] = out["plano"].map(familias).fillna("desconhecida")
    out["plano_rotulo"] = out["plano"].map(rotulos).fillna("Plano nao identificado")
    return out


def resolver_conceitos(fatos: pd.DataFrame, planos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Converte o painel longo de contas num painel longo de conceitos.

    Usa apenas contas padronizadas (conta_fixa = 'S'), que sao as unicas
    comparaveis entre companhias. Contas livres criadas pela empresa ficam de
    fora deliberadamente.
    """
    base = fatos.merge(planos[["cnpj", "plano"]], on="cnpj", how="left")
    base = base[base["conta_fixa"].fillna("S").str.upper() == "S"]

    linhas: list[pd.DataFrame] = []
    for conceito, spec in cfg["conceitos"].items():
        for plano in spec:
            if plano == "rotulo":
                continue
            regra = spec[plano] or {}
            codigos = regra.get("codigos", [])
            mascara = (base["plano"] == plano) & (base["cd_conta"].isin(codigos))
            if regra.get("regex"):
                mascara &= base["ds_conta"].fillna("").str.contains(
                    regra["regex"], case=False, regex=True
                )
            sel = base.loc[mascara, ["cnpj", "ano", "cd_conta", "valor"]].copy()
            if sel.empty:
                continue
            sel["conceito"] = conceito
            linhas.append(sel)

    if not linhas:
        return pd.DataFrame(columns=["cnpj", "ano", "conceito", "valor"])

    longo = pd.concat(linhas, ignore_index=True)
    # Se mais de um codigo casou (layouts alternativos entre anos), fica o de
    # maior magnitude, que corresponde a conta de nivel mais alto.
    longo = (
        longo.assign(_abs=longo["valor"].abs())
        .sort_values("_abs", ascending=False)
        .drop_duplicates(["cnpj", "ano", "conceito"], keep="first")
        .drop(columns=["_abs", "cd_conta"])
    )
    return longo


def extrair_depreciacao(fatos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Estima depreciacao e amortizacao a partir da DFC.

    A DFP nao tem conta padronizada para D&A. A extracao percorre a subarvore
    das atividades operacionais procurando descricoes que mencionem depreciacao
    ou amortizacao. Contas-pai de outra ja capturada sao descartadas para nao
    contar o mesmo valor duas vezes.

    Toda metrica derivada daqui (EBITDA, Divida Liquida/EBITDA) e ESTIMADA e
    deve ser lida junto com a cobertura reportada na aba de qualidade.
    """
    regra = cfg.get("depreciacao_amortizacao", {})
    raiz = regra.get("raiz", "6.01")
    regex = regra.get("regex", "deprecia|amortiza")
    excluir = regra.get("excluir_regex")

    dfc = fatos[fatos["demonstracao"] == "DFC"].copy()
    if dfc.empty:
        return pd.DataFrame(columns=["cnpj", "ano", "conceito", "valor"])

    alvo = dfc[
        dfc["cd_conta"].astype(str).str.startswith(raiz)
        & dfc["ds_conta"].fillna("").str.contains(regex, case=False, regex=True)
    ].copy()
    if excluir:
        alvo = alvo[
            ~alvo["ds_conta"].fillna("").str.contains(excluir, case=False, regex=True)
        ]
    if alvo.empty:
        return pd.DataFrame(columns=["cnpj", "ano", "conceito", "valor"])

    def _sem_pais(grupo: pd.DataFrame) -> pd.DataFrame:
        codigos = sorted(grupo["cd_conta"].astype(str).unique())
        pais = {
            c for c in codigos
            if any(o != c and o.startswith(c + ".") for o in codigos)
        }
        return grupo[~grupo["cd_conta"].astype(str).isin(pais)]

    alvo = (
        alvo.groupby(["cnpj", "ano"], group_keys=False)[alvo.columns.tolist()]
        .apply(_sem_pais)
    )
    agregado = (
        alvo.groupby(["cnpj", "ano"], as_index=False)["valor"].sum()
        .assign(conceito="depreciacao_amortizacao")
    )
    # Na DFC a D&A entra como ajuste positivo ao lucro; o sinal negativo
    # ocasional vem de apresentacao invertida e e corrigido aqui.
    agregado["valor"] = agregado["valor"].abs()
    return agregado[["cnpj", "ano", "conceito", "valor"]]


def montar_painel(
    fatos: pd.DataFrame, planos: pd.DataFrame, cadastro: pd.DataFrame, cfg: dict
) -> pd.DataFrame:
    """Painel largo final: uma linha por (companhia, ano), conceitos em colunas."""
    conceitos = resolver_conceitos(fatos, planos, cfg)
    dep = extrair_depreciacao(fatos, cfg)
    if not dep.empty:
        conceitos = pd.concat([conceitos, dep], ignore_index=True)

    if conceitos.empty:
        return pd.DataFrame()

    painel = conceitos.pivot_table(
        index=["cnpj", "ano"], columns="conceito", values="valor", aggfunc="first"
    ).reset_index()
    painel.columns.name = None

    identificacao = (
        fatos.sort_values("ano", ascending=False)
        .drop_duplicates("cnpj")[["cnpj", "cd_cvm", "denominacao"]]
    )
    painel = painel.merge(identificacao, on="cnpj", how="left")
    painel = painel.merge(planos, on="cnpj", how="left")

    if cadastro is not None and not cadastro.empty:
        painel = painel.merge(cadastro, on="cnpj", how="left")
    for col in ["setor", "familia_cadastral", "nome_empresarial",
                "controle_acionario", "situacao_emissor"]:
        if col not in painel.columns:
            painel[col] = pd.NA

    painel["setor"] = painel["setor"].fillna("Sem classificação cadastral")
    painel["empresa"] = painel["nome_empresarial"].fillna(painel["denominacao"])

    # Familia final: o plano de contas manda; o cadastro so pode promover uma
    # companhia de plano padrao para o relatorio financeiro (caso de
    # securitizadoras e arrendamento mercantil).
    painel["familia"] = painel["familia_plano"]
    promover = (painel["familia_plano"] == "nao_financeira") & (
        painel["familia_cadastral"] == "financeira"
    )
    painel.loc[promover, "familia"] = "financeira"

    return painel.sort_values(["empresa", "ano"]).reset_index(drop=True)
