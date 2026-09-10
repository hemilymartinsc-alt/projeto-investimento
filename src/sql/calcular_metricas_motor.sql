with universo_acoes as (
    select ativo_id
    from investimento.vw_motor_universo_v1
    where universo_investivel_v1
      and classe = 'ACAO'
),

max_fund as (
    select
        max(f.data_referencia) as data_maxima
    from investimento.fundamentos_snapshot f
    join universo_acoes u
      on u.ativo_id = f.ativo_id
),

fund_latest as (
    select distinct on (f.ativo_id)
        f.ativo_id,
        f.data_referencia,
        f.receita_ttm,
        f.lucro_liquido_ttm
    from investimento.fundamentos_snapshot f
    join universo_acoes u
      on u.ativo_id = f.ativo_id
    order by
        f.ativo_id,
        f.data_referencia desc,
        f.calculado_em desc
),

fund_3a as (
    select
        l.ativo_id,
        l.data_referencia,
        l.receita_ttm,
        l.lucro_liquido_ttm,

        p.data_referencia
            as data_3a,

        p.receita_ttm
            as receita_3a,

        p.lucro_liquido_ttm
            as lucro_3a,

        (
            (
                l.data_referencia
                - p.data_referencia
            )::numeric
            / 365.2425
        ) as anos,

        abs(
            p.data_referencia
            - (
                l.data_referencia
                - interval '3 years'
            )::date
        ) as desvio_dias,

        case
            when
                m.data_maxima
                - l.data_referencia
                <= 120
                then 0.95::numeric

            when
                m.data_maxima
                - l.data_referencia
                <= 365
                then 0.80::numeric

            when
                m.data_maxima
                - l.data_referencia
                <= 730
                then 0.55::numeric

            else 0.30::numeric
        end as qualidade_frescor

    from fund_latest l

    cross join max_fund m

    left join lateral (
        select
            f2.data_referencia,
            f2.receita_ttm,
            f2.lucro_liquido_ttm

        from investimento.fundamentos_snapshot f2

        where f2.ativo_id
              = l.ativo_id

          and f2.data_referencia between
              (
                  (
                      l.data_referencia
                      - interval '3 years'
                  )::date
                  - 60
              )
              and
              (
                  (
                      l.data_referencia
                      - interval '3 years'
                  )::date
                  + 60
              )

        order by
            abs(
                f2.data_referencia
                - (
                    l.data_referencia
                    - interval '3 years'
                )::date
            ),
            f2.data_referencia desc

        limit 1
    ) p on true
),

metricas_crescimento as (
    select
        ativo_id,
        data_referencia,
        'CAGR_RECEITA_3A'::text
            as indicador_codigo,

        power(
            receita_ttm
            / receita_3a,
            1::numeric
            / anos
        ) - 1
            as valor,

        'CALC_CVM_TTM_V1'::text
            as fonte,

        false
            as fonte_oficial,

        least(
            qualidade_frescor,
            case
                when desvio_dias <= 31
                    then 0.95::numeric
                else 0.85::numeric
            end
        ) as qualidade_dado

    from fund_3a

    where receita_ttm > 0
      and receita_3a > 0
      and anos between 2.7 and 3.3

    union all

    select
        ativo_id,
        data_referencia,
        'CAGR_LUCRO_3A'::text
            as indicador_codigo,

        power(
            lucro_liquido_ttm
            / lucro_3a,
            1::numeric
            / anos
        ) - 1
            as valor,

        'CALC_CVM_TTM_V1'::text
            as fonte,

        false
            as fonte_oficial,

        least(
            qualidade_frescor,
            case
                when desvio_dias <= 31
                    then 0.95::numeric
                else 0.85::numeric
            end
        ) as qualidade_dado

    from fund_3a

    where lucro_liquido_ttm > 0
      and lucro_3a > 0
      and anos between 2.7 and 3.3
),

