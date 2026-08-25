"""Pipeline de indicadores setoriais a partir das DFPs da CVM."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd
import yaml

from . import agregacao, conceitos, indicadores, ingestao, normalizacao, snapshot

DIR_CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")


def carregar_config(diretorio: str = DIR_CONFIG) -> dict:
    """Le os tres arquivos de configuracao do sistema."""
    cfg: dict = {}
    for nome in ["conceitos", "indicadores", "setores"]:
        caminho = os.path.join(diretorio, f"{nome}.yaml")
        with open(caminho, encoding="utf-8") as fh:
            cfg[nome] = yaml.safe_load(fh)
    return cfg


@dataclass
class Resultado:
    """Saida completa do pipeline, com os artefatos de auditoria."""

    painel: pd.DataFrame
    indicadores: pd.DataFrame
    setorial_nao_financeira: pd.DataFrame
    setorial_financeira: pd.DataFrame
    planos: pd.DataFrame
    cadastro: pd.DataFrame
    conflitos: pd.DataFrame
    diagnostico: pd.DataFrame
    log_ingestao: pd.DataFrame

    @property
    def anos(self) -> list[int]:
        if self.painel.empty:
            return []
        return sorted(self.painel["ano"].unique().tolist())


def executar(
    arquivos: list[tuple[str, bytes]],
    cfg: dict | None = None,
    prioridade: str = "ultimo",
) -> Resultado:
    """Roda o pipeline completo: ingestao -> normalizacao -> conceitos -> indicadores."""
    cfg = cfg or carregar_config()

    bruto = ingestao.ingerir(arquivos)
    fatos, conflitos = normalizacao.normalizar_fatos(bruto.demonstracoes, prioridade)
    cadastro = normalizacao.normalizar_cadastro(bruto.cadastro, cfg["setores"])

    if fatos.empty:
        vazio = pd.DataFrame()
        return Resultado(vazio, vazio, vazio, vazio, vazio, cadastro, conflitos,
                         vazio, bruto.resumo_log)

    planos = conceitos.detectar_plano(fatos, cfg["conceitos"])
    painel = conceitos.montar_painel(fatos, planos, cadastro, cfg["conceitos"])
    ind, diag = indicadores.calcular(painel, cfg["indicadores"])

    return Resultado(
        painel=painel,
        indicadores=ind,
        setorial_nao_financeira=agregacao.agregar_por_setor(
            ind, cfg["indicadores"], "nao_financeira"
        ),
        setorial_financeira=agregacao.agregar_por_setor(
            ind, cfg["indicadores"], "financeira"
        ),
        planos=planos,
        cadastro=cadastro,
        conflitos=conflitos,
        diagnostico=diag,
        log_ingestao=bruto.resumo_log,
    )


def executar_pasta(caminho: str, **kwargs) -> Resultado:
    """Conveniencia para rodar sobre um diretorio local de arquivos."""
    arquivos: list[tuple[str, bytes]] = []
    for raiz, _dirs, nomes in os.walk(caminho):
        for nome in nomes:
            if nome.lower().endswith((".csv", ".zip")):
                with open(os.path.join(raiz, nome), "rb") as fh:
                    arquivos.append((nome, fh.read()))
    return executar(arquivos, **kwargs)


__all__ = [
    "executar", "executar_pasta", "carregar_config", "Resultado",
    "ingestao", "normalizacao", "conceitos", "indicadores", "agregacao", "snapshot",
]
