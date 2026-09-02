"""Traducao do plano de contas da CVM para conceitos canonicos.

Este e o modulo que impede o erro mais caro do dataset: tratar CD_CONTA como
se tivesse significado unico. A conta 2.03 e patrimonio liquido numa industria
e provisoes num banco. Aqui cada companhia recebe um plano de contas detectado,
e a traducao de codigo para conceito passa a depender desse plano.
"""

from __future__ import annotations

import re

import pandas as pd

# Chave de uma observacao contabil. Uma companhia tem uma linha por ano e por
# periodo: o exercicio fechado da DFP e cada recorte trimestral do ITR.
CHAVE_OBS = ["cnpj", "ano", "periodo"]


PREFERENCIA_ORIGEM = {"DFP": 0, "ITR": 1}


def detectar_plano(fatos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Atribui um plano de contas a cada par (companhia, documento).

    A deteccao usa a assinatura estrutural do balanco, nao o setor cadastral:
    o setor pode estar desatualizado ou ausente, mas a estrutura das contas
    que a propria companhia entregou nao mente.

    O plano e detectado por documento, e nao por companhia, porque a DFP e o ITR
    da mesma companhia nem sempre usam o mesmo layout: ha banco que reporta o
    patrimonio liquido em 2.07 no ITR e em 2.08 na DFP. Resolver cada linha com
    o plano do documento de onde ela veio evita que carregar o ITR apague
    conceitos do recorte anual.
    """
    planos_cfg = cfg["planos"]
    if "origem" not in fatos.columns:
        fatos = fatos.assign(origem="DFP")

    chaves = fatos[["cnpj", "origem"]].drop_duplicates()
    indice = pd.MultiIndex.from_frame(chaves)
    resultado = pd.Series("desconhecido", index=indice, name="plano")

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
        candidatos = pd.MultiIndex.from_frame(
            fatos.loc[mascara, ["cnpj", "origem"]].drop_duplicates()
        )
        ainda_sem_plano = resultado.index.isin(candidatos) & (resultado == "desconhecido")
        resultado.loc[ainda_sem_plano] = nome

    familias = {n: s.get("familia", "nao_financeira") for n, s in planos_cfg.items()}
    rotulos = {n: s.get("rotulo", n) for n, s in planos_cfg.items()}

    out = resultado.reset_index()
    out["familia_plano"] = out["plano"].map(familias).fillna("desconhecida")
    out["plano_rotulo"] = out["plano"].map(rotulos).fillna("Plano nao identificado")
    return out


def plano_por_empresa(planos: pd.DataFrame) -> pd.DataFrame:
    """Um plano por companhia, para rotular o painel e a aba de qualidade.

    A resolucao de conceitos usa o plano do documento; a identidade que aparece
    na tela e uma so, e vem da DFP quando ela existe.
    """
    if "origem" not in planos.columns:
        return planos
    ordenado = planos.assign(
        _pref=planos["origem"].map(PREFERENCIA_ORIGEM).fillna(99)
    ).sort_values("_pref")
    return (
        ordenado.drop_duplicates("cnpj")
        .drop(columns=["_pref", "origem"])
        .reset_index(drop=True)
    )


def resolver_conceitos(fatos: pd.DataFrame, planos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Converte o painel longo de contas num painel longo de conceitos.

    Usa apenas contas padronizadas (conta_fixa = 'S'), que sao as unicas
    comparaveis entre companhias. Contas livres criadas pela empresa ficam de
    fora deliberadamente.
    """
    chave_plano = ["cnpj", "origem"] if "origem" in planos.columns else ["cnpj"]
    base = fatos.merge(planos[chave_plano + ["plano"]], on=chave_plano, how="left")
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
            sel = base.loc[
                mascara, ["cnpj", "ano", "periodo", "cd_conta", "valor"]
            ].copy()
            if sel.empty:
                continue
            sel["conceito"] = conceito
            linhas.append(sel)

    if not linhas:
        return pd.DataFrame(columns=CHAVE_OBS + ["conceito", "valor"])

    longo = pd.concat(linhas, ignore_index=True)
    # Se mais de um codigo casou (layouts alternativos entre anos), fica o de
    # maior magnitude, que corresponde a conta de nivel mais alto.
    longo = (
        longo.assign(_abs=longo["valor"].abs())
        .sort_values("_abs", ascending=False)
        .drop_duplicates(CHAVE_OBS + ["conceito"], keep="first")
        .drop(columns=["_abs", "cd_conta"])
    )
    return longo


def extrair_heuristicos(fatos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Extrai conceitos que a DFP nao padroniza, por descricao da conta.

    Alguns conceitos economicamente relevantes nao tem conta fixa na DFP:
    depreciacao e amortizacao nao existem como linha padronizada, e dividendos
    pagos aparecem como conta livre dentro das atividades de financiamento, em
    dezenas de grafias diferentes. Para esses, a unica saida e casar a descricao.

    Cada conceito e declarado em config/conceitos.yaml sob `conceitos_heuristicos`,
    com a subarvore onde procurar, o regex de inclusao e, opcionalmente, um regex
    de exclusao. Nenhum conceito e conhecido por este modulo.

    Contas-pai de outra ja capturada sao descartadas para nao contar o mesmo
    valor duas vezes. Toda metrica derivada daqui e ESTIMADA, e a cobertura real
    aparece na aba "Qualidade dos dados".
    """
    regras = cfg.get("conceitos_heuristicos", {})
    vazio = pd.DataFrame(columns=CHAVE_OBS + ["conceito", "valor"])
    if not regras:
        return vazio

    dfc = fatos[fatos["demonstracao"] == "DFC"].copy()
    if dfc.empty:
        return vazio

    def _sem_pais(grupo: pd.DataFrame) -> pd.DataFrame:
        codigos = sorted(grupo["cd_conta"].astype(str).unique())
        pais = {
            c for c in codigos
            if any(o != c and o.startswith(c + ".") for o in codigos)
        }
        return grupo[~grupo["cd_conta"].astype(str).isin(pais)]

    blocos: list[pd.DataFrame] = []
    for conceito, regra in regras.items():
        raiz = str(regra.get("raiz", ""))
        regex = regra.get("regex")
        if not regex:
            continue

        alvo = dfc[
            dfc["cd_conta"].astype(str).str.startswith(raiz)
            & dfc["ds_conta"].fillna("").str.contains(regex, case=False, regex=True)
        ].copy()
        if regra.get("excluir_regex"):
            alvo = alvo[
                ~alvo["ds_conta"].fillna("").str.contains(
                    regra["excluir_regex"], case=False, regex=True
                )
            ]
        if alvo.empty:
            continue

        alvo = (
            alvo.groupby(CHAVE_OBS, group_keys=False)[alvo.columns.tolist()]
            .apply(_sem_pais)
        )
        agregado = (
            alvo.groupby(CHAVE_OBS, as_index=False)["valor"].sum()
            .assign(conceito=conceito)
        )
        # D&A entra como ajuste positivo ao lucro e dividendos como saida
        # negativa de caixa; em ambos o sinal e convencao de apresentacao, nao
        # informacao. `valor_absoluto` normaliza o conceito para positivo.
        if regra.get("valor_absoluto", True):
            agregado["valor"] = agregado["valor"].abs()
        blocos.append(agregado[CHAVE_OBS + ["conceito", "valor"]])

    return pd.concat(blocos, ignore_index=True) if blocos else vazio


def montar_painel(
    fatos: pd.DataFrame, planos: pd.DataFrame, cadastro: pd.DataFrame, cfg: dict
) -> pd.DataFrame:
    """Painel largo final: uma linha por observacao, conceitos em colunas.

    A observacao e a tripla (companhia, ano, periodo). `dt_fim` viaja junto
    porque o motor de indicadores precisa da data de encerramento para achar o
    saldo de abertura do periodo.
    """
    conceitos = resolver_conceitos(fatos, planos, cfg)
    heuristicos = extrair_heuristicos(fatos, cfg)
    if not heuristicos.empty:
        conceitos = pd.concat([conceitos, heuristicos], ignore_index=True)

    if conceitos.empty:
        return pd.DataFrame()

    painel = conceitos.pivot_table(
        index=CHAVE_OBS, columns="conceito", values="valor", aggfunc="first"
    ).reset_index()
    painel.columns.name = None

    # Data de encerramento da observacao. Uma companhia que muda o exercicio
    # social pode ter duas datas no mesmo (ano, periodo); fica a mais recente.
    datas = (
        fatos.groupby(CHAVE_OBS, as_index=False)["dt_fim"].max()
        if "dt_fim" in fatos.columns
        else pd.DataFrame(columns=CHAVE_OBS + ["dt_fim"])
    )
    painel = painel.merge(datas, on=CHAVE_OBS, how="left")

    identificacao = (
        fatos.sort_values("ano", ascending=False)
        .drop_duplicates("cnpj")[["cnpj", "cd_cvm", "denominacao"]]
    )
    painel = painel.merge(identificacao, on="cnpj", how="left")
    painel = painel.merge(plano_por_empresa(planos), on="cnpj", how="left")

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

    return painel.sort_values(["empresa", "ano", "periodo"]).reset_index(drop=True)
