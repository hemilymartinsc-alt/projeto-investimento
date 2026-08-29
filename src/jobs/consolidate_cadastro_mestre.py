from __future__ import annotations

import sys

from src.database import connect


PROCESS_NAME = "consolidar_cadastro_mestre_ativos"
SOURCE_CODE = "B3_LISTED_COMPANIES"


UPSERT_SQL = """
insert into investimento.ativos (
    ticker,
    nome,
    nome_pregao,
    classe,
    setor,
    segmento,
    cnpj,
    codigo_cvm,
    moeda,
    ativo,
    isin,
    fonte_cadastro,
    url_fonte,
    codigo_emissor_b3,
    status_validacao,
    elegivel_analise,
    motivo_exclusao,
    atualizado_em
)
select
    t.ticker,
    coalesce(
        b.razao_social,
        c.denominacao_social,
        b.nome_pregao,
        t.ticker
    ) as nome,
    b.nome_pregao,
    'NAO_CLASSIFICADO' as classe,
    coalesce(c.setor_atividade, b.classificacao_setorial) as setor,
    b.segmento_listagem as segmento,
    coalesce(b.cnpj, c.cnpj) as cnpj,
    t.codigo_cvm,
    'BRL' as moeda,
    false as ativo,
    t.isin,
    'B3_LISTED_COMPANIES' as fonte_cadastro,
    'https://sistemaswebb3-listados.b3.com.br/listedCompaniesPage/' as url_fonte,
    b.codigo_emissor,
    'PENDENTE_VALIDACAO' as status_validacao,
    false as elegivel_analise,
    null as motivo_exclusao,
    now() as atualizado_em
from investimento.b3_tickers t
join investimento.b3_empresas_listadas b
  on b.codigo_cvm = t.codigo_cvm
left join investimento.cvm_cadastro_companhias c
  on c.codigo_cvm = t.codigo_cvm
where t.isin is not null
on conflict (ticker) do update set
    nome = excluded.nome,
    nome_pregao = excluded.nome_pregao,
    setor = excluded.setor,
    segmento = excluded.segmento,
    cnpj = excluded.cnpj,
    codigo_cvm = excluded.codigo_cvm,
    isin = excluded.isin,
    fonte_cadastro = excluded.fonte_cadastro,
    url_fonte = excluded.url_fonte,
    codigo_emissor_b3 = excluded.codigo_emissor_b3,
    atualizado_em = now()
returning ticker
"""


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
    registros_gravados: int,
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
                registros_gravados,
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
        with conn.cursor() as cur:
            cur.execute(UPSERT_SQL)
            tickers = [row[0] for row in cur.fetchall()]

        conn.commit()

        finish_log(
            conn,
            log_id,
            "SUCESSO",
            len(tickers),
            (
                f"Cadastro mestre atualizado com {len(tickers)} tickers com ISIN. "
                "Ativos permanecem fora da análise até validação oficial de "
                "instrumento/classificação B3."
            ),
        )

        print(f"Cadastro mestre: SUCESSO | tickers={len(tickers)}")
        return 0

    except Exception as exc:
        conn.rollback()
        try:
            finish_log(conn, log_id, "ERRO", 0, str(exc))
        finally:
            print(f"Cadastro mestre: erro | {exc}", file=sys.stderr)
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
