from __future__ import annotations

import sys

from src.database import connect


PROCESS_NAME = "calcular_fundamentos"
SOURCE_CODE = "CVM_DFP_ITR"
FONTE_BASE = "CVM_DFP_ITR"


CALCULAR_SQL = r"""
with acoes as (
    select
        ativo_id,
        codigo_cvm
    from investimento.vw_acoes_validacao_oficial_atual
    where elegivel_analise = true
      and codigo_cvm is not null
),

setores as (
    select
        codigo_cvm,
        coalesce(
            max(classificacao_setorial) filter (
                where classificacao_setorial is not null
                  and lower(classificacao_setorial)
                      not like 'não classific%'
                  and lower(classificacao_setorial)
                      not like 'nao classific%'
                  and lower(classificacao_setorial)
                      not like 'carga inicial%'
            ),
            max(classificacao_setorial) filter (
                where classificacao_setorial is not null
            )
        ) as classificacao_setorial
    from investimento.b3_empresas_listadas
    where codigo_cvm is not null
    group by codigo_cvm
),

dre_raw as (
    select
        codigo_cvm,
        data_referencia,
        fonte,

        max(valor) filter (
            where codigo_conta = '3.01'
        ) as receita,

        max(descricao_conta) filter (
            where codigo_conta = '3.01'
        ) as receita_descricao,

        max(valor) filter (
            where codigo_conta = '3.11'
        ) as lucro

    from investimento.demonstracoes_financeiras

    where fonte in (
        'CVM_DFP',
        'CVM_ITR'
    )
      and tipo_demonstracao = 'DRE'
      and codigo_conta in (
          '3.01',
          '3.11'
      )
      and codigo_cvm is not null

    group by
        codigo_cvm,
        data_referencia,
        fonte
),

dre_atual as (
    select distinct on (
        codigo_cvm,
        data_referencia
    )
        codigo_cvm,
        data_referencia,
        fonte,
        receita,
        receita_descricao,
        lucro

    from dre_raw

    order by
        codigo_cvm,
        data_referencia,
        case
            when fonte = 'CVM_DFP'
                then 0
            else 1
        end
),

bp_raw as (
    select
        codigo_cvm,
        data_referencia,
        fonte,

        max(valor) filter (
            where tipo_demonstracao = 'BPP'
              and codigo_conta = '2.03'
              and (
                  lower(descricao_conta)
                      like '%patrimônio líquido%'
                  or lower(descricao_conta)
                      like '%patrimonio liquido%'
              )
        ) as patrimonio_liquido,

        max(valor) filter (
            where tipo_demonstracao = 'BPA'
              and codigo_conta = '1.01'
              and lower(descricao_conta)
                  = 'ativo circulante'
        ) as ativo_circulante,

        max(valor) filter (
            where tipo_demonstracao = 'BPP'
              and codigo_conta = '2.01'
              and lower(descricao_conta)
                  = 'passivo circulante'
        ) as passivo_circulante,

        max(valor) filter (
            where tipo_demonstracao = 'BPA'
              and codigo_conta = '1.01.01'
              and lower(descricao_conta)
                  like 'caixa%'
        ) as caixa,

        max(valor) filter (
            where tipo_demonstracao = 'BPP'
              and codigo_conta = '2.01.04'
              and (
                  lower(descricao_conta)
                      like '%empréstim%'
                  or lower(descricao_conta)
                      like '%emprestim%'
              )
              and lower(descricao_conta)
                  like '%financiam%'
        ) as divida_curto,

        max(valor) filter (
            where tipo_demonstracao = 'BPP'
              and codigo_conta = '2.02.01'
              and (
                  lower(descricao_conta)
                      like '%empréstim%'
                  or lower(descricao_conta)
                      like '%emprestim%'
              )
              and lower(descricao_conta)
                  like '%financiam%'
        ) as divida_longo

    from investimento.demonstracoes_financeiras

    where fonte in (
        'CVM_DFP',
        'CVM_ITR'
    )
      and codigo_cvm is not null
      and (
          (
              tipo_demonstracao = 'BPA'
              and codigo_conta in (
                  '1.01',
                  '1.01.01'
              )
          )
          or
          (
              tipo_demonstracao = 'BPP'
              and codigo_conta in (
                  '2.01',
                  '2.01.04',
                  '2.02.01',
                  '2.03'
              )
          )
      )

    group by
        codigo_cvm,
        data_referencia,
        fonte
),

bp_atual as (
    select distinct on (
        codigo_cvm,
        data_referencia
    )
        *

    from bp_raw

    order by
        codigo_cvm,
        data_referencia,
        case
            when fonte = 'CVM_DFP'
                then 0
            else 1
        end
),

refs as (
    select
        codigo_cvm,
        data_referencia
    from dre_atual

    union

    select
        codigo_cvm,
        data_referencia
    from bp_atual
),

base as (
    select
        r.codigo_cvm,
        r.data_referencia,

        coalesce(
            lower(
                s.classificacao_setorial
            ),
            ''
        ) like 'financeiro%'
            as setor_financeiro,

        d.fonte
            as dre_fonte,

        d.receita
            as receita_atual,

        d.receita_descricao,

        d.lucro
            as lucro_atual,

        b.patrimonio_liquido,

        b.ativo_circulante,

        b.passivo_circulante,

        b.caixa,

        b.divida_curto,

        b.divida_longo,

        anual.receita
            as receita_anual,

        anual.lucro
            as lucro_anual,

        anterior.receita
            as receita_ano_anterior,

        anterior.lucro
            as lucro_ano_anterior,

        pl_anterior.patrimonio_liquido
            as patrimonio_liquido_ano_anterior

    from refs r

    left join setores s
      on s.codigo_cvm = r.codigo_cvm

    left join dre_atual d
      on d.codigo_cvm = r.codigo_cvm
     and d.data_referencia
         = r.data_referencia

    left join bp_atual b
      on b.codigo_cvm = r.codigo_cvm
     and b.data_referencia
         = r.data_referencia

    left join lateral (
        select
            x.receita,
            x.lucro

        from dre_raw x

        where x.codigo_cvm
              = r.codigo_cvm

          and x.fonte
              = 'CVM_DFP'

          and x.data_referencia
              < r.data_referencia

        order by
            x.data_referencia desc

        limit 1
    ) anual on true

    left join lateral (
        select
            x.receita,
            x.lucro

        from dre_raw x

        where x.codigo_cvm
              = r.codigo_cvm

          and x.fonte
              = 'CVM_ITR'

          and x.data_referencia between
              (
                  (
                      r.data_referencia
                      - interval '1 year'
                  )::date
                  - 10
              )
              and
              (
                  (
                      r.data_referencia
                      - interval '1 year'
                  )::date
                  + 10
              )

        order by
            abs(
                x.data_referencia
                - (
                    r.data_referencia
                    - interval '1 year'
                )::date
            ),
            x.data_referencia desc

        limit 1
    ) anterior on true

    left join lateral (
        select
            x.patrimonio_liquido

        from bp_raw x

        where x.codigo_cvm
              = r.codigo_cvm

          and x.patrimonio_liquido
              is not null

          and x.data_referencia between
              (
                  (
                      r.data_referencia
                      - interval '1 year'
                  )::date
                  - 10
              )
              and
              (
                  (
                      r.data_referencia
                      - interval '1 year'
                  )::date
                  + 10
              )

        order by
            abs(
                x.data_referencia
                - (
                    r.data_referencia
                    - interval '1 year'
                )::date
            ),
            case
                when x.fonte = 'CVM_DFP'
                    then 0
                else 1
            end

        limit 1
    ) pl_anterior on true
),

ttm as (
    select
        *,

        case
            when dre_fonte = 'CVM_DFP'
                then receita_atual

            when dre_fonte = 'CVM_ITR'
              and receita_anual
                  is not null
              and receita_ano_anterior
                  is not null
                then
                    receita_anual
                    + receita_atual
                    - receita_ano_anterior

            else null
        end as receita_ttm,

        case
            when dre_fonte = 'CVM_DFP'
                then lucro_atual

            when dre_fonte = 'CVM_ITR'
              and lucro_anual
                  is not null
              and lucro_ano_anterior
                  is not null
                then
                    lucro_anual
                    + lucro_atual
                    - lucro_ano_anterior

            else null
        end as lucro_liquido_ttm

    from base
),

empresa as (
    select
        codigo_cvm,
        data_referencia,
        receita_ttm,
        lucro_liquido_ttm,
        patrimonio_liquido,

        case
            when setor_financeiro
                then null

            when caixa is not null
              and (
                  divida_curto
                      is not null
                  or divida_longo
                      is not null
              )
                then
                    coalesce(
                        divida_curto,
                        0
                    )
                    + coalesce(
                        divida_longo,
                        0
                    )
                    - caixa

            else null
        end as divida_liquida,

        case
            when patrimonio_liquido > 0
              and patrimonio_liquido_ano_anterior > 0
              and lucro_liquido_ttm
                  is not null
                then
                    lucro_liquido_ttm
                    / nullif(
                        (
                            patrimonio_liquido
                            + patrimonio_liquido_ano_anterior
                        ) / 2,
                        0
                    )

            else null
        end as roe,

        case
            when setor_financeiro

              or lower(
                    coalesce(
                        receita_descricao,
                        ''
                    )
                 )
                 like '%intermedia%financeir%'

              or lower(
                    coalesce(
                        receita_descricao,
                        ''
                    )
                 )
                 like '%opera%segur%'

              or lower(
                    coalesce(
                        receita_descricao,
                        ''
                    )
                 )
                 like '%prêmio%'

              or lower(
                    coalesce(
                        receita_descricao,
                        ''
                    )
                 )
                 like '%premio%'

                then null

            when receita_ttm
                    is not null
              and receita_ttm <> 0
              and lucro_liquido_ttm
                    is not null
                then
                    lucro_liquido_ttm
                    / receita_ttm

            else null
        end as margem_liquida,

        case
            when setor_financeiro
                then null

            when ativo_circulante
                    is not null
              and passivo_circulante > 0
                then
                    ativo_circulante
                    / passivo_circulante

            else null
        end as liquidez_corrente

    from ttm
),

linhas as (
    select
        a.ativo_id,
        e.data_referencia,
        e.receita_ttm,
        e.lucro_liquido_ttm,
        e.patrimonio_liquido,
        e.divida_liquida,
        e.roe,
        e.margem_liquida,
        e.liquidez_corrente

    from empresa e

    join acoes a
      on a.codigo_cvm
         = e.codigo_cvm

    where
        e.receita_ttm
            is not null

        or e.lucro_liquido_ttm
            is not null

        or e.patrimonio_liquido
            is not null

        or e.divida_liquida
            is not null

        or e.roe
            is not null

        or e.margem_liquida
            is not null

        or e.liquidez_corrente
            is not null
),

upserted as (
    insert into investimento.fundamentos_snapshot (
        ativo_id,
        data_referencia,
        receita_ttm,
        lucro_liquido_ttm,
        patrimonio_liquido,
        divida_liquida,
        roe,
        margem_liquida,
        liquidez_corrente,
        fonte_base,
        calculado_em
    )

    select
        ativo_id,
        data_referencia,
        receita_ttm,
        lucro_liquido_ttm,
        patrimonio_liquido,
        divida_liquida,
        roe,
        margem_liquida,
        liquidez_corrente,
        'CVM_DFP_ITR',
        now()

    from linhas

    on conflict (
        ativo_id,
        data_referencia
    )
    do update set

        receita_ttm =
            excluded.receita_ttm,

        lucro_liquido_ttm =
            excluded.lucro_liquido_ttm,

        patrimonio_liquido =
            excluded.patrimonio_liquido,

        divida_liquida =
            excluded.divida_liquida,

        roe =
            excluded.roe,

        margem_liquida =
            excluded.margem_liquida,

        liquidez_corrente =
            excluded.liquidez_corrente,

        fonte_base =
            excluded.fonte_base,

        calculado_em =
            now()

    returning 1
)

select
    count(*)

from upserted
"""


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


