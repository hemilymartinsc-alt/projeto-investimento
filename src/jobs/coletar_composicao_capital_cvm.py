from __future__ import annotations

import argparse
import sys
from datetime import date

from src.collectors.composicao_capital_cvm import (
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
        default=date.today().year,
        help=(
            "Ano da composição do capital. "
            "Padrão: ano atual."
        ),
    )

    parser.add_argument(
        "--documento",
        type=str,
        choices=(
            "DFP",
            "ITR",
            "AMBOS",
        ),
        default="AMBOS",
        help=(
            "Documento CVM a processar: "
            "DFP, ITR ou AMBOS."
        ),
    )

    return parser.parse_args()


def criar_log(
    conn,
) -> int:

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into
                investimento.coletas_log (
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

    return int(
        log_id
    )


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

               set finalizado_em =
                       now(),

                   status =
                       %s,

                   registros_lidos =
                       %s,

                   registros_gravados =
                       %s,

                   mensagem =
                       %s

             where id =
                       %s
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

    documentos = (
        (
            "DFP",
            "ITR",
        )
        if args.documento
           == "AMBOS"
        else (
            args.documento,
        )
    )

    conn = connect()

    log_id = None

    try:
        garantir_fontes(
            conn
        )

        conn.commit()

        log_id = criar_log(
            conn
        )

        resultados = []

        for documento in documentos:

            print(
                (
                    f"Processando "
                    f"{documento} "
                    f"{args.ano}..."
                )
            )

            resultado = coletar(
                conn,
                documento=documento,
                ano=args.ano,
            )

            resultados.append(
                resultado
            )

            print(
                (
                    f"{documento} "
                    f"{args.ano}: "
                    "lidos="
                    f"{resultado['registros_lidos']} | "
                    "gravados="
                    f"{resultado['registros_gravados']} | "
                    "empresas="
                    f"{resultado['empresas']} | "
                    "arquivo="
                    f"{resultado['arquivo'] or 'AUSENTE'}"
                )
            )

        conn.commit()

        lidos = sum(
            item[
                "registros_lidos"
            ]
            for item
            in resultados
        )

        gravados = sum(
            item[
                "registros_gravados"
            ]
            for item
            in resultados
        )

        detalhes = "; ".join(
            (
                f"{item['documento']}:"
                "gravados="
                f"{item['registros_gravados']},"
                "empresas="
                f"{item['empresas']},"
                "arquivo="
                f"{item['arquivo'] or 'AUSENTE'}"
            )

            for item
            in resultados
        )

        mensagem = (
            "Composição do capital CVM "
            f"ano={args.ano}; "
            f"documento={args.documento}; "
            f"lidos={lidos}; "
            f"gravados={gravados}; "
            f"{detalhes}."
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
            "Composição do capital "
            "CVM: SUCESSO"
        )

        print(
            (
                "Registros lidos: "
                f"{lidos}"
            )
        )

        print(
            (
                "Registros gravados: "
                f"{gravados}"
            )
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
                    mensagem=str(
                        exc
                    ),
                )

            except Exception:
                pass

        print(
            (
                "Composição do capital "
                "CVM: erro | "
                f"{exc}"
            ),
            file=sys.stderr,
        )

        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
