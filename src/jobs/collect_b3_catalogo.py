from __future__ import annotations

import sys

from src.collectors.b3_catalogo import (
    PROCESS_NAME,
    SOURCE_CODE,
    collect_and_upsert,
)
from src.database import connect


def create_log(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (
                fonte_codigo, processo, status
            )
            values (%s, %s, 'INICIADO')
            returning id
            """,
            (SOURCE_CODE, PROCESS_NAME),
        )
        log_id = cur.fetchone()[0]
    conn.commit()
    return log_id


def finish_log(
    conn,
    log_id: int,
    status: str,
    registros_lidos: int | None,
    registros_gravados: int | None,
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
                registros_lidos,
                registros_gravados,
                mensagem[:1500],
                log_id,
            ),
        )
    conn.commit()


def main() -> int:
    conn = connect()
    log_id = create_log(conn)

    try:
        empresas, tickers, erros_detalhe = collect_and_upsert(conn)
        conn.commit()

        status = "SUCESSO" if erros_detalhe == 0 else "PARCIAL"
        finish_log(
            conn,
            log_id,
            status,
            registros_lidos=empresas,
            registros_gravados=empresas + tickers,
            mensagem=(
                f"Empresas B3={empresas}; tickers extraídos={tickers}; "
                f"detalhes com falha={erros_detalhe}. "
                "Tickers ainda serão validados contra instrumentos/COTAHIST."
            ),
        )

        print(
            f"B3 catálogo: {status} | empresas={empresas} | "
            f"tickers={tickers} | falhas_detalhe={erros_detalhe}"
        )
        return 0

    except Exception as exc:
        conn.rollback()
        try:
            finish_log(
                conn,
                log_id,
                "ERRO",
                registros_lidos=None,
                registros_gravados=0,
                mensagem=str(exc),
            )
        finally:
            print(f"B3 catálogo: erro | {exc}", file=sys.stderr)
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
