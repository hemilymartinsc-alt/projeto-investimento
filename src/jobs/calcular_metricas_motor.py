from __future__ import annotations

import sys
from pathlib import Path

from src.database import connect


PROCESS_NAME = "calcular_metricas_motor"
SOURCE_CODE = "MOTOR_METRICAS_V1"

FONTE_CVM = "CALC_CVM_TTM_V1"
FONTE_PROVENTOS = "CALC_B3_PROVENTOS_V1"

SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "calcular_metricas_motor.sql"
)

DELETE_SQL = """
delete from investimento.motor_metricas_observadas
where fonte in (
    'CALC_CVM_TTM_V1',
    'CALC_B3_PROVENTOS_V1'
)
and indicador_codigo in (
    'CAGR_RECEITA_3A',
    'CAGR_LUCRO_3A',
    'ESTABILIDADE_LUCRO',
    'CAGR_PROVENTOS_3A',
    'REGULARIDADE_PROVENTOS'
)
"""


def carregar_sql() -> str:
    if not SQL_PATH.exists():
        raise FileNotFoundError(
            f"Arquivo SQL não encontrado: {SQL_PATH}"
        )

    return SQL_PATH.read_text(
        encoding="utf-8"
    )


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


def contar_universo(
    conn,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                count(*)
            from
                investimento.vw_motor_universo_v1
            where
                universo_investivel_v1
                and classe = 'ACAO'
            """
        )

        total = cur.fetchone()[0]

    return int(
        total or 0
    )


def limpar_metricas(
    conn,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            DELETE_SQL
        )

        apagados = cur.rowcount

    return int(
        apagados or 0
    )


def calcular_metricas(
    conn,
) -> dict[str, int]:
    sql = carregar_sql()

    with conn.cursor() as cur:
        cur.execute(
            sql
        )

        rows = cur.fetchall()

    return {
        str(
            codigo
        ): int(
            quantidade
        )
        for (
            codigo,
            quantidade,
        ) in rows
    }


def validar_resultado(
    conn,
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                indicador_codigo,
                count(*) as registros,
                count(
                    distinct ativo_id
                ) as ativos,
                min(
                    data_referencia
                ) as primeira_data,
                max(
                    data_referencia
                ) as ultima_data,
                min(
                    qualidade_dado
                ) as qualidade_minima,
                max(
                    qualidade_dado
                ) as qualidade_maxima
            from
                investimento.motor_metricas_observadas
            where
                fonte in (
                    %s,
                    %s
                )
            group by
                indicador_codigo
            order by
                indicador_codigo
            """,
            (
                FONTE_CVM,
                FONTE_PROVENTOS,
            ),
        )

        rows = cur.fetchall()

    return [
        {
            "indicador":
                row[0],

            "registros":
                int(
                    row[1] or 0
                ),

            "ativos":
                int(
                    row[2] or 0
                ),

            "primeira_data":
                row[3],

            "ultima_data":
                row[4],

            "qualidade_minima":
                row[5],

            "qualidade_maxima":
                row[6],
        }
        for row in rows
    ]


def main() -> int:
    conn = connect()

    log_id = None

    try:
        log_id = criar_log(
            conn
        )

        universo = contar_universo(
            conn
        )

        apagados = limpar_metricas(
            conn
        )

        contagens = calcular_metricas(
            conn
        )

        gravados = sum(
            contagens.values()
        )

        # DELETE e novo cálculo permanecem
        # dentro da mesma transação.
        # Em caso de erro, o rollback
        # preserva as métricas anteriores.
        conn.commit()

        validacao = validar_resultado(
            conn
        )

        mensagem = (
            "Métricas MOTOR_V1 calculadas; "
            f"universo_acoes={universo}; "
            f"metricas_anteriores_substituidas={apagados}; "
            f"registros_gravados={gravados}; "
            f"contagens={contagens}."
        )

        finalizar_log(
            conn=conn,
            log_id=log_id,
            status="SUCESSO",
            lidos=universo,
            gravados=gravados,
            mensagem=mensagem,
        )

        print(
            "Métricas do motor: SUCESSO"
        )

        print(
            f"Universo de ações: {universo}"
        )

        print(
            "Métricas anteriores substituídas: "
            f"{apagados}"
        )

        print(
            f"Registros gravados: {gravados}"
        )

        for item in validacao:
            print(
                (
                    f"{item['indicador']}: "
                    f"{item['registros']} registros | "
                    f"{item['ativos']} ativos | "
                    f"{item['primeira_data']} a "
                    f"{item['ultima_data']} | "
                    "qualidade "
                    f"{item['qualidade_minima']}.."
                    f"{item['qualidade_maxima']}"
                )
            )

        print(
            (
                "ROE, margem líquida, "
                "liquidez corrente e DY 12M "
                "permanecem dinâmicos na view."
            )
        )

        print(
            (
                "Volatilidade 252D e drawdown 3A "
                "ficam pendentes até haver "
                "preços ajustados."
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
                "Métricas do motor: erro | "
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
