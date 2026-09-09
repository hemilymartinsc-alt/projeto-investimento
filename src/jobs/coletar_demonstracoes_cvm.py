from __future__ import annotations

import argparse
import sys
from datetime import date

from src.collectors.demonstracoes_cvm import (
    PROCESS_NAME,
    SOURCE_LOG_CODE,
    coletar,
    garantir_fontes,
)
from src.database import connect


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--ano",
        type=int,
        default=None,
        help=(
            "Ano das demonstrações. "
            "Se omitido, usa o ano atual."
        ),
    )

    parser.add_argument(
        "--documento",
        type=str,
        default="AMBOS",
        choices=[
            "DFP",
            "ITR",
            "AMBOS",
        ],
        help=(
            "Documento a coletar: "
            "DFP, ITR ou AMBOS."
        ),
    )

    return parser.parse_args()


def criar_log(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (
                fonte_codigo,
                processo,
                status
            )
            values (
                %s,
                %s,
                'INICIADO'
            )
            returning id
            """,
            (
                SOURCE_LOG_CODE,
                PROCESS_NAME,
            ),
        )

        log_id = cur.fetchone()[0]

    conn.commit()

    return log_id


def finalizar_log(
    conn,
    log_id: int,
    status: str,
    lidos: int | None,
    gravados: int | None,
    mensagem: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            update investimento.coletas_log
               set finalizado_em = now(),
                   status = %s,
                   registros_lidos = %s,
                   registros_gravados = %s,
                   mensagem = %s
             where id = %s
            """,
            (
                status,
                lidos,
                gravados,
                mensagem[:1500],
                log_id,
            ),
        )

    conn.commit()


def main() -> int:
    args = parse_args()

    ano = (
        args.ano
        if args.ano
        else date.today().year
    )

    conn = connect()

    log_id = None

    try:
        garantir_fontes(
            conn,
        )

        conn.commit()

        log_id = criar_log(
            conn,
        )

        lidos, gravados = coletar(
            conn=conn,
            ano=ano,
            documento=args.documento,
        )

        conn.commit()

        mensagem = (
            f"CVM {args.documento} ano={ano}; "
            f"registros lidos={lidos}; "
            f"registros gravados={gravados}."
        )

        finalizar_log(
            conn=conn,
            log_id=log_id,
            status="SUCESSO",
            lidos=lidos,
            gravados=gravados,
            mensagem=mensagem,
        )

        print(
            "Demonstrações CVM: SUCESSO | "
            f"ano={ano} | "
            f"documento={args.documento} | "
            f"lidos={lidos} | "
            f"gravados={gravados}"
        )

        return 0

    except Exception as exc:
        conn.rollback()

        if log_id is not None:
            try:
                finalizar_log(
                    conn=conn,
                    log_id=log_id,
                    status="ERRO",
                    lidos=None,
                    gravados=0,
                    mensagem=str(exc),
                )
            except Exception:
                pass

        print(
            f"Demonstrações CVM: erro | {exc}",
            file=sys.stderr,
        )

        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
