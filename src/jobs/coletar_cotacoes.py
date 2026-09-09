from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

from src.collectors.cotacoes_b3 import (
    PROCESS_NAME,
    SOURCE_CODE,
    coletar_anual,
    coletar_diario,
)
from src.database import connect


def criar_log(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (
                fonte_codigo,
                processo,
                status
            )
            values (%s, %s, 'INICIADO')
            returning id
            """,
            (
                SOURCE_CODE,
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


def ultimo_dia_util() -> date:
    dia = date.today() - timedelta(days=1)

    while dia.weekday() >= 5:
        dia -= timedelta(days=1)

    return dia


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--ano",
        type=int,
        help="Importa todo o COTAHIST anual.",
    )

    parser.add_argument(
        "--data",
        type=str,
        help="Importa um pregão específico (AAAA-MM-DD).",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    conn = connect()
    log_id = criar_log(conn)

    try:
        if args.ano:
            lidos, gravados = coletar_anual(
                conn,
                args.ano,
            )
            referencia = f"ano={args.ano}"

        else:
            if args.data:
                data_ref = datetime.strptime(
                    args.data,
                    "%Y-%m-%d",
                ).date()
            else:
                data_ref = ultimo_dia_util()

            lidos, gravados = coletar_diario(
                conn,
                data_ref,
            )
            referencia = f"data={data_ref.isoformat()}"

        conn.commit()

        finalizar_log(
            conn,
            log_id,
            "SUCESSO",
            lidos,
            gravados,
            (
                f"B3 COTAHIST {referencia}; "
                f"registros lidos={lidos}; "
                f"registros do universo gravados={gravados}."
            ),
        )

        print(
            f"Cotações B3: SUCESSO | "
            f"{referencia} | "
            f"lidos={lidos} | "
            f"gravados={gravados}"
        )

        return 0

    except Exception as exc:
        conn.rollback()

        try:
            finalizar_log(
                conn,
                log_id,
                "ERRO",
                None,
                0,
                str(exc),
            )
        finally:
            print(
                f"Cotações B3: erro | {exc}",
                file=sys.stderr,
            )

        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
