from __future__ import annotations

import sys
from datetime import date

from src.database import connect


PROCESS_NAME = "calcular_valuation"
SOURCE_CODE = "MOTOR_VALUATION_V1"
MODELO = "MOTOR_V1"
METODO = "MULTIPLOS_RELATIVOS_PE_PB_V1"

PILARES = (
    "QUALIDADE",
    "SAUDE_FINANCEIRA",
    "CRESCIMENTO",
    "RENDA",
    "VALUATION",
    "RISCO",
)


def garantir_fonte(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.fontes_dados (
                codigo,
                nome,
                tipo,
                oficial,
                url_base,
                periodicidade,
                finalidade,
                ativa
            )
            values (
                %s,
                %s,
                'FUNDAMENTOS',
                false,
                null,
                'SEMANAL',
                %s,
                true
            )
            on conflict (codigo)
            do update set
                nome = excluded.nome,
                tipo = excluded.tipo,
                oficial = excluded.oficial,
                periodicidade = excluded.periodicidade,
                finalidade = excluded.finalidade,
                ativa = true,
                atualizado_em = now()
            """,
            (
                SOURCE_CODE,
                "Motor - Valuation Relativo V1",
                (
                    "Materialização do valuation relativo "
                    "P/L + P/VP e atualização dos resultados "
                    "derivados do MOTOR_V1."
                ),
            ),
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


def contar_universo(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            select count(*)
            from investimento.vw_motor_universo_v1
            where universo_investivel_v1
            """
        )

        total = cur.fetchone()[0]

    return int(
        total or 0
    )


def materializar_pilares_premium(
    conn,
    data_referencia: date,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            delete from investimento.motor_scores_pilares
            where modelo_codigo = %s
              and data_referencia = %s
              and pilar in (
                  'QUALIDADE',
                  'CRESCIMENTO'
              )
            """,
            (
                MODELO,
                data_referencia,
            ),
        )

        cur.execute(
            """
            insert into investimento.motor_scores_pilares (
                ativo_id,
                data_referencia,
                modelo_codigo,
                perfil,
                pilar,
                score,
                cobertura,
                qtd_indicadores,
                qtd_indicadores_disponiveis,
                qualidade_media,
                confianca_pilar,
                cobertura_suficiente
            )
            select
                ativo_id,
                %s,
                modelo_codigo,
                perfil,
                pilar,
                score,
                cobertura,
                qtd_indicadores,
                qtd_indicadores_disponiveis,
                qualidade_media,
                confianca_pilar,
                cobertura_suficiente
            from investimento.vw_motor_scores_pilares_calculados_v1
            where pilar in (
                'QUALIDADE',
                'CRESCIMENTO'
            )
            """,
            (
                data_referencia,
            ),
        )

        gravados = cur.rowcount

    return int(
        gravados or 0
    )


def materializar_valuations(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.motor_valuations (
                ativo_id,
                data_referencia,
                metodo,
                valor_justo_conservador,
                valor_justo_base,
                valor_justo_otimista,
                preco_referencia,
                margem_seguranca,
                score_valuation,
                confianca,
                premissas,
                fonte,
                calculado_em
            )
            select
                ativo_id,
                data_referencia,
                metodo,
                valor_justo_conservador,
                valor_justo_base,
                valor_justo_otimista,
                preco_referencia,
                margem_seguranca,
                score_valuation,
                confianca,
                premissas,
                fonte,
                now()
            from investimento.vw_valuation_relativo_calculado_v1

            on conflict (
                ativo_id,
                data_referencia,
                metodo
            )
            do update set
                valor_justo_conservador =
                    excluded.valor_justo_conservador,

                valor_justo_base =
                    excluded.valor_justo_base,

                valor_justo_otimista =
                    excluded.valor_justo_otimista,

                preco_referencia =
                    excluded.preco_referencia,

                margem_seguranca =
                    excluded.margem_seguranca,

                score_valuation =
                    excluded.score_valuation,

                confianca =
                    excluded.confianca,

                premissas =
                    excluded.premissas,

                fonte =
                    excluded.fonte,

                calculado_em =
                    now()
            """
        )

        gravados = cur.rowcount

    return int(
        gravados or 0
    )


def materializar_pilares(
    conn,
    data_referencia: date,
) -> int:
    total = 0

    with conn.cursor() as cur:
        for pilar in PILARES:
            cur.execute(
                """
                delete from investimento.motor_scores_pilares
                where modelo_codigo = %s
                  and data_referencia = %s
                  and pilar = %s
                """,
                (
                    MODELO,
                    data_referencia,
                    pilar,
                ),
            )

            cur.execute(
                """
                insert into investimento.motor_scores_pilares (
                    ativo_id,
                    data_referencia,
                    modelo_codigo,
                    perfil,
                    pilar,
                    score,
                    cobertura,
                    qtd_indicadores,
                    qtd_indicadores_disponiveis,
                    qualidade_media,
                    confianca_pilar,
                    cobertura_suficiente
                )
                select
                    ativo_id,
                    %s,
                    modelo_codigo,
                    perfil,
                    pilar,
                    score,
                    cobertura,
                    qtd_indicadores,
                    qtd_indicadores_disponiveis,
                    qualidade_media,
                    confianca_pilar,
                    cobertura_suficiente
                from investimento.vw_motor_scores_pilares_calculados_v1
                where pilar = %s
                """,
                (
                    data_referencia,
                    pilar,
                ),
            )

            gravados = int(
                cur.rowcount or 0
            )

            total += gravados

            print(
                (
                    f"Pilar {pilar}: "
                    f"{gravados} registros."
                ),
                flush=True,
            )

    return total


