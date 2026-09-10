create or replace view investimento.vw_motor_metricas_disponiveis_v1 as
with f_latest as (
    select distinct on (f.ativo_id)
        f.id,
        f.ativo_id,
        f.data_referencia,
        f.receita_ttm,
        f.lucro_liquido_ttm,
        f.patrimonio_liquido,
        f.fluxo_caixa_livre_ttm,
        f.divida_liquida,
        f.roe,
        f.roic,
        f.margem_liquida,
        f.margem_ebitda,
        f.liquidez_corrente,
        f.divida_liquida_ebitda,
        f.payout,
        f.pl,
        f.pvp,
        f.ev_ebitda,
        f.fcf_yield,
        f.fonte_base,
        f.calculado_em
    from investimento.fundamentos_snapshot f
    join investimento.vw_motor_universo_v1 u
      on u.ativo_id = f.ativo_id
     and u.universo_investivel_v1
    order by
        f.ativo_id,
        f.data_referencia desc,
        f.calculado_em desc
),

fund_long as (
    select
        f.ativo_id,
        f.data_referencia,
        x.indicador_codigo,
        x.valor,
        'FUNDAMENTOS_SNAPSHOT'::text as fonte,
        1.0::numeric as qualidade_dado,
        2 as prioridade
    from f_latest f
    cross join lateral (
        values
            ('ROIC'::text, f.roic),
            ('ROE'::text, f.roe),
            ('MARGEM_LIQUIDA'::text, f.margem_liquida),
            ('MARGEM_EBITDA'::text, f.margem_ebitda),
            ('DIVIDA_LIQ_EBITDA'::text, f.divida_liquida_ebitda),
            ('LIQUIDEZ_CORRENTE'::text, f.liquidez_corrente),
            ('FCF_YIELD'::text, f.fcf_yield),
            (
                'PL'::text,
                case
                    when f.pl > 0 then f.pl
                    else null
                end
            ),
            (
                'PVP'::text,
                case
                    when f.pvp > 0 then f.pvp
                    else null
                end
            ),
            (
                'EV_EBITDA'::text,
                case
                    when f.ev_ebitda > 0 then f.ev_ebitda
                    else null
                end
            )
    ) x(indicador_codigo, valor)
    where x.valor is not null
),

quote_latest as (
    select distinct on (c.ativo_id)
        c.ativo_id,
        u.classe,
        c.data,
        c.fechamento,
        c.fechamento_ajustado,
        c.fonte
    from investimento.cotacoes_diarias c
    join investimento.vw_motor_universo_v1 u
      on u.ativo_id = c.ativo_id
     and u.universo_investivel_v1
    where coalesce(
        c.fechamento_ajustado,
        c.fechamento
    ) > 0
    order by
        c.ativo_id,
        c.data desc,
        c.coletado_em desc
),

dy as (
    select
        q.ativo_id,
        q.data as data_referencia,
        'DY_12M'::text as indicador_codigo,
        case
            when coalesce(
                q.fechamento_ajustado,
                q.fechamento
            ) > 0
            then
                sum(
                    case
                        when p.tipo in (
                            'DIVIDENDO',
                            'JCP'
                        )
                         and p.fonte_oficial
                         and p.status = 'CONFIRMADO'
                         and coalesce(
                             p.data_pagamento,
                             p.data_ex,
                             p.data_com
                         ) > q.data - interval '1 year'
                         and coalesce(
                             p.data_pagamento,
                             p.data_ex,
                             p.data_com
                         ) <= q.data
                        then coalesce(
                            p.valor_por_unidade,
                            0
                        )
                        else 0
                    end
                )
                / coalesce(
                    q.fechamento_ajustado,
                    q.fechamento
                )
            else null
        end as valor,
        'PROVENTOS_RECORRENTES+COTACOES'::text
            as fonte,
        0.95::numeric as qualidade_dado,
        2 as prioridade
    from quote_latest q
    left join investimento.proventos p
      on p.ativo_id = q.ativo_id

    -- Por enquanto o DY automático é calculado
    -- somente para ações. FIIs terão regra própria
    -- após a carga oficial dos seus rendimentos.
    where q.classe = 'ACAO'

    group by
        q.ativo_id,
        q.classe,
        q.data,
        q.fechamento,
        q.fechamento_ajustado
),

valuation_metric as (
    select distinct on (v.ativo_id)
        v.ativo_id,
        v.data_referencia,
        'MARGEM_SEGURANCA'::text
            as indicador_codigo,
        coalesce(
            v.margem_seguranca,
            case
                when v.valor_justo_base > 0
                 and v.preco_referencia is not null
                then
                    (
                        v.valor_justo_base
                        - v.preco_referencia
                    )
                    / v.valor_justo_base
                else null
            end
        ) as valor,
        'VALUATION:'::text || v.metodo
            as fonte,
        v.confianca
            as qualidade_dado,
        1 as prioridade
    from investimento.motor_valuations v
    join investimento.vw_motor_universo_v1 u
      on u.ativo_id = v.ativo_id
     and u.universo_investivel_v1
    where coalesce(
        v.margem_seguranca,
        case
            when v.valor_justo_base > 0
             and v.preco_referencia is not null
            then
                (
                    v.valor_justo_base
                    - v.preco_referencia
                )
                / v.valor_justo_base
            else null
        end
    ) is not null
    order by
        v.ativo_id,
        v.data_referencia desc,
        v.confianca desc,
        v.calculado_em desc
),

observadas as (
    select
        m.ativo_id,
        m.data_referencia,
        m.indicador_codigo,
        m.valor,
        m.fonte,
        m.qualidade_dado,
        1 as prioridade
    from investimento.motor_metricas_observadas m
    join investimento.vw_motor_universo_v1 u
      on u.ativo_id = m.ativo_id
     and u.universo_investivel_v1
    where m.valor is not null
),

todas as (
    select *
    from observadas

    union all

    select *
    from fund_long

    union all

    select *
    from dy
    where valor is not null

    union all

    select *
    from valuation_metric
),

ranked as (
    select
        t.ativo_id,
        t.data_referencia,
        t.indicador_codigo,
        t.valor,
        t.fonte,
        t.qualidade_dado,
        t.prioridade,
        row_number() over (
            partition by
                t.ativo_id,
                t.indicador_codigo
            order by
                t.data_referencia desc,
                t.prioridade
        ) as rn
    from todas t
)

select
    r.ativo_id,
    u.ticker,
    u.classe,
    u.setor,
    u.segmento,
    u.perfil,
    r.data_referencia,
    r.indicador_codigo,
    r.valor,
    r.fonte,
    r.qualidade_dado
from ranked r
join investimento.vw_motor_universo_v1 u
  on u.ativo_id = r.ativo_id
where r.rn = 1;
