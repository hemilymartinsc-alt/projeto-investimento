create or replace view investimento.vw_motor_scores_pilares_calculados_v1 as
with perfis as (
    select
        u.ativo_id,
        u.ticker,
        u.perfil
    from investimento.vw_motor_universo_v1 u
    where u.universo_investivel_v1
      and u.perfil is not null
),
esperado as (
    select
        p.ativo_id,
        p.ticker,
        p.perfil,
        c.pilar,
        sum(w.peso_no_pilar) as peso_total,
        count(*) as qtd_indicadores
    from perfis p
    join investimento.motor_pesos_indicadores w
      on w.modelo_codigo = 'MOTOR_V1'
     and w.perfil = p.perfil
     and w.ativo
    join investimento.motor_indicadores_catalogo c
      on c.codigo = w.indicador_codigo
    group by
        p.ativo_id,
        p.ticker,
        p.perfil,
        c.pilar
),
params as (
    select
        coalesce(
            max(valor_numerico) filter (
                where parametro = 'COBERTURA_MINIMA_PILAR'
            ),
            0.60
        ) as cobertura_minima
    from investimento.motor_parametros
    where modelo_codigo = 'MOTOR_V1'
),
calc as (
    select
        e.ativo_id,
        e.ticker,
        e.perfil,
        e.pilar,
        e.qtd_indicadores,
        e.peso_total,

        count(s.indicador_codigo) filter (
            where s.score is not null
        ) as qtd_disponiveis,

        sum(
            case
                when s.score is not null
                    then w.peso_no_pilar
                else 0::numeric
            end
        ) as peso_presente,

        sum(
            case
                when s.score is not null
                    then w.peso_no_pilar * s.qualidade_dado
                else 0::numeric
            end
        ) as peso_qualificado,

        sum(
            case
                when s.score is not null
                    then s.score * w.peso_no_pilar * s.qualidade_dado
                else 0::numeric
            end
        ) as soma_ponderada,

        max(s.data_referencia) as data_referencia

    from esperado e

    join investimento.motor_pesos_indicadores w
      on w.modelo_codigo = 'MOTOR_V1'
     and w.perfil = e.perfil
     and w.ativo

    join investimento.motor_indicadores_catalogo c
      on c.codigo = w.indicador_codigo
     and c.pilar = e.pilar

    left join investimento.vw_motor_scores_indicadores_calculados_v1 s
      on s.ativo_id = e.ativo_id
     and s.indicador_codigo = w.indicador_codigo

    group by
        e.ativo_id,
        e.ticker,
        e.perfil,
        e.pilar,
        e.qtd_indicadores,
        e.peso_total
)
select
    c.ativo_id,
    c.ticker,
    c.perfil,
    coalesce(
        c.data_referencia,
        current_date
    ) as data_referencia,
    'MOTOR_V1'::text as modelo_codigo,
    c.pilar,

    case
        when c.peso_qualificado > 0
            then round(
                c.soma_ponderada / c.peso_qualificado,
                2
            )
        else null::numeric
    end as score,

    case
        when c.peso_total > 0
            then least(
                1::numeric,
                round(
                    c.peso_presente / c.peso_total,
                    4
                )
            )
        else 0::numeric
    end as cobertura,

    c.qtd_indicadores,
    c.qtd_disponiveis as qtd_indicadores_disponiveis,

    case
        when c.peso_presente > 0
            then least(
                1::numeric,
                round(
                    c.peso_qualificado / c.peso_presente,
                    4
                )
            )
        else 0::numeric
    end as qualidade_media,

    case
        when c.peso_total > 0
            then least(
                1::numeric,
                round(
                    c.peso_qualificado / c.peso_total,
                    4
                )
            )
        else 0::numeric
    end as confianca_pilar,

    (
        case
            when c.peso_total > 0
                then c.peso_presente / c.peso_total
            else 0::numeric
        end
        >= p.cobertura_minima
    ) as cobertura_suficiente

from calc c
cross join params p;