def materializar_resultados(
    conn,
    data_referencia: date,
) -> tuple[int, dict[str, int]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            with p as (
                select
                    s.ativo_id,
                    s.perfil,

                    sum(
                        case
                            when w.peso_score_ativo > 0
                             and s.score is not null
                                then
                                    s.score
                                    * w.peso_score_ativo
                            else 0
                        end
                    ) as soma_ativo,

                    sum(
                        case
                            when w.peso_score_ativo > 0
                             and s.score is not null
                                then
                                    w.peso_score_ativo
                            else 0
                        end
                    ) as peso_ativo,

                    sum(
                        case
                            when w.peso_score_oportunidade > 0
                             and s.score is not null
                                then
                                    s.score
                                    * w.peso_score_oportunidade
                            else 0
                        end
                    ) as soma_oportunidade,

                    sum(
                        case
                            when w.peso_score_oportunidade > 0
                             and s.score is not null
                                then
                                    w.peso_score_oportunidade
                            else 0
                        end
                    ) as peso_oportunidade,

                    sum(
                        coalesce(
                            s.confianca_pilar,
                            s.cobertura
                        )
                        * w.peso_score_oportunidade
                    ) as confianca_ponderada,

                    sum(
                        w.peso_score_oportunidade
                    ) as peso_conf

                from investimento.motor_scores_pilares s

                join investimento.motor_pesos_pilares w
                  on w.modelo_codigo = %s
                 and w.perfil = s.perfil
                 and w.pilar = s.pilar
                 and w.ativo

                where s.modelo_codigo = %s
                  and s.data_referencia = %s

                group by
                    s.ativo_id,
                    s.perfil
            ),
            params as (
                select
                    max(valor_numerico) filter (
                        where parametro =
                            'CONFIANCA_MINIMA_COMPRA'
                    ) as conf_min,

                    max(valor_numerico) filter (
                        where parametro =
                            'SCORE_ATIVO_MIN_COMPRA'
                    ) as ativo_compra,

                    max(valor_numerico) filter (
                        where parametro =
                            'SCORE_OPORTUNIDADE_MIN_COMPRA'
                    ) as oportunidade_compra,

                    max(valor_numerico) filter (
                        where parametro =
                            'SCORE_ATIVO_MIN_MANTER'
                    ) as ativo_manter,

                    max(valor_numerico) filter (
                        where parametro =
                            'SCORE_ATIVO_MAX_NAO_COMPRAR'
                    ) as ativo_nao

                from investimento.motor_parametros
                where modelo_codigo = %s
            ),
            calc as (
                select
                    p.ativo_id,
                    p.perfil,

                    case
                        when peso_ativo > 0
                            then round(
                                soma_ativo / peso_ativo,
                                2
                            )
                    end as score_ativo,

                    case
                        when peso_oportunidade > 0
                            then round(
                                soma_oportunidade
                                / peso_oportunidade,
                                2
                            )
                    end as score_oportunidade,

                    case
                        when peso_conf > 0
                            then round(
                                100
                                * confianca_ponderada
                                / peso_conf,
                                2
                            )
                        else 0
                    end as score_confianca,

                    exists (
                        select 1
                        from investimento.motor_red_flags r
                        where r.ativo_id = p.ativo_id
                          and r.ativo
                          and r.bloqueia_compra
                    ) as bloqueado

                from p
            ),
            final as (
                select
                    c.*,
                    pr.*,

                    case
                        when bloqueado
                            then 'NAO_COMPRAR'

                        when score_confianca < conf_min
                            then 'AGUARDAR'

                        when score_ativo is null
                          or score_oportunidade is null
                            then 'AGUARDAR'

                        when score_ativo < ativo_nao
                            then 'NAO_COMPRAR'

                        when score_ativo >= ativo_compra
                         and score_oportunidade
                             >= oportunidade_compra
                            then 'COMPRAR'

                        when score_ativo >= ativo_manter
                         and score_oportunidade >= 55
                            then 'MANTER'

                        else 'AGUARDAR'
                    end as decisao_ativo

                from calc c
                cross join params pr
            ),
            upserted as (
                insert into investimento.motor_resultados (
                    ativo_id,
                    data_referencia,
                    modelo_codigo,
                    perfil,
                    score_ativo,
                    score_oportunidade,
                    score_confianca,
                    decisao_ativo,
                    motivo_principal,
                    bloqueado,
                    explicacoes,
                    calculado_em
                )
                select
                    ativo_id,
                    %s,
                    %s,
                    perfil,
                    score_ativo,
                    score_oportunidade,
                    score_confianca,
                    decisao_ativo,

                    case
                        when bloqueado
                            then
                                'Compra bloqueada por red flag ativa.'

                        when score_confianca < conf_min
                            then
                                'Confiança agregada insuficiente para uma decisão de compra confiável.'

                        when decisao_ativo = 'COMPRAR'
                            then
                                'Qualidade estrutural e oportunidade superam os limites configurados do modelo.'

                        when decisao_ativo = 'MANTER'
                            then
                                'Qualidade suficiente, mas a oportunidade não justifica priorização máxima de novo capital.'

                        when decisao_ativo = 'NAO_COMPRAR'
                            then
                                'Qualidade estrutural abaixo do limite ou risco impeditivo.'

                        else
                            'Relação atual entre qualidade, valuation, risco e confiança recomenda aguardar.'
                    end,

                    bloqueado,

                    jsonb_build_object(
                        'modelo',
                        %s::text,
                        'origem',
                        'PILARES_MATERIALIZADOS',
                        'data_materializacao',
                        %s::date
                    ),

                    now()

                from final

                on conflict (
                    ativo_id,
                    data_referencia,
                    modelo_codigo
                )
                do update set
                    perfil =
                        excluded.perfil,

                    score_ativo =
                        excluded.score_ativo,

                    score_oportunidade =
                        excluded.score_oportunidade,

                    score_confianca =
                        excluded.score_confianca,

                    decisao_ativo =
                        excluded.decisao_ativo,

                    motivo_principal =
                        excluded.motivo_principal,

                    bloqueado =
                        excluded.bloqueado,

                    explicacoes =
                        excluded.explicacoes,

                    calculado_em =
                        now()

                returning decisao_ativo
            )
            select
                decisao_ativo,
                count(*)
            from upserted
            group by decisao_ativo
            order by decisao_ativo
            """,
            (
                MODELO,
                MODELO,
                data_referencia,
                MODELO,
                data_referencia,
                MODELO,
                MODELO,
                data_referencia,
            ),
        )

        rows = cur.fetchall()

    distribuicao = {
        str(decisao): int(
            quantidade
        )
        for decisao, quantidade
        in rows
    }

    return (
        sum(
            distribuicao.values()
        ),
        distribuicao,
    )


def validar_valuations(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                count(*) as registros,
                count(distinct ativo_id) as ativos,
                min(confianca) as confianca_min,
                max(confianca) as confianca_max,
                count(*) filter (
                    where margem_seguranca > 0
                ) as com_desconto
            from investimento.motor_valuations
            where metodo = %s
              and data_referencia = (
                  select max(data_referencia)
                  from investimento.motor_valuations
                  where metodo = %s
              )
            """,
            (
                METODO,
                METODO,
            ),
        )

        row = cur.fetchone()

    return {
        "registros": int(
            row[0] or 0
        ),
        "ativos": int(
            row[1] or 0
        ),
        "confianca_min":
            row[2],
        "confianca_max":
            row[3],
        "com_desconto": int(
            row[4] or 0
        ),
    }


