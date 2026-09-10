create or replace view investimento.vw_motor_resultados_calculados_v1 as
with p as (
    select
        s.ativo_id,
        s.ticker,
        s.perfil,
        max(s.data_referencia) as data_referencia,

        sum(
            case
                when w.peso_score_ativo > 0
                 and s.score is not null
                    then s.score * w.peso_score_ativo
                else 0::numeric
            end
        ) as soma_ativo,

        sum(
            case
                when w.peso_score_ativo > 0
                 and s.score is not null
                    then w.peso_score_ativo
                else 0::numeric
            end
        ) as peso_ativo,

        sum(
            case
                when w.peso_score_oportunidade > 0
                 and s.score is not null
                    then s.score * w.peso_score_oportunidade
                else 0::numeric
            end
        ) as soma_oportunidade,

        sum(
            case
                when w.peso_score_oportunidade > 0
                 and s.score is not null
                    then w.peso_score_oportunidade
                else 0::numeric
            end
        ) as peso_oportunidade,

        sum(
            s.confianca_pilar
            * w.peso_score_oportunidade
        ) as confianca_ponderada,

        sum(
            w.peso_score_oportunidade
        ) as peso_conf

    from investimento.vw_motor_scores_pilares_calculados_v1 s

    join investimento.motor_pesos_pilares w
      on w.modelo_codigo = 'MOTOR_V1'
     and w.perfil = s.perfil
     and w.pilar = s.pilar
     and w.ativo

    group by
        s.ativo_id,
        s.ticker,
        s.perfil
),
params as (
    select
        max(valor_numerico) filter (
            where parametro = 'CONFIANCA_MINIMA_COMPRA'
        ) as conf_min,

        max(valor_numerico) filter (
            where parametro = 'SCORE_ATIVO_MIN_COMPRA'
        ) as ativo_compra,

        max(valor_numerico) filter (
            where parametro = 'SCORE_OPORTUNIDADE_MIN_COMPRA'
        ) as oportunidade_compra,

        max(valor_numerico) filter (
            where parametro = 'SCORE_ATIVO_MIN_MANTER'
        ) as ativo_manter,

        max(valor_numerico) filter (
            where parametro = 'SCORE_ATIVO_MAX_NAO_COMPRAR'
        ) as ativo_nao

    from investimento.motor_parametros
    where modelo_codigo = 'MOTOR_V1'
),
base as (
    select
        p.*,

        case
            when p.peso_ativo > 0
                then round(
                    p.soma_ativo / p.peso_ativo,
                    2
                )
            else null::numeric
        end as score_ativo,

        case
            when p.peso_oportunidade > 0
                then round(
                    p.soma_oportunidade / p.peso_oportunidade,
                    2
                )
            else null::numeric
        end as score_oportunidade,

        case
            when p.peso_conf > 0
                then round(
                    100::numeric
                    * p.confianca_ponderada
                    / p.peso_conf,
                    2
                )
            else 0::numeric
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
        b.*,
        pr.conf_min,
        pr.ativo_compra,
        pr.oportunidade_compra,
        pr.ativo_manter,
        pr.ativo_nao,

        case
            when b.bloqueado
                then 'NAO_COMPRAR'::text

            when b.score_confianca < pr.conf_min
                then 'AGUARDAR'::text

            when b.score_ativo is null
              or b.score_oportunidade is null
                then 'AGUARDAR'::text

            when b.score_ativo < pr.ativo_nao
                then 'NAO_COMPRAR'::text

            when b.score_ativo >= pr.ativo_compra
             and b.score_oportunidade >= pr.oportunidade_compra
                then 'COMPRAR'::text

            when b.score_ativo >= pr.ativo_manter
             and b.score_oportunidade >= 55::numeric
                then 'MANTER'::text

            else 'AGUARDAR'::text
        end as decisao_ativo

    from base b
    cross join params pr
)
select
    ativo_id,
    ticker,
    perfil,
    coalesce(
        data_referencia,
        current_date
    ) as data_referencia,
    'MOTOR_V1'::text as modelo_codigo,
    score_ativo,
    score_oportunidade,
    score_confianca,
    bloqueado,
    decisao_ativo,

    case
        when bloqueado
            then 'Compra bloqueada por red flag ativa.'::text

        when score_confianca < conf_min
            then 'Confiança agregada insuficiente para uma decisão de compra confiável.'::text

        when decisao_ativo = 'COMPRAR'
            then 'Qualidade estrutural e oportunidade superam os limites configurados do modelo.'::text

        when decisao_ativo = 'MANTER'
            then 'Qualidade suficiente, mas a oportunidade não justifica priorização máxima de novo capital.'::text

        when decisao_ativo = 'NAO_COMPRAR'
            then 'Qualidade estrutural abaixo do limite ou risco impeditivo.'::text

        else
            'Relação atual entre qualidade, valuation, risco e confiança recomenda aguardar.'::text
    end as motivo_principal

from final;