lucro_painel as (
    select
        f.ativo_id,
        f.data_referencia,

        f.lucro_liquido_ttm
            as lucro_0,

        p1.lucro_liquido_ttm
            as lucro_1,

        p2.lucro_liquido_ttm
            as lucro_2,

        f.lucro_3a
            as lucro_3,

        f.qualidade_frescor

    from fund_3a f

    left join lateral (
        select
            f2.lucro_liquido_ttm

        from investimento.fundamentos_snapshot f2

        where f2.ativo_id
              = f.ativo_id

          and f2.data_referencia between
              (
                  (
                      f.data_referencia
                      - interval '1 year'
                  )::date
                  - 60
              )
              and
              (
                  (
                      f.data_referencia
                      - interval '1 year'
                  )::date
                  + 60
              )

        order by
            abs(
                f2.data_referencia
                - (
                    f.data_referencia
                    - interval '1 year'
                )::date
            ),
            f2.data_referencia desc

        limit 1
    ) p1 on true

    left join lateral (
        select
            f2.lucro_liquido_ttm

        from investimento.fundamentos_snapshot f2

        where f2.ativo_id
              = f.ativo_id

          and f2.data_referencia between
              (
                  (
                      f.data_referencia
                      - interval '2 years'
                  )::date
                  - 60
              )
              and
              (
                  (
                      f.data_referencia
                      - interval '2 years'
                  )::date
                  + 60
              )

        order by
            abs(
                f2.data_referencia
                - (
                    f.data_referencia
                    - interval '2 years'
                )::date
            ),
            f2.data_referencia desc

        limit 1
    ) p2 on true
),

lucro_long as (
    select
        p.ativo_id,
        p.data_referencia,
        p.qualidade_frescor,
        v.valor

    from lucro_painel p

    cross join lateral (
        values
            (p.lucro_0),
            (p.lucro_1),
            (p.lucro_2),
            (p.lucro_3)
    ) v(valor)

    where v.valor is not null
),

lucro_stats as (
    select
        ativo_id,
        data_referencia,

        min(
            qualidade_frescor
        ) as qualidade_frescor,

        count(*)
            as n_obs,

        count(*) filter (
            where valor > 0
        ) as n_positivos,

        avg(valor)
            as media_lucro,

        stddev_samp(valor)
            as desvio_lucro

    from lucro_long

    group by
        ativo_id,
        data_referencia

    having count(*) = 4
),

metrica_estabilidade as (
    select
        ativo_id,
        data_referencia,

        'ESTABILIDADE_LUCRO'::text
            as indicador_codigo,

        100::numeric
        * (
            0.65::numeric
            * (
                n_positivos::numeric
                / 4::numeric
            )
            +
            0.35::numeric
            * case
                when media_lucro > 0
                    then
                        1::numeric
                        / (
                            1::numeric
                            + coalesce(
                                desvio_lucro,
                                0::numeric
                            )
                            / nullif(
                                abs(
                                    media_lucro
                                ),
                                0::numeric
                            )
                        )
                else 0::numeric
            end
        ) as valor,

        'CALC_CVM_TTM_V1'::text
            as fonte,

        false
            as fonte_oficial,

        least(
            qualidade_frescor,
            0.90::numeric
        ) as qualidade_dado

    from lucro_stats
),

quote_span as (
    select
        c.ativo_id,

        min(c.data)
            as primeira_cotacao,

        max(c.data)
            as ultima_cotacao

    from investimento.cotacoes_diarias c

    join universo_acoes u
      on u.ativo_id = c.ativo_id

    where c.fonte = 'B3_HIST'
      and c.fechamento > 0

    group by
        c.ativo_id
),

