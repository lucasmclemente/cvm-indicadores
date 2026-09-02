"""Motor de calculo dos indicadores.

As formulas ficam em config/indicadores.yaml como pares numerador/denominador.
Manter os dois lados separados permite calcular, com uma unica definicao, tanto
a distribuicao das razoes individuais quanto a razao agregada do setor.

Tudo aqui trabalha dentro de um periodo: exercicio fechado, semestre ou
trimestre nunca se misturam numa mesma observacao.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .normalizacao import PERIODOS

ALIQUOTA_PADRAO = 0.34

# Ordem canonica das observacoes. `calcular` ordena o painel uma unica vez e
# todo o resto assume essa ordem - inclusive a montagem dos blocos de saida,
# que casa vetores por posicao.
ORDEM_PAINEL = ["cnpj", "ano", "periodo"]


def _preparar_namespace(painel: pd.DataFrame) -> dict:
    """Monta o espaco de nomes exposto as formulas.

    Inclui os conceitos como Series, a aliquota efetiva derivada da DRE e as
    funcoes auxiliares MED, ABS, MAX e MIN. Espera o painel ja ordenado e com
    indice limpo, como `calcular` entrega.
    """
    ns: dict[str, object] = {}

    reservado = {
        "cnpj", "ano", "periodo", "dt_fim", "empresa", "denominacao",
        "nome_empresarial", "setor", "setor_original", "plano", "plano_rotulo",
        "familia", "familia_plano", "familia_cadastral", "cd_cvm",
        "controle_acionario", "situacao_emissor",
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

    # Data do saldo de abertura: o balanco encerrado no fim do periodo anterior.
    # Para o exercicio fechado e o balanco de um ano antes; para o 2o trimestre,
    # o de 31 de marco; para o 1o semestre, o de 31 de dezembro. Recuar meses a
    # partir do encerramento e mais seguro do que olhar a linha anterior do
    # painel: a companhia agora tem varias linhas por ano, e a vizinha pode ser
    # um recorte diferente do mesmo intervalo.
    dt_fim = pd.to_datetime(painel.get("dt_fim"), errors="coerce")
    if dt_fim is None or dt_fim.isna().all():
        dt_fim = pd.Series(pd.NaT, index=painel.index)
    meses = painel["periodo"].map(
        lambda p: PERIODOS.get(p, {}).get("meses", 12)
    ) if "periodo" in painel.columns else pd.Series(12, index=painel.index)

    # O casamento e por MES de encerramento, nao pelo dia exato. Companhia que
    # fecha em 28 de fevereiro reporta 28/02 tambem em ano bissexto, quando o
    # ultimo dia do mes e 29: exigir o dia certo perderia o saldo de abertura
    # justamente de quem tem exercicio social deslocado.
    mes_fechamento = dt_fim.dt.to_period("M")
    mes_abertura = pd.Series(pd.NaT, index=painel.index, dtype=mes_fechamento.dtype)
    for n in pd.unique(meses.dropna()):
        alvo = meses == n
        mes_abertura[alvo] = mes_fechamento[alvo] - int(n)

    chave_fechamento = pd.MultiIndex.from_arrays([painel["cnpj"], mes_fechamento])
    chave_abertura = pd.MultiIndex.from_arrays([painel["cnpj"], mes_abertura])

    def MED(conceito) -> pd.Series:
        """Media entre saldo de abertura e de fechamento do periodo.

        Aceita a propria Series (uso normal nas formulas) ou o nome do conceito.
        Quando o balanco de abertura nao esta no painel, devolve o saldo final.
        Isso mantem a serie utilizavel no primeiro periodo carregado, ao custo
        de uma pequena inconsistencia que o usuario deve conhecer.
        """
        serie = ns.get(conceito) if isinstance(conceito, str) else conceito
        if serie is None or not isinstance(serie, pd.Series):
            return pd.Series(np.nan, index=painel.index)
        # O mesmo balanco de 30 de junho serve ao 1o semestre e ao 2o
        # trimestre; como saldo os dois valem o mesmo, entao a repeticao cai.
        # A ordem do painel poe "ano" antes dos recortes parciais, entao o
        # saldo do exercicio fechado e o que fica quando ha empate.
        saldos = pd.Series(serie.to_numpy(), index=chave_fechamento)
        saldos = saldos[~saldos.index.duplicated(keep="first")]
        anterior = pd.Series(
            saldos.reindex(chave_abertura).to_numpy(), index=painel.index
        )
        media = (serie + anterior) / 2
        return media.where(anterior.notna(), serie)

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


def _conceitos_ausentes(requeridos, ns: dict, index: pd.Index) -> pd.Series:
    """Marca as observacoes em que falta algum conceito declarado em 'requer'.

    A lista de nomes vem do YAML: este modulo nao conhece nenhum conceito pelo
    nome. Um conceito que nao existe no painel - nenhuma companhia reportou a
    conta - invalida todas as observacoes do indicador; um conceito presente
    invalida apenas as linhas em que ele esta nulo.
    """
    ausente = pd.Series(False, index=index)
    for conceito in requeridos:
        serie = ns.get(conceito)
        if not isinstance(serie, pd.Series):
            return pd.Series(True, index=index)
        ausente |= serie.isna()
    return ausente


def calcular(painel: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calcula todos os indicadores aplicaveis a cada companhia/ano/periodo.

    O diagnostico e um funil, aberto por periodo: de todas as observacoes
    aplicaveis a familia do indicador, separa as que ficaram de fora por falta
    de um conceito exigido ('requer' no YAML), por denominador residual e por
    limite de plausibilidade. Abrir por periodo importa porque a cobertura muda
    muito entre eles - a DFC do ITR, por exemplo, so vem acumulada no ano.

    Returns:
        (empresa_indicadores, diagnostico) - o painel longo de indicadores e um
        resumo de quantas observacoes foram descartadas em cada etapa de
        saneamento.
    """
    if painel.empty:
        return pd.DataFrame(), pd.DataFrame()

    painel = painel.copy()
    if "periodo" not in painel.columns:
        # Snapshot gravado antes de existirem periodos: tudo era exercicio
        # fechado.
        painel["periodo"] = "ano"
    if "dt_fim" not in painel.columns:
        painel["dt_fim"] = pd.NaT
    painel["dt_fim"] = pd.to_datetime(painel["dt_fim"], errors="coerce")

    # Painel antigo nao guardava a data de encerramento. Como naquela epoca tudo
    # era exercicio fechado, 31 de dezembro reproduz exatamente a regra que
    # valia: o saldo de abertura e o balanco do ano anterior.
    sem_data = painel["dt_fim"].isna()
    if bool(sem_data.any()):
        painel.loc[sem_data, "dt_fim"] = pd.to_datetime(
            painel.loc[sem_data, "ano"].astype(int).astype(str) + "-12-31"
        )

    painel = painel.sort_values(ORDEM_PAINEL).reset_index(drop=True)
    ns = _preparar_namespace(painel)
    san = cfg.get("saneamento", {})
    den_min = float(san.get("denominador_minimo_brl", 0))
    limites = san.get("limites", {})

    resultados: list[pd.DataFrame] = []
    diagnostico: list[dict] = []

    for chave, spec in cfg["indicadores"].items():
        familias = set(spec.get("familias", ["nao_financeira"]))
        aplicavel = painel["familia"].isin(familias)

        # Conceitos exigidos pelo indicador. Sem eles a observacao nao e
        # calculavel - o descarte e contabilizado a parte para nao se
        # confundir com companhia que simplesmente nao tem dado nenhum.
        sem_conceito = aplicavel & _conceitos_ausentes(
            spec.get("requer") or [], ns, painel.index
        )

        num = _avaliar(spec["num"], ns, painel.index)
        den = _avaliar(spec.get("den", "1"), ns, painel.index)

        bruto = num.notna() & den.notna() & aplicavel & ~sem_conceito

        valido = bruto.copy()
        # Denominador residual gera razao explosiva sem significado economico.
        if spec.get("den", "1") != "1" and den_min > 0:
            valido &= den.abs() >= den_min
        if spec.get("den_positivo"):
            valido &= den > 0

        valor = pd.Series(np.nan, index=painel.index)
        valor[valido] = num[valido] / den[valido]

        formato = spec.get("formato", "razao")
        limite = limites.get(formato)
        fora = pd.Series(False, index=painel.index)
        if limite:
            fora = valor.notna() & ~valor.between(limite[0], limite[1])
            valor[fora] = np.nan

        bloco = painel.loc[
            valor.notna(),
            ["cnpj", "empresa", "ano", "periodo", "setor", "familia", "plano"],
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

        contagem = pd.DataFrame(
            {
                "periodo": painel["periodo"],
                "cnpj": painel["cnpj"],
                "aplicavel": aplicavel,
                "sem_conceito": sem_conceito,
                "com_dados": bruto,
                "descarte_denominador": bruto & ~valido,
                "descarte_limite": fora,
                "valida": valor.notna(),
            }
        )
        for periodo, g in contagem[contagem["aplicavel"]].groupby("periodo"):
            n_bruto = int(g["com_dados"].sum())
            n_validas = int(g["valida"].sum())
            diagnostico.append(
                {
                    "indicador": chave,
                    "rotulo": spec.get("rotulo", chave),
                    "periodo": periodo,
                    "empresas_aplicaveis": int(g["cnpj"].nunique()),
                    "observacoes_aplicaveis": int(len(g)),
                    "descartes_conceito_ausente": int(g["sem_conceito"].sum()),
                    "observacoes_com_dados": n_bruto,
                    "descartes_denominador": int(g["descarte_denominador"].sum()),
                    "descartes_outlier": int(g["descarte_limite"].sum()),
                    "observacoes_validas": n_validas,
                    "cobertura": round(n_validas / n_bruto, 3) if n_bruto else 0.0,
                }
            )

    if not resultados:
        return pd.DataFrame(), pd.DataFrame(diagnostico)

    return pd.concat(resultados, ignore_index=True), pd.DataFrame(diagnostico)
