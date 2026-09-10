from __future__ import annotations

import argparse
import sys

from src.collectors.proventos_b3 import (
    PROCESS_NAME,
    SOURCE_CODE,
    coletar,
    garantir_fonte,
)
from src.database import connect


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help=(
            "Ticker opcional para teste "
            "de uma única companhia, "
            "por exemplo PETR4. "
            "Se omitido, coleta "
            "o universo completo."
        ),
    )

    return parser.parse_args()


def criar_log(
    conn,
) -> int:
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
                SOURCE_CODE,
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

    ticker = (
        args.ticker
        .strip()
        .upper()
        if args.ticker
        else None
    )

    conn = connect()

    log_id = None

    try:
        garantir_fonte(
            conn
        )

        conn.commit()

        log_id = criar_log(
            conn
        )

        resultado = coletar(
            conn,
            ticker=ticker,
        )

        conn.commit()

        mensagem = (
            f"B3 proventos; "
            f"ticker={ticker or 'TODOS'}; "
            f"empresas_universo="
            f"{resultado['empresas_universo']}; "
            f"empresas_resolvidas="
            f"{resultado['empresas_resolvidas']}; "
            f"empresas_sem_catalogo="
            f"{resultado['empresas_sem_catalogo']}; "
            f"registros_api="
            f"{resultado['lidos']}; "
            f"gravados="
            f"{resultado['gravados']}; "
            f"ignorados_tipo="
            f"{resultado['ignorados_tipo']}; "
            f"ignorados_data="
            f"{resultado['ignorados_data']}; "
            f"ignorados_classe="
            f"{resultado['ignorados_classe']}; "
            f"duplicados_removidos="
            f"{resultado['duplicados_removidos']}."
        )

        finalizar_log(
            conn=conn,
            log_id=log_id,
            status="SUCESSO",
            lidos=resultado[
                "lidos"
            ],
            gravados=resultado[
                "gravados"
            ],
            mensagem=mensagem,
        )

        print(
            "Proventos B3: SUCESSO"
        )

        print(
            "Ticker: "
            f"{ticker or 'TODOS'}"
        )

        print(
            "Empresas resolvidas: "
            f"{resultado['empresas_resolvidas']}/"
            f"{resultado['empresas_universo']}"
        )

        print(
            "Registros lidos na API: "
            f"{resultado['lidos']}"
        )

        print(
            "Registros gravados: "
            f"{resultado['gravados']}"
        )

        print(
            "Sem catálogo: "
            f"{resultado['empresas_sem_catalogo']}"
        )

        print(
            "Ignorados por tipo: "
            f"{resultado['ignorados_tipo']}"
        )

        print(
            "Ignorados por data: "
            f"{resultado['ignorados_data']}"
        )

        print(
            "Ignorados por classe: "
            f"{resultado['ignorados_classe']}"
        )

        print(
            "Duplicados removidos: "
            f"{resultado['duplicados_removidos']}"
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
            "Proventos B3: erro | "
            f"{exc}",
            file=sys.stderr,
        )

        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
