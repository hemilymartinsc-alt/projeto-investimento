create or replace view investimento.vw_valuation_relativo_calculado_v1 as
with params as (
    select
        coalesce(
            max(valor_numerico) filter(
                where parametro='GRUPO_COMPARACAO_MINIMO'
            ),
            10
        )::int as grupo_min,

        coalesce(
            max(valor_numerico) filter(
                where parametro='VALUATION_GRUPO_PEQUENO_MIN'
            ),
            5
        )::int as grupo_pequeno_min,

        coalesce(
            max(valor_numerico) filter(
                where parametro='VALUATION_PESO_PE_GERAL'
            ),
            .70
        ) as peso_pe_geral,

        coalesce(
            max(valor_numerico) filter(
                where parametro='VALUATION_PESO_PB_GERAL'
            ),
            .30
        ) as peso_pb_geral,

        coalesce(
            max(valor_numerico) filter(
                where parametro='VALUATION_PESO_PE_FINANCEIRO'
            ),
            .40
        ) as peso_pe_financeiro,

        coalesce(
            max(valor_numerico) filter(
                where parametro='VALUATION_PESO_PB_FINANCEIRO'
            ),
            .60
        ) as peso_pb_financeiro,

        coalesce(
            max(valor_numerico) filter(
                where parametro='VALUATION_CONFIANCA_MAX_RELATIVA'
            ),
            .85
        ) as confianca_max

    from investimento.motor_parametros
    where modelo_codigo='MOTOR_V1'
),
p_latest as (
    select distinct on (ativo_id,pilar)
        ativo_id,
        pilar,
        score,
        cobertura
    from investimento.motor_scores_pilares
    where modelo_codigo='MOTOR_V1'
      and pilar in (
          'QUALIDADE',
          'CRESCIMENTO'
      )
    order by
        ativo_id,
        pilar,
        data_referencia desc,
        calculado_em desc
),
pillar as (
    select
        ativo_id,

        max(score) filter(
            where pilar='QUALIDADE'
        ) as q_score,

        max(cobertura) filter(
            where pilar='QUALIDADE'
        ) as q_cov,

        max(score) filter(
            where pilar='CRESCIMENTO'
        ) as g_score,

        max(cobertura) filter(
            where pilar='CRESCIMENTO'
        ) as g_cov

    from p_latest

    group by
        ativo_id
),
base0 as (
    select
        m.*,
        p.q_score,
        p.q_cov,
        p.g_score,
        p.g_cov,

        (
            coalesce(
                p.q_score*p.q_cov*.60,
                0
            )
            +
            coalesce(
                p.g_score*p.g_cov*.40,
                0
            )
        )
        /
        nullif(
            coalesce(
                p.q_cov*.60,
                0
            )
            +
            coalesce(
                p.g_cov*.40,
                0
            ),
            0
        ) as raw_premium,

        least(
            1::numeric,
            (
                coalesce(
                    p.q_cov*.60,
                    0
                )
                +
                coalesce(
                    p.g_cov*.40,
                    0
                )
            )
            /.50
        ) as premium_reliability

    from investimento.vw_multiplos_atuais_v1 m

    left join pillar p
      on p.ativo_id=m.ativo_id

    where m.elegivel_multiplos
      and (
          m.pl is not null
          or m.pvp is not null
      )
),
base as (
    select
        b.*,

        greatest(
            0::numeric,
            least(
                100::numeric,
                50
                +
                (
                    coalesce(
                        raw_premium,
                        50
                    )
                    -
                    50
                )
                *
                coalesce(
                    premium_reliability,
                    0
                )
            )
        ) as score_premium

    from base0 b
),
reps as (
    select *
    from (
        select
            b.*,

            row_number() over(
                partition by codigo_cvm
                order by
                    coalesce(
                        volume,
                        0
                    ) desc,
                    ticker
            ) as rn

        from base b
    ) x

    where rn=1
),
sec_stats as (
    select
        setor,

        count(*) filter(
            where pl>0
        ) as n_pe,

        percentile_cont(.25)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe25,

        percentile_cont(.50)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe50,

        percentile_cont(.75)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe75,

        percentile_cont(.90)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe90,

        count(*) filter(
            where pvp>0
        ) as n_pb,

        percentile_cont(.25)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb25,

        percentile_cont(.50)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb50,

        percentile_cont(.75)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb75,

        percentile_cont(.90)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb90

    from reps

    group by
        setor
),
broad_stats as (
    select
        setor_amplo,

        count(*) filter(
            where pl>0
        ) as n_pe,

        percentile_cont(.25)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe25,

        percentile_cont(.50)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe50,

        percentile_cont(.75)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe75,

        percentile_cont(.90)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe90,

        count(*) filter(
            where pvp>0
        ) as n_pb,

        percentile_cont(.25)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb25,

        percentile_cont(.50)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb50,

        percentile_cont(.75)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb75,

        percentile_cont(.90)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb90

    from reps

    group by
        setor_amplo
),
global_stats as (
    select
        count(*) filter(
            where pl>0
        ) as n_pe,

        percentile_cont(.25)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe25,

        percentile_cont(.50)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe50,

        percentile_cont(.75)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe75,

        percentile_cont(.90)
        within group(
            order by pl
        ) filter(
            where pl>0
        ) as pe90,

        count(*) filter(
            where pvp>0
        ) as n_pb,

        percentile_cont(.25)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb25,

        percentile_cont(.50)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb50,

        percentile_cont(.75)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb75,

        percentile_cont(.90)
        within group(
            order by pvp
        ) filter(
            where pvp>0
        ) as pb90

    from reps
),
joined as (
    select
        b.*,
        p.*,

        ss.n_pe as sec_n_pe,
        ss.pe25 as sec_pe25,
        ss.pe50 as sec_pe50,
        ss.pe75 as sec_pe75,
        ss.pe90 as sec_pe90,

        ss.n_pb as sec_n_pb,
        ss.pb25 as sec_pb25,
        ss.pb50 as sec_pb50,
        ss.pb75 as sec_pb75,
        ss.pb90 as sec_pb90,

        bs.n_pe as broad_n_pe,
        bs.pe25 as broad_pe25,
        bs.pe50 as broad_pe50,
        bs.pe75 as broad_pe75,
        bs.pe90 as broad_pe90,

        bs.n_pb as broad_n_pb,
        bs.pb25 as broad_pb25,
        bs.pb50 as broad_pb50,
        bs.pb75 as broad_pb75,
        bs.pb90 as broad_pb90,

        gs.n_pe as global_n_pe,
        gs.pe25 as global_pe25,
        gs.pe50 as global_pe50,
        gs.pe75 as global_pe75,
        gs.pe90 as global_pe90,

        gs.n_pb as global_n_pb,
        gs.pb25 as global_pb25,
        gs.pb50 as global_pb50,
        gs.pb75 as global_pb75,
        gs.pb90 as global_pb90

    from base b

    cross join params p

    left join sec_stats ss
      on ss.setor is not distinct from b.setor

    left join broad_stats bs
      on bs.setor_amplo
         is not distinct from b.setor_amplo

    cross join global_stats gs
),
targets as (
    select
        j.*,

        case
            when sec_n_pe>=grupo_min
                then
                    'SETOR_ROBUSTO:'
                    ||coalesce(
                        setor,
                        'SEM_SETOR'
                    )

            when broad_n_pe>=grupo_min
                then
                    'AMPLO_ROBUSTO:'
                    ||coalesce(
                        setor_amplo,
                        'SEM_GRUPO'
                    )

            when sec_n_pe>=grupo_pequeno_min
                then
                    'SETOR_PEQUENO:'
                    ||coalesce(
                        setor,
                        'SEM_SETOR'
                    )

            when broad_n_pe>=grupo_pequeno_min
                then
                    'AMPLO_PEQUENO:'
                    ||coalesce(
                        setor_amplo,
                        'SEM_GRUPO'
                    )

            else 'ACAO_GERAL'
        end as grupo_pe,

        (
            case
                when sec_n_pe>=grupo_min
                    then sec_pe25

                when broad_n_pe>=grupo_min
                    then broad_pe25

                when sec_n_pe>=grupo_pequeno_min
                    then sec_pe25

                when broad_n_pe>=grupo_pequeno_min
                    then broad_pe25

                else global_pe25
            end
        )::numeric as pe25,

        (
            case
                when sec_n_pe>=grupo_min
                    then sec_pe50

                when broad_n_pe>=grupo_min
                    then broad_pe50

                when sec_n_pe>=grupo_pequeno_min
                    then sec_pe50

                when broad_n_pe>=grupo_pequeno_min
                    then broad_pe50

                else global_pe50
            end
        )::numeric as pe50,

        (
            case
                when sec_n_pe>=grupo_min
                    then sec_pe75

                when broad_n_pe>=grupo_min
                    then broad_pe75

                when sec_n_pe>=grupo_pequeno_min
                    then sec_pe75

                when broad_n_pe>=grupo_pequeno_min
                    then broad_pe75

                else global_pe75
            end
        )::numeric as pe75,

        (
            case
                when sec_n_pe>=grupo_min
                    then sec_pe90

                when broad_n_pe>=grupo_min
                    then broad_pe90

                when sec_n_pe>=grupo_pequeno_min
                    then sec_pe90

                when broad_n_pe>=grupo_pequeno_min
                    then broad_pe90

                else global_pe90
            end
        )::numeric as pe90,

        case
            when sec_n_pe>=grupo_min
                then sec_n_pe

            when broad_n_pe>=grupo_min
                then broad_n_pe

            when sec_n_pe>=grupo_pequeno_min
                then sec_n_pe

            when broad_n_pe>=grupo_pequeno_min
                then broad_n_pe

            else global_n_pe
        end as n_pe,

        case
            when sec_n_pb>=grupo_min
                then
                    'SETOR_ROBUSTO:'
                    ||coalesce(
                        setor,
                        'SEM_SETOR'
                    )

            when broad_n_pb>=grupo_min
                then
                    'AMPLO_ROBUSTO:'
                    ||coalesce(
                        setor_amplo,
                        'SEM_GRUPO'
                    )

            when sec_n_pb>=grupo_pequeno_min
                then
                    'SETOR_PEQUENO:'
                    ||coalesce(
                        setor,
                        'SEM_SETOR'
                    )

            when broad_n_pb>=grupo_pequeno_min
                then
                    'AMPLO_PEQUENO:'
                    ||coalesce(
                        setor_amplo,
                        'SEM_GRUPO'
                    )

            else 'ACAO_GERAL'
        end as grupo_pb,

        (
            case
                when sec_n_pb>=grupo_min
                    then sec_pb25

                when broad_n_pb>=grupo_min
                    then broad_pb25

                when sec_n_pb>=grupo_pequeno_min
                    then sec_pb25

                when broad_n_pb>=grupo_pequeno_min
                    then broad_pb25

                else global_pb25
            end
        )::numeric as pb25,

        (
            case
                when sec_n_pb>=grupo_min
                    then sec_pb50

                when broad_n_pb>=grupo_min
                    then broad_pb50

                when sec_n_pb>=grupo_pequeno_min
                    then sec_pb50

                when broad_n_pb>=grupo_pequeno_min
                    then broad_pb50

                else global_pb50
            end
        )::numeric as pb50,

        (
            case
                when sec_n_pb>=grupo_min
                    then sec_pb75

                when broad_n_pb>=grupo_min
                    then broad_pb75

                when sec_n_pb>=grupo_pequeno_min
                    then sec_pb75

                when broad_n_pb>=grupo_pequeno_min
                    then broad_pb75

                else global_pb75
            end
        )::numeric as pb75,

        (
            case
                when sec_n_pb>=grupo_min
                    then sec_pb90

                when broad_n_pb>=grupo_min
                    then broad_pb90

                when sec_n_pb>=grupo_pequeno_min
                    then sec_pb90

                when broad_n_pb>=grupo_pequeno_min
                    then broad_pb90

                else global_pb90
            end
        )::numeric as pb90,

        case
            when sec_n_pb>=grupo_min
                then sec_n_pb

            when broad_n_pb>=grupo_min
                then broad_n_pb

            when sec_n_pb>=grupo_pequeno_min
                then sec_n_pb

            when broad_n_pb>=grupo_pequeno_min
                then broad_n_pb

            else global_n_pb
        end as n_pb

    from joined j
),
premium_targets as (
    select
        t.*,

        case
            when score_premium<=25
                then pe25

            when score_premium<50
                then
                    pe25
                    +
                    (
                        pe50-pe25
                    )
                    *
                    (
                        (
                            score_premium-25
                        )
                        /25
                    )

            when score_premium<75
                then
                    pe50
                    +
                    (
                        pe75-pe50
                    )
                    *
                    (
                        (
                            score_premium-50
                        )
                        /25
                    )

            else pe75
        end as pe_base,

        case
            when score_premium<=25
                then pb25

            when score_premium<50
                then
                    pb25
                    +
                    (
                        pb50-pb25
                    )
                    *
                    (
                        (
                            score_premium-25
                        )
                        /25
                    )

            when score_premium<75
                then
                    pb50
                    +
                    (
                        pb75-pb50
                    )
                    *
                    (
                        (
                            score_premium-50
                        )
                        /25
                    )

            else pb75
        end as pb_base

    from targets t
),
weighted as (
    select
        t.*,

        case
            when setor_amplo='Financeiro'
                then peso_pe_financeiro
            else peso_pe_geral
        end as wpe,

        case
            when setor_amplo='Financeiro'
                then peso_pb_financeiro
            else peso_pb_geral
        end as wpb,

        case
            when pl is not null
             and lpa>0
                then lpa*pe25
        end as pe_vc,

        case
            when pl is not null
             and lpa>0
                then lpa*pe_base
        end as pe_vb,

        case
            when pl is not null
             and lpa>0
                then lpa*pe90
        end as pe_vo,

        case
            when pvp is not null
             and vpa>0
                then vpa*pb25
        end as pb_vc,

        case
            when pvp is not null
             and vpa>0
                then vpa*pb_base
        end as pb_vb,

        case
            when pvp is not null
             and vpa>0
                then vpa*pb90
        end as pb_vo

    from premium_targets t
),
fair as (
    select
        w.*,

        (
            coalesce(
                pe_vc*wpe,
                0
            )
            +
            coalesce(
                pb_vc*wpb,
                0
            )
        )
        /
        nullif(
            (
                case
                    when pe_vc is not null
                        then wpe
                    else 0
                end
            )
            +
            (
                case
                    when pb_vc is not null
                        then wpb
                    else 0
                end
            ),
            0
        ) as valor_c,

        (
            coalesce(
                pe_vb*wpe,
                0
            )
            +
            coalesce(
                pb_vb*wpb,
                0
            )
        )
        /
        nullif(
            (
                case
                    when pe_vb is not null
                        then wpe
                    else 0
                end
            )
            +
            (
                case
                    when pb_vb is not null
                        then wpb
                    else 0
                end
            ),
            0
        ) as valor_b,

        (
            coalesce(
                pe_vo*wpe,
                0
            )
            +
            coalesce(
                pb_vo*wpb,
                0
            )
        )
        /
        nullif(
            (
                case
                    when pe_vo is not null
                        then wpe
                    else 0
                end
            )
            +
            (
                case
                    when pb_vo is not null
                        then wpb
                    else 0
                end
            ),
            0
        ) as valor_o,

        least(
            confianca_max,

            .50

            +
            case
                when pe_vb is not null
                 and pb_vb is not null
                    then .15
                else .05
            end

            +
            case
                when qualidade_dado>=.90
                    then .08

                when qualidade_dado>=.80
                    then .04

                else 0
            end

            +
            case
                when grupo_pe like '%ROBUSTO:%'
                  or grupo_pb like '%ROBUSTO:%'
                    then .07

                when grupo_pe like '%PEQUENO:%'
                  or grupo_pb like '%PEQUENO:%'
                    then .03

                else 0
            end

            +
            case
                when premium_reliability>=.75
                    then .05

                when premium_reliability>=.40
                    then .025

                else 0
            end
        )::numeric as confianca_calc

    from weighted w
)
select
    ativo_id,
    ticker,
    data_preco as data_referencia,
    'MULTIPLOS_RELATIVOS_PE_PB_V1'::text as metodo,
    valor_c::numeric as valor_justo_conservador,
    valor_b::numeric as valor_justo_base,
    valor_o::numeric as valor_justo_otimista,
    preco::numeric as preco_referencia,
    (
        (
            valor_b-preco
        )
        /
        nullif(
            valor_b,
            0
        )
    )::numeric as margem_seguranca,
    null::numeric as score_valuation,
    confianca_calc as confianca,

    jsonb_build_object(
        'versao',
        'RELATIVO_PE_PB_V1',

        'setor',
        setor,

        'setor_amplo',
        setor_amplo,

        'grupo_pe',
        grupo_pe,

        'grupo_pb',
        grupo_pb,

        'n_pe',
        n_pe,

        'n_pb',
        n_pb,

        'lpa',
        lpa,

        'vpa',
        vpa,

        'pl_atual',
        pl,

        'pvp_atual',
        pvp,

        'score_premium',
        score_premium,

        'premium_reliability',
        premium_reliability,

        'qualidade_score',
        q_score,

        'qualidade_cobertura',
        q_cov,

        'crescimento_score',
        g_score,

        'crescimento_cobertura',
        g_cov,

        'pe_alvos',
        jsonb_build_object(
            'conservador',
            pe25,
            'base',
            pe_base,
            'otimista',
            pe90
        ),

        'pb_alvos',
        jsonb_build_object(
            'conservador',
            pb25,
            'base',
            pb_base,
            'otimista',
            pb90
        ),

        'pesos',
        jsonb_build_object(
            'pe',
            wpe,
            'pb',
            wpb
        ),

        'data_fundamentos',
        data_fundamentos,

        'data_capital',
        data_capital,

        'data_preco',
        data_preco,

        'observacao',
        'Valuation relativo por P/L e P/VP. Não é DCF e não usa a meta de retorno da carteira como peso.'
    ) as premissas,

    'CVM_DFP_ITR+B3_HIST+B3_CAPITAL'::text as fonte

from fair

where valor_b>0
  and valor_c>0
  and valor_o>0;