def contar_demonstracoes(
    conn,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                count(*)
            from investimento.demonstracoes_financeiras
            where fonte in (
                'CVM_DFP',
                'CVM_ITR'
            )
            """
        )

        total = cur.fetchone()[0]

    return int(
        total or 0
    )


def calcular_fundamentos(
    conn,
) -> int:
    with conn.cursor() as cur:

        cur.execute(
            CALCULAR_SQL
        )

        gravados = cur.fetchone()[0]

    return int(
        gravados or 0
    )


def validar_resultado(
    conn,
) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                count(*) as snapshots,

                count(
                    distinct ativo_id
                ) as ativos,

                min(
                    data_referencia
                ) as primeira_data,

                max(
                    data_referencia
                ) as ultima_data,

                count(*) filter (
                    where receita_ttm
                        is not null
                ) as com_receita,

                count(*) filter (
                    where lucro_liquido_ttm
                        is not null
                ) as com_lucro,

                count(*) filter (
                    where patrimonio_liquido
                        is not null
                ) as com_pl,

                count(*) filter (
                    where roe
                        is not null
                ) as com_roe,

                count(*) filter (
                    where margem_liquida
                        is not null
                ) as com_margem,

                count(*) filter (
                    where liquidez_corrente
                        is not null
                ) as com_liquidez,

                count(*) filter (
                    where divida_liquida
                        is not null
                ) as com_divida

            from investimento.fundamentos_snapshot

            where fonte_base = %s
            """,
            (
                FONTE_BASE,
            ),
        )

        row = cur.fetchone()

    return {
        "snapshots": int(
            row[0] or 0
        ),

        "ativos": int(
            row[1] or 0
        ),

        "primeira_data":
            row[2],

        "ultima_data":
            row[3],

        "com_receita": int(
            row[4] or 0
        ),

        "com_lucro": int(
            row[5] or 0
        ),

        "com_pl": int(
            row[6] or 0
        ),

        "com_roe": int(
            row[7] or 0
        ),

        "com_margem": int(
            row[8] or 0
        ),

        "com_liquidez": int(
            row[9] or 0
        ),

        "com_divida": int(
            row[10] or 0
        ),
    }


