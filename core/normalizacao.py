"""Normalizacao dos fatos contabeis.

Trata as tres armadilhas estruturais dos arquivos DFP:
  1. VERSAO: o mesmo documento pode aparecer em varias versoes no mesmo arquivo.
  2. ESCALA_MOEDA: parte das linhas vem em MIL e parte em UNIDADE.
  3. ORDEM_EXERC: cada arquivo anual traz dois exercicios (ULTIMO e PENULTIMO),
     de modo que empilhar N anos gera N-1 duplicidades de exercicio.
"""

from __future__ import annotations

import unicodedata

import pandas as pd

ESCALAS = {"MIL": 1_000.0, "UNIDADE": 1.0, "MILHAR": 1_000.0, "MILHAO": 1_000_000.0}


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c)
    ).upper().strip()


def normalizar_fatos(
    demonstracoes: dict[str, pd.DataFrame],
    prioridade: str = "ultimo",
) -> tuple[pd.DataFrame, pd.DataFrame]:
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
        (fatos, conflitos) onde `fatos` tem uma linha por
        (cnpj, ano, demonstracao, cd_conta) e `conflitos` lista os casos em que
        as duas fontes divergiram, para auditoria.
    """
    if not demonstracoes:
        return pd.DataFrame(), pd.DataFrame()

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

    # Na DRE e na DFC, descarta periodos que nao sejam anuais (protege contra
    # ITR carregado por engano ou exercicio social de transicao).
    fluxo = df["DEMONSTRACAO"].isin(["DRE", "DFC", "DVA"])
    duracao = (df["dt_fim"] - df["dt_ini"]).dt.days
    df = df[~fluxo | duracao.isna() | duracao.between(300, 400)]

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

    chave = ["CNPJ_CIA", "ano", "DEMONSTRACAO", "CD_CONTA", "DS_CONTA"]
    conflitos = _detectar_conflitos(df, chave)
    fatos = df.drop_duplicates(subset=chave, keep="first")

    fatos = fatos.rename(
        columns={
            "CNPJ_CIA": "cnpj", "DENOM_CIA": "denominacao", "CD_CVM": "cd_cvm",
            "CD_CONTA": "cd_conta", "DS_CONTA": "ds_conta",
            "ST_CONTA_FIXA": "conta_fixa", "DEMONSTRACAO": "demonstracao",
        }
    )
    colunas = [
        "cnpj", "cd_cvm", "denominacao", "ano", "demonstracao", "cd_conta",
        "ds_conta", "conta_fixa", "valor", "versao", "dt_refer",
    ]
    return fatos[colunas].reset_index(drop=True), conflitos


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
