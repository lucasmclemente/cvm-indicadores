"""Gera o snapshot publicavel a partir dos arquivos brutos da CVM.

Roda o pipeline completo sobre uma pasta local e grava o resultado em
./snapshot como Parquet. E esse snapshot - pequeno o bastante para caber no
repositorio - que o painel compartilhado carrega.

Uso tipico:

    python gerar_snapshot.py
    python gerar_snapshot.py --pasta "D:/bases/cvm" --rotulo "DFP 2015-2025"
    python gerar_snapshot.py --prioridade reapresentado

Depois de gerar:  git add snapshot && git commit && git push
"""

from __future__ import annotations

import argparse
import os
import sys

import core


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pasta", default="./dados",
                    help="Pasta com os CSVs/ZIPs da CVM (padrao: ./dados)")
    ap.add_argument("--saida", default=core.snapshot.DIR_PADRAO,
                    help="Pasta de destino do snapshot (padrao: ./snapshot)")
    ap.add_argument("--prioridade", default="ultimo",
                    choices=["ultimo", "reapresentado"],
                    help="Desempate entre exercicios repetidos (padrao: ultimo)")
    ap.add_argument("--rotulo", default="",
                    help="Texto livre exibido no painel, ex: 'DFP 2015-2025'")
    args = ap.parse_args()

    if not os.path.isdir(args.pasta):
        print(f"ERRO: pasta nao encontrada: {args.pasta}", file=sys.stderr)
        return 1

    brutos = [
        nome
        for _r, _d, nomes in os.walk(args.pasta)
        for nome in nomes
        if nome.lower().endswith((".csv", ".zip"))
    ]
    if not brutos:
        print(f"ERRO: nenhum .csv ou .zip em {args.pasta}", file=sys.stderr)
        print("Baixe as bases em https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp",
              file=sys.stderr)
        return 1

    print(f"Lendo {len(brutos)} arquivo(s) de {args.pasta} ...")
    res = core.executar_pasta(args.pasta, prioridade=args.prioridade)

    if res.painel.empty:
        print("ERRO: nenhum dado contabil reconhecido. Log de ingestao:",
              file=sys.stderr)
        print(res.log_ingestao.to_string(index=False), file=sys.stderr)
        return 1

    meta = core.snapshot.salvar(res, args.saida, rotulo=args.rotulo)

    peso = sum(
        os.path.getsize(os.path.join(args.saida, f))
        for f in os.listdir(args.saida)
    )
    print()
    print(f"Snapshot gravado em {args.saida}")
    print(f"  exercicios : {min(meta['anos'])}-{max(meta['anos'])}")
    print(f"  companhias : {meta['companhias']}")
    print(f"  setores    : {meta['setores']}")
    print(f"  tamanho    : {peso / 1_048_576:.1f} MB")
    print()
    print("Para publicar:  git add snapshot && git commit -m \"dados: atualiza snapshot\" && git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
