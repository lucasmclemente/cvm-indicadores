"""Motor de calculo dos indicadores.

As formulas ficam em config/indicadores.yaml como pares numerador/denominador.
Manter os dois lados separados permite calcular, com uma unica definicao, tanto
a distribuicao das razoes individuais quanto a razao agregada do setor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ALIQUOTA_PADRAO = 0.34


def _preparar_namespace(painel: pd.DataFrame) -> dict:
    """Monta o espaco de nomes exposto as formulas.

    Inclui os conceitos como Series, a aliquota efetiva derivada da DRE e as
    funcoes auxiliares MED, ABS, MAX e MIN.
    """
    painel = painel.sort_values(["cnpj", "ano"])
    ns: dict[str, object] = {}

    reservado = {
        "cnpj", "ano", "empresa", "denominacao", "nome_empresarial", "setor",
        "setor_original", "plano", "plano_rotulo", "familia", "familia_plano",
        "familia_cadastral", "cd_cvm", "controle_acionario", "situacao_emissor",
    }
    for col in painel.columns:
        if col not in reservado:
            ns[col] = pd.to_numeric(painel[col], errors="coerce")

    # Aliquota efetiva de IR/CSLL, usada no ROIC. Limitada porque prejuizo
    # fiscal e incentivos produzem aliquotas negativas ou acima de 100%.
    if "ir_csll" in ns and "ebt" in ns:
        aliq = (-ns["ir_csll"]) / ns["ebt"].replace(0, np.nan)
        aliq = aliq.where(aliq.between(0, 0.5), ALIQUOTA_PADRAO)
        ns["aliquota_efetiva"] = aliq.fillna(ALIQUOTA_PADRAO)
    else:
        ns["aliquota_efetiva"] = pd.Series(ALIQUOTA_PADRAO, index=painel.index)

    grupo = painel.groupby("cnpj")
    ano = painel["ano"]
    ano_anterior = grupo["ano"].shift(1)
    tem_anterior = (ano - ano_anterior) == 1

    def MED(conceito) -> pd.Series:
        """Media entre saldo de abertura e de fechamento do exercicio.

        Aceita a propria Series (uso normal nas formulas) ou o nome do conceito.
        Quando o ano anterior nao esta no painel, devolve o saldo final. Isso
        mantem a serie utilizavel no primeiro ano carregado, ao custo de uma
        pequena inconsistencia que o usuario deve conhecer.
        """
        serie = ns.get(conceito) if isinstance(conceito, str) else conceito
        if serie is None or not isinstance(serie, pd.Series):
            return pd.Series(np.nan, index=painel.index)
        anterior = serie.groupby(painel["cnpj"]).shift(1)
        media = (serie + anterior) / 2
        return media.where(tem_anterior & anterior.notna(), serie)

    ns["MED"] = MED
    ns["ABS"] = lambda x: x.abs() if isinstance(x, pd.Series) else abs(x)
    ns["MAX"] = lambda a, b: np.maximum(a, b)
    ns["MIN"] = lambda a, b: np.minimum(a, b)
    return ns


def _avaliar(expressao: str, ns: dict, index: pd.Index) -> pd.Series:
    """Avalia a expressao no namespace restrito.

    O eval roda sem builtins. As formulas vem de um arquivo de configuracao sob
    controle de quem opera o sistema, nao de entrada de usuario final.
    """
    try:
        valor = eval(expressao, {"__builtins__": {}}, ns)  # noqa: S307
    except (KeyError, NameError, TypeError):
        return pd.Series(np.nan, index=index)
    if np.isscalar(valor):
        return pd.Series(float(valor), index=index)
    return pd.Series(valor, index=index)


def calcular(painel: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calcula todos os indicadores aplicaveis a cada companhia/ano.

    Returns:
        (empresa_indicadores, diagnostico) - o painel longo de indicadores e um
        resumo de quantas observacoes foram descartadas em cada etapa de
        saneamento.
    """
    if painel.empty:
        return pd.DataFrame(), pd.DataFrame()

    painel = painel.sort_values(["cnpj", "ano"]).reset_index(drop=True)
    ns = _preparar_namespace(painel)
    san = cfg.get("saneamento", {})
    den_min = float(san.get("denominador_minimo_brl", 0))
    limites = san.get("limites", {})

    resultados: list[pd.DataFrame] = []
    diagnostico: list[dict] = []

    for chave, spec in cfg["indicadores"].items():
        familias = set(spec.get("familias", ["nao_financeira"]))
        aplicavel = painel["familia"].isin(familias)

        num = _avaliar(spec["num"], ns, painel.index)
        den = _avaliar(spec.get("den", "1"), ns, painel.index)

        bruto = num.notna() & den.notna() & aplicavel
        n_bruto = int(bruto.sum())

        valido = bruto.copy()
        # Denominador residual gera razao explosiva sem significado economico.
        if spec.get("den", "1") != "1" and den_min > 0:
            valido &= den.abs() >= den_min
        if spec.get("den_positivo"):
            valido &= den > 0

        n_denominador = int(bruto.sum() - valido.sum())

        valor = pd.Series(np.nan, index=painel.index)
        valor[valido] = num[valido] / den[valido]

        formato = spec.get("formato", "razao")
        limite = limites.get(formato)
        n_outlier = 0
        if limite:
            fora = valor.notna() & ~valor.between(limite[0], limite[1])
            n_outlier = int(fora.sum())
            valor[fora] = np.nan

        bloco = painel.loc[
            valor.notna(), ["cnpj", "empresa", "ano", "setor", "familia", "plano"]
        ].copy()
        bloco["indicador"] = chave
        bloco["rotulo"] = spec.get("rotulo", chave)
        bloco["grupo"] = spec.get("grupo", "Outros")
        bloco["formato"] = formato
        bloco["valor"] = valor[valor.notna()].values
        bloco["numerador"] = num[valor.notna()].values
        bloco["denominador"] = den[valor.notna()].values
        bloco["agregacao_soma"] = bool(spec.get("agregacao_soma", False))
        resultados.append(bloco)

        diagnostico.append(
            {
                "indicador": chave,
                "rotulo": spec.get("rotulo", chave),
                "empresas_aplicaveis": int(painel.loc[aplicavel, "cnpj"].nunique()),
                "observacoes_com_dados": n_bruto,
                "descartes_denominador": n_denominador,
                "descartes_outlier": n_outlier,
                "observacoes_validas": int(valor.notna().sum()),
                "cobertura": (
                    round(valor.notna().sum() / n_bruto, 3) if n_bruto else 0.0
                ),
            }
        )

    if not resultados:
        return pd.DataFrame(), pd.DataFrame(diagnostico)

    return pd.concat(resultados, ignore_index=True), pd.DataFrame(diagnostico)
