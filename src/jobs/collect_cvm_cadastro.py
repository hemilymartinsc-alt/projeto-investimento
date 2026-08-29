from __future__ import annotations

import sys
from datetime import datetime, timezone

from src.collectors.cvm_cadastro import (
    PROCESS_NAME,
    SOURCE_CODE,
    download_cvm_cadastro,
    transform_cvm_cadastro,
    upsert_cvm_cadastro,
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
    registros_lidos: int | None = None,
    registros_gravados: int | None = None,
    mensagem: str | None = None,
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
                mensagem,
                log_id,
            ),
        )
    conn.commit()


def main() -> int:
    conn = connect()
    log_id = create_log(conn)
    lidos = 0

    try:
        df, source_updated_at = download_cvm_cadastro()
        lidos = len(df)

        rows = transform_cvm_cadastro(df, source_updated_at)
        gravados = upsert_cvm_cadastro(conn, rows)
        conn.commit()

        finish_log(
            conn,
            log_id,
            "SUCESSO",
            registros_lidos=lidos,
            registros_gravados=gravados,
            mensagem=(
                "Cadastro oficial de companhias abertas atualizado. "
                f"Execução UTC: {datetime.now(timezone.utc).isoformat()}"
            ),
        )

        print(
            f"CVM cadastro: sucesso | lidos={lidos} | gravados={gravados}"
        )
        return 0

    except Exception as exc:
        conn.rollback()
        try:
            finish_log(
                conn,
                log_id,
                "ERRO",
                registros_lidos=lidos or None,
                registros_gravados=0,
                mensagem=str(exc)[:1500],
            )
        finally:
            print(f"CVM cadastro: erro | {exc}", file=sys.stderr)
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