def main() -> int:
    data_referencia = date.today()

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

        universo = contar_universo(
            conn
        )

        premium = materializar_pilares_premium(
            conn,
            data_referencia,
        )

        valuations = materializar_valuations(
            conn
        )

        pilares = materializar_pilares(
            conn,
            data_referencia,
        )

        (
            resultados,
            decisoes,
        ) = materializar_resultados(
            conn,
            data_referencia,
        )

        conn.commit()

        resumo_val = validar_valuations(
            conn
        )

        mensagem = (
            "Valuation relativo MOTOR_V1; "
            f"universo={universo}; "
            f"pilares_premium={premium}; "
            f"valuations_upsert={valuations}; "
            f"pilares_materializados={pilares}; "
            f"resultados={resultados}; "
            f"decisoes={decisoes}; "
            f"valuation_ativos={resumo_val['ativos']}; "
            f"com_desconto={resumo_val['com_desconto']}."
        )

        finalizar_log(
            conn=conn,
            log_id=log_id,
            status="SUCESSO",
            lidos=universo,
            gravados=resultados,
            mensagem=mensagem,
        )

        print(
            "Valuation MOTOR_V1: SUCESSO",
            flush=True,
        )

        print(
            (
                "Universo: "
                f"{universo}"
            ),
            flush=True,
        )

        print(
            (
                "Valuations: "
                f"{resumo_val}"
            ),
            flush=True,
        )

        print(
            (
                "Pilares materializados: "
                f"{pilares}"
            ),
            flush=True,
        )

        print(
            (
                "Resultados: "
                f"{resultados}"
            ),
            flush=True,
        )

        print(
            (
                "Decisoes: "
                f"{decisoes}"
            ),
            flush=True,
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
                "Valuation MOTOR_V1: erro | "
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