prov_somas as (
    select
        q.ativo_id,
        q.primeira_cotacao,
        q.ultima_cotacao,

        coalesce(
            sum(
                p.valor_por_unidade
            ) filter (
                where
                    p.fonte = 'B3_PROVENTOS'

                    and p.fonte_oficial

                    and p.status = 'CONFIRMADO'

                    and p.tipo in (
                        'DIVIDENDO',
                        'JCP'
                    )

                    and coalesce(
                        p.data_pagamento,
                        p.data_ex,
                        p.data_com
                    )
                    > q.ultima_cotacao
                      - interval '1 year'

                    and coalesce(
                        p.data_pagamento,
                        p.data_ex,
                        p.data_com
                    )
                    <= q.ultima_cotacao
            ),
            0::numeric
        ) as prov_0_1,

        coalesce(
            sum(
                p.valor_por_unidade
            ) filter (
                where
                    p.fonte = 'B3_PROVENTOS'

                    and p.fonte_oficial

                    and p.status = 'CONFIRMADO'

                    and p.tipo in (
                        'DIVIDENDO',
                        'JCP'
                    )

                    and coalesce(
                        p.data_pagamento,
                        p.data_ex,
                        p.data_com
                    )
                    > q.ultima_cotacao
                      - interval '2 years'

                    and coalesce(
                        p.data_pagamento,
                        p.data_ex,
                        p.data_com
                    )
                    <= q.ultima_cotacao
                       - interval '1 year'
            ),
            0::numeric
        ) as prov_1_2,

        coalesce(
            sum(
                p.valor_por_unidade
            ) filter (
                where
                    p.fonte = 'B3_PROVENTOS'

                    and p.fonte_oficial

                    and p.status = 'CONFIRMADO'

                    and p.tipo in (
                        'DIVIDENDO',
                        'JCP'
                    )

                    and coalesce(
                        p.data_pagamento,
                        p.data_ex,
                        p.data_com
                    )
                    > q.ultima_cotacao
                      - interval '3 years'

                    and coalesce(
                        p.data_pagamento,
                        p.data_ex,
                        p.data_com
                    )
                    <= q.ultima_cotacao
                       - interval '2 years'
            ),
            0::numeric
        ) as prov_2_3,

        coalesce(
            sum(
                p.valor_por_unidade
            ) filter (
                where
                    p.fonte = 'B3_PROVENTOS'

                    and p.fonte_oficial

                    and p.status = 'CONFIRMADO'

                    and p.tipo in (
                        'DIVIDENDO',
                        'JCP'
                    )

                    and coalesce(
                        p.data_pagamento,
                        p.data_ex,
                        p.data_com
                    )
                    > q.ultima_cotacao
                      - interval '4 years'

                    and coalesce(
                        p.data_pagamento,
                        p.data_ex,
                        p.data_com
                    )
                    <= q.ultima_cotacao
                       - interval '3 years'
            ),
            0::numeric
        ) as prov_3_4

    from quote_span q

    left join investimento.proventos p
      on p.ativo_id = q.ativo_id

    group by
        q.ativo_id,
        q.primeira_cotacao,
        q.ultima_cotacao
),

metricas_proventos as (
    select
        ativo_id,

        ultima_cotacao
            as data_referencia,

        'REGULARIDADE_PROVENTOS'::text
            as indicador_codigo,

        (
            (
                case
                    when prov_0_1 > 0
                        then 1
                    else 0
                end
            )
            +
            (
                case
                    when prov_1_2 > 0
                        then 1
                    else 0
                end
            )
            +
            (
                case
                    when prov_2_3 > 0
                        then 1
                    else 0
                end
            )
        )::numeric
        / 3::numeric
            as valor,

        'CALC_B3_PROVENTOS_V1'::text
            as fonte,

        false
            as fonte_oficial,

        0.95::numeric
            as qualidade_dado

    from prov_somas

    where
        primeira_cotacao
        <= ultima_cotacao
           - interval '3 years'

    union all

    select
        ativo_id,

        ultima_cotacao
            as data_referencia,

        'CAGR_PROVENTOS_3A'::text
            as indicador_codigo,

        power(
            prov_0_1
            / prov_3_4,
            1::numeric
            / 3::numeric
        ) - 1
            as valor,

        'CALC_B3_PROVENTOS_V1'::text
            as fonte,

        false
            as fonte_oficial,

        0.90::numeric
            as qualidade_dado

    from prov_somas

    where
        primeira_cotacao
        <= ultima_cotacao
           - interval '4 years'

      and prov_0_1 > 0
      and prov_3_4 > 0
),

metricas_novas as (
    select *
    from metricas_crescimento

    union all

    select *
    from metrica_estabilidade

    union all

    select *
    from metricas_proventos
),

inseridos as (
    insert into
        investimento.motor_metricas_observadas (
            ativo_id,
            data_referencia,
            indicador_codigo,
            valor,
            fonte,
            fonte_oficial,
            qualidade_dado,
            coletado_em
        )

    select
        ativo_id,
        data_referencia,
        indicador_codigo,
        valor,
        fonte,
        fonte_oficial,
        qualidade_dado,
        now()

    from metricas_novas

    where valor is not null

    on conflict (
        ativo_id,
        data_referencia,
        indicador_codigo,
        fonte
    )
    do update set

        valor =
            excluded.valor,

        fonte_oficial =
            excluded.fonte_oficial,

        qualidade_dado =
            excluded.qualidade_dado,

        coletado_em =
            now()

    returning
        indicador_codigo
)

select
    indicador_codigo,

    count(*)::integer
        as registros

from inseridos

group by
    indicador_codigo

order by
    indicador_codigo;
