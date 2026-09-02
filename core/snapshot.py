"""Persistencia do resultado do pipeline em Parquet.

Existe para separar o trabalho pesado do trabalho de exibicao: a ingestao dos
CSVs da CVM roda uma vez, na maquina de quem publica, e o painel compartilhado
carrega apenas o resultado - que e pequeno - sem tocar nos arquivos brutos.

Nao importa Streamlit: e parte do motor, nao da interface.
"""

from __future__ import annotations

import json
import os
from dataclasses import fields
from datetime import datetime

import pandas as pd

NOME_META = "meta.json"
DIR_PADRAO = os.path.join(os.path.dirname(os.path.dirname(__file__)), "snapshot")


def _caminho(diretorio: str, nome: str) -> str:
    return os.path.join(diretorio, f"{nome}.parquet")


def salvar(res, diretorio: str = DIR_PADRAO, rotulo: str = "") -> dict:
    """Grava cada tabela do Resultado como Parquet, mais um meta.json.

    Percorre os campos da dataclass em vez de uma lista fixa: se o Resultado
    ganhar uma tabela nova, ela entra no snapshot sem alterar este modulo.
    """
    os.makedirs(diretorio, exist_ok=True)

    tabelas: list[str] = []
    for campo in fields(res):
        valor = getattr(res, campo.name)
        if not isinstance(valor, pd.DataFrame):
            continue
        valor.to_parquet(_caminho(diretorio, campo.name), index=False)
        tabelas.append(campo.name)

    meta = {
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rotulo": rotulo,
        "tabelas": tabelas,
        "anos": res.anos,
        "periodos": res.periodos,
        "companhias": (
            int(res.painel["cnpj"].nunique()) if not res.painel.empty else 0
        ),
        "setores": (
            int(res.painel["setor"].nunique()) if not res.painel.empty else 0
        ),
    }
    with open(os.path.join(diretorio, NOME_META), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    return meta


def existe(diretorio: str = DIR_PADRAO) -> bool:
    """Ha um snapshot publicavel neste diretorio?"""
    return os.path.isfile(os.path.join(diretorio, NOME_META))


def ler_meta(diretorio: str = DIR_PADRAO) -> dict:
    with open(os.path.join(diretorio, NOME_META), encoding="utf-8") as fh:
        return json.load(fh)


def assinatura(diretorio: str = DIR_PADRAO) -> float:
    """Marca de tempo do meta.json, usada como chave de cache pela interface."""
    try:
        return os.path.getmtime(os.path.join(diretorio, NOME_META))
    except OSError:
        return 0.0


def carregar(diretorio: str = DIR_PADRAO):
    """Reconstroi um Resultado a partir dos Parquet gravados por salvar().

    Snapshot gravado antes de existirem periodos nao tem a coluna: naquela
    epoca todo o painel era exercicio fechado, entao ela e reposta com "ano".
    Isso mantem um painel publicado antigo funcionando sem regerar nada.
    """
    from . import Resultado  # tardio: Resultado vive no __init__ do pacote

    dados = {}
    for campo in fields(Resultado):
        caminho = _caminho(diretorio, campo.name)
        dados[campo.name] = (
            pd.read_parquet(caminho) if os.path.isfile(caminho) else pd.DataFrame()
        )
    for nome in ["painel", "indicadores", "setorial_nao_financeira",
                 "setorial_financeira", "diagnostico"]:
        tabela = dados.get(nome)
        if tabela is not None and not tabela.empty and "periodo" not in tabela.columns:
            tabela["periodo"] = "ano"
    return Resultado(**dados)