def main() -> int:
    conn = connect()

    log_id = None

    try:
        log_id = criar_log(
            conn
        )

        lidos = contar_demonstracoes(
            conn
        )

        gravados = calcular_fundamentos(
            conn
        )

        conn.commit()

        resumo = validar_resultado(
            conn
        )

        mensagem = (
            "Fundamentos calculados a partir "
            "de DFP/ITR CVM; "
            f"demonstracoes_lidas={lidos}; "
            f"linhas_atualizadas={gravados}; "
            f"snapshots={resumo['snapshots']}; "
            f"ativos={resumo['ativos']}; "
            f"periodo="
            f"{resumo['primeira_data']}.."
            f"{resumo['ultima_data']}."
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
            "Fundamentos: SUCESSO"
        )

        print(
            "Demonstracoes lidas: "
            f"{lidos}"
        )

        print(
            "Linhas atualizadas: "
            f"{gravados}"
        )

        print(
            "Snapshots no banco: "
            f"{resumo['snapshots']}"
        )

        print(
            "Ativos cobertos: "
            f"{resumo['ativos']}"
        )

        print(
            "Periodo: "
            f"{resumo['primeira_data']} "
            "a "
            f"{resumo['ultima_data']}"
        )

        print(
            "Receita TTM: "
            f"{resumo['com_receita']}"
        )

        print(
            "Lucro TTM: "
            f"{resumo['com_lucro']}"
        )

        print(
            "Patrimonio liquido: "
            f"{resumo['com_pl']}"
        )

        print(
            "ROE: "
            f"{resumo['com_roe']}"
        )

        print(
            "Margem liquida: "
            f"{resumo['com_margem']}"
        )

        print(
            "Liquidez corrente: "
            f"{resumo['com_liquidez']}"
        )

        print(
            "Divida liquida: "
            f"{resumo['com_divida']}"
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
            "Fundamentos: erro | "
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
