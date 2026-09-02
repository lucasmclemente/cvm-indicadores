"""Leitura dos arquivos brutos da CVM.

Responsabilidades:
  - identificar que demonstracao cada arquivo contem, pelo nome
  - lidar com encoding (cp1252 com fallback) e com variacao de schema entre anos
  - aceitar CSV solto ou o ZIP anual completo publicado pela CVM
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

import pandas as pd

# Nomes dos arquivos DFP da CVM -> rotulo interno.
# A ordem importa: DFC_MD e DFC_MI precisam ser testados antes de padroes curtos.
PADROES_ARQUIVO: list[tuple[str, str]] = [
    (r"dfp_cia_aberta_BPA_con", "BPA"),
    (r"dfp_cia_aberta_BPP_con", "BPP"),
    (r"dfp_cia_aberta_DRE_con", "DRE"),
    (r"dfp_cia_aberta_DFC_MD_con", "DFC"),
    (r"dfp_cia_aberta_DFC_MI_con", "DFC"),
    (r"dfp_cia_aberta_DVA_con", "DVA"),
    (r"itr_cia_aberta_BPA_con", "BPA"),
    (r"itr_cia_aberta_BPP_con", "BPP"),
    (r"itr_cia_aberta_DRE_con", "DRE"),
    (r"itr_cia_aberta_DFC_M[DI]_con", "DFC"),
    (r"base[_ ]?cadastral|fca_cia_aberta_geral", "CADASTRO"),
]

# Individual (ind) e explicitamente ignorado: misturar DF individual e
# consolidada da mesma companhia duplicaria a empresa no calculo setorial.
PADRAO_INDIVIDUAL = re.compile(r"_ind_", re.IGNORECASE)

# De que documento a linha veio. Importa porque os dois pacotes se sobrepoem:
# o ITR carrega algumas janelas anuais, e a mesma conta pode chegar com CD_CONTA
# diferente em cada um. Quem separa as duas fontes e core.normalizacao.
PADRAO_ITR = re.compile(r"itr_cia_aberta", re.IGNORECASE)

COLUNAS_DF = [
    "CNPJ_CIA", "DT_REFER", "VERSAO", "DENOM_CIA", "CD_CVM", "GRUPO_DFP",
    "MOEDA", "ESCALA_MOEDA", "ORDEM_EXERC", "DT_FIM_EXERC",
    "CD_CONTA", "DS_CONTA", "VL_CONTA", "ST_CONTA_FIXA",
]

ENCODINGS = ["cp1252", "latin-1", "utf-8-sig", "utf-8"]


@dataclass
class ResultadoIngestao:
    """Dados brutos empilhados por demonstracao, mais o log de leitura."""

    demonstracoes: dict[str, pd.DataFrame] = field(default_factory=dict)
    cadastro: pd.DataFrame | None = None
    log: list[dict] = field(default_factory=list)

    @property
    def resumo_log(self) -> pd.DataFrame:
        return pd.DataFrame(self.log)


def origem_arquivo(nome: str) -> str:
    """ITR (trimestral) ou DFP (anual), pelo nome do arquivo."""
    return "ITR" if PADRAO_ITR.search(nome) else "DFP"


def classificar_arquivo(nome: str) -> str | None:
    """Devolve o rotulo da demonstracao, ou None se o arquivo nao for reconhecido."""
    if PADRAO_INDIVIDUAL.search(nome):
        return None
    for padrao, rotulo in PADROES_ARQUIVO:
        if re.search(padrao, nome, flags=re.IGNORECASE):
            return rotulo
    return None


def _ler_csv(buffer: bytes) -> pd.DataFrame:
    """Le o CSV testando encodings ate um funcionar sem mojibake."""
    ultimo_erro: Exception | None = None
    for enc in ENCODINGS:
        try:
            df = pd.read_csv(
                io.BytesIO(buffer), sep=";", encoding=enc, dtype=str,
                keep_default_na=False, na_values=[""],
            )
            # Heuristica de mojibake: procura pares 'Ã' + minuscula acentuada,
            # assinatura de utf-8 lido como latin-1. Um 'Ã' isolado nao serve
            # de teste, porque "SAO" maiusculo e legitimo em nomes de empresa.
            amostra = "".join(map(str, df.columns)) + "".join(
                map(str, df.iloc[:50].astype(str).values.ravel()[:200])
            )
            if enc in ("latin-1", "cp1252") and any(
                par in amostra for par in ("Ã£", "Ã©", "Ã§", "Ãª", "Ã³", "Ãº", "Ã¡", "Ã­", "Ãµ")
            ):
                continue
            return df
        except Exception as exc:  # noqa: BLE001 - queremos tentar o proximo enc
            ultimo_erro = exc
    raise ValueError(f"Nao foi possivel ler o arquivo: {ultimo_erro}")


def _normalizar_colunas(
    df: pd.DataFrame, demonstracao: str, origem: str = "DFP"
) -> pd.DataFrame:
    """Garante o conjunto de colunas esperado mesmo em layouts antigos.

    O BPA/BPP nao trazem DT_INI_EXERC, e arquivos de anos anteriores ja
    omitiram ST_CONTA_FIXA. As faltantes sao criadas vazias em vez de
    quebrar a carga.
    """
    df = df.rename(columns=lambda c: c.strip().upper())
    for col in COLUNAS_DF:
        if col not in df.columns:
            df[col] = pd.NA
    if "DT_INI_EXERC" not in df.columns:
        df["DT_INI_EXERC"] = pd.NA
    df["DEMONSTRACAO"] = demonstracao
    df["ORIGEM"] = origem
    return df[COLUNAS_DF + ["DT_INI_EXERC", "DEMONSTRACAO", "ORIGEM"]]


def ingerir(arquivos: list[tuple[str, bytes]]) -> ResultadoIngestao:
    """Le uma lista de (nome, conteudo) e empilha por demonstracao.

    Arquivos .zip sao abertos e seus membros processados recursivamente,
    o que permite jogar o ZIP anual da CVM direto no sistema.
    """
    resultado = ResultadoIngestao()
    blocos: dict[str, list[pd.DataFrame]] = {}
    cadastros: list[pd.DataFrame] = []

    fila = list(arquivos)
    while fila:
        nome, conteudo = fila.pop(0)

        if nome.lower().endswith(".zip"):
            try:
                membros = []
                with zipfile.ZipFile(io.BytesIO(conteudo)) as zf:
                    for m in zf.namelist():
                        if not m.lower().endswith(".csv"):
                            continue
                        # Classifica pelo nome ANTES de descomprimir. O pacote
                        # anual da CVM traz DMPL, DRA, parecer e as versoes
                        # individuais, que somam centenas de MB e seriam
                        # descartadas logo adiante: le-los custaria memoria que
                        # o resto do pipeline precisa.
                        if classificar_arquivo(m) is None:
                            resultado.log.append(
                                {"arquivo": m, "tipo": "-", "linhas": 0,
                                 "status": "ignorado (nao reconhecido ou DF individual)"}
                            )
                            continue
                        membros.append((m, zf.read(m)))
                fila.extend(membros)
                resultado.log.append(
                    {"arquivo": nome, "tipo": "ZIP", "linhas": len(membros),
                     "status": f"{len(membros)} CSV extraidos"}
                )
            except Exception as exc:  # noqa: BLE001
                resultado.log.append(
                    {"arquivo": nome, "tipo": "ZIP", "linhas": 0,
                     "status": f"erro: {exc}"}
                )
            continue

        tipo = classificar_arquivo(nome)
        if tipo is None:
            resultado.log.append(
                {"arquivo": nome, "tipo": "-", "linhas": 0,
                 "status": "ignorado (nao reconhecido ou DF individual)"}
            )
            continue

        try:
            df = _ler_csv(conteudo)
        except Exception as exc:  # noqa: BLE001
            resultado.log.append(
                {"arquivo": nome, "tipo": tipo, "linhas": 0,
                 "status": f"erro de leitura: {exc}"}
            )
            continue

        if tipo == "CADASTRO":
            cadastros.append(df)
            resultado.log.append(
                {"arquivo": nome, "tipo": tipo, "linhas": len(df), "status": "ok"}
            )
            continue

        df = _normalizar_colunas(df, tipo, origem_arquivo(nome))
        blocos.setdefault(tipo, []).append(df)
        resultado.log.append(
            {"arquivo": nome, "tipo": tipo, "linhas": len(df), "status": "ok"}
        )

    resultado.demonstracoes = {
        tipo: pd.concat(partes, ignore_index=True) for tipo, partes in blocos.items()
    }
    if cadastros:
        resultado.cadastro = pd.concat(cadastros, ignore_index=True)
    return resultado


def ingerir_pasta(caminho: str) -> ResultadoIngestao:
    """Atalho para carga em lote a partir de um diretorio local."""
    import os

    arquivos: list[tuple[str, bytes]] = []
    for raiz, _dirs, nomes in os.walk(caminho):
        for nome in nomes:
            if nome.lower().endswith((".csv", ".zip")):
                caminho_completo = os.path.join(raiz, nome)
                with open(caminho_completo, "rb") as fh:
                    arquivos.append((nome, fh.read()))
    return ingerir(arquivos)
