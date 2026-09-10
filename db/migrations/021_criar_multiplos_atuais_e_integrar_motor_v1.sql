insert into investimento.motor_parametros (
    modelo_codigo,
    parametro,
    valor_numerico,
    valor_texto,
    descricao,
    atualizado_em
)
values
    ('MOTOR_V1','VALUATION_PESO_PE_GERAL',0.70,null,'Peso do método P/L no valuation relativo de ações não financeiras.',now()),
    ('MOTOR_V1','VALUATION_PESO_PB_GERAL',0.30,null,'Peso do método P/VP no valuation relativo de ações não financeiras.',now()),
    ('MOTOR_V1','VALUATION_PESO_PE_FINANCEIRO',0.40,null,'Peso do método P/L no valuation relativo de instituições financeiras.',now()),
    ('MOTOR_V1','VALUATION_PESO_PB_FINANCEIRO',0.60,null,'Peso do método P/VP no valuation relativo de instituições financeiras.',now()),
    ('MOTOR_V1','VALUATION_PRECO_MAX_DIAS',30,null,'Defasagem máxima, em dias, da cotação usada no valuation V1.',now()),
    ('MOTOR_V1','VALUATION_FUNDAMENTOS_MAX_DIAS',450,null,'Defasagem máxima, em dias, do snapshot fundamental usado no valuation V1.',now())
on conflict (modelo_codigo, parametro)
do update set
    valor_numerico = excluded.valor_numerico,
    valor_texto = excluded.valor_texto,
    descricao = excluded.descricao,
    atualizado_em = now();

create or replace view investimento.vw_multiplos_atuais_v1 as
with max_quote as (
    select max(data) as data_maxima
    from investimento.cotacoes_diarias
),
setor_map as (
    select
        ltrim(codigo_cvm, '0') as codigo_cvm_norm,
        max(classificacao_setorial) filter (
            where classificacao_setorial is not null
        ) as classificacao_setorial
    from investimento.b3_empresas_listadas
    where codigo_cvm is not null
    group by ltrim(codigo_cvm, '0')
),
f_latest as (
    select distinct on (f.ativo_id)
        f.*
    from investimento.fundamentos_snapshot f
    join investimento.vw_motor_universo_v1 u
      on u.ativo_id = f.ativo_id
     and u.classe = 'ACAO'
     and u.universo_investivel_v1
    order by
        f.ativo_id,
        f.data_referencia desc,
        f.calculado_em desc
),
q_latest as (
    select distinct on (q.ativo_id)
        q.ativo_id,
        q.data,
        coalesce(
            q.fechamento_ajustado,
            q.fechamento
        ) as preco,
        q.volume,
        q.fonte
    from investimento.cotacoes_diarias q
    join investimento.vw_motor_universo_v1 u
      on u.ativo_id = q.ativo_id
     and u.classe = 'ACAO'
     and u.universo_investivel_v1
    where coalesce(
        q.fechamento_ajustado,
        q.fechamento
    ) > 0
    order by
        q.ativo_id,
        q.data desc,
        q.coletado_em desc
),
cap_latest as (
    select distinct on (v.ativo_id)
        v.ativo_id,
        ltrim(v.codigo_cvm,'0') as codigo_cvm,
        c.data_referencia,
        c.qt_acao_total_circulacao,
        c.fator_escala_aplicado,
        c.divergencia_relativa_b3,
        c.documento
    from investimento.vw_acoes_validacao_oficial_atual v
    join investimento.composicao_capital_cvm c
      on ltrim(c.codigo_cvm,'0')
         =
         ltrim(v.codigo_cvm,'0')
    where c.validado_b3 = true
      and c.qt_acao_total_circulacao > 0
    order by
        v.ativo_id,
        c.data_referencia desc,
        case
            when c.documento='ITR' then 0
            else 1
        end,
        c.coletado_em desc
),
base as (
    select
        u.ativo_id,
        u.ticker,
        u.classe,
        u.setor,
        u.segmento,
        u.perfil,
        a.subclasse,
        c.codigo_cvm,
        nullif(
            split_part(
                coalesce(
                    sm.classificacao_setorial,
                    ''
                ),
                ' / ',
                1
            ),
            ''
        ) as setor_amplo,
        q.data as data_preco,
        q.preco,
        q.volume,
        f.data_referencia as data_fundamentos,
        c.data_referencia as data_capital,
        c.qt_acao_total_circulacao as acoes_circulacao,
        c.fator_escala_aplicado,
        c.divergencia_relativa_b3,
        f.lucro_liquido_ttm,
        f.patrimonio_liquido,
        f.fluxo_caixa_livre_ttm,
        mq.data_maxima,
        case
            when q.data is null
              or f.data_referencia is null
              or c.qt_acao_total_circulacao is null
              or c.qt_acao_total_circulacao <= 0
              or a.subclasse='UNIT'
                then false

            when q.data
                 < mq.data_maxima
                   - coalesce(
                       (
                           select valor_numerico::integer
                           from investimento.motor_parametros
                           where modelo_codigo='MOTOR_V1'
                             and parametro='VALUATION_PRECO_MAX_DIAS'
                       ),
                       30
                   )
                then false

            when f.data_referencia
                 < mq.data_maxima
                   - coalesce(
                       (
                           select valor_numerico::integer
                           from investimento.motor_parametros
                           where modelo_codigo='MOTOR_V1'
                             and parametro='VALUATION_FUNDAMENTOS_MAX_DIAS'
                       ),
                       450
                   )
                then false

            else true
        end as elegivel_multiplos,

        least(
            case
                when mq.data_maxima-q.data <= 5 then 0.98
                when mq.data_maxima-q.data <= 15 then 0.90
                when mq.data_maxima-q.data <= 30 then 0.80
                else 0.40
            end,
            case
                when mq.data_maxima-f.data_referencia <= 120 then 0.95
                when mq.data_maxima-f.data_referencia <= 270 then 0.85
                when mq.data_maxima-f.data_referencia <= 450 then 0.70
                else 0.40
            end
        )::numeric as qualidade_dado
    from investimento.vw_motor_universo_v1 u
    join investimento.ativos a
      on a.id=u.ativo_id
    join f_latest f
      on f.ativo_id=u.ativo_id
    join q_latest q
      on q.ativo_id=u.ativo_id
    join cap_latest c
      on c.ativo_id=u.ativo_id
    left join setor_map sm
      on sm.codigo_cvm_norm=c.codigo_cvm
    cross join max_quote mq
    where u.classe='ACAO'
      and u.universo_investivel_v1
)
select
    ativo_id,
    ticker,
    classe,
    setor,
    segmento,
    perfil,
    subclasse,
    codigo_cvm,
    setor_amplo,
    data_preco,
    preco,
    volume,
    data_fundamentos,
    data_capital,
    acoes_circulacao,
    fator_escala_aplicado,
    divergencia_relativa_b3,
    elegivel_multiplos,
    qualidade_dado,

    case
        when subclasse<>'UNIT'
         and acoes_circulacao>0
         and lucro_liquido_ttm is not null
            then lucro_liquido_ttm/acoes_circulacao
    end as lpa,

    case
        when subclasse<>'UNIT'
         and acoes_circulacao>0
         and patrimonio_liquido is not null
            then patrimonio_liquido/acoes_circulacao
    end as vpa,

    case
        when elegivel_multiplos
         and lucro_liquido_ttm>0
            then preco*acoes_circulacao/lucro_liquido_ttm
    end as pl,

    case
        when elegivel_multiplos
         and patrimonio_liquido>0
            then preco*acoes_circulacao/patrimonio_liquido
    end as pvp,

    case
        when elegivel_multiplos
         and fluxo_caixa_livre_ttm is not null
         and preco>0
         and acoes_circulacao>0
            then fluxo_caixa_livre_ttm/(preco*acoes_circulacao)
    end as fcf_yield

from base;


create or replace view investimento.vw_motor_metricas_disponiveis_v1 as
with f_latest as (
    select distinct on (f.ativo_id)
        f.*
    from investimento.fundamentos_snapshot f
    join investimento.vw_motor_universo_v1 u
      on u.ativo_id=f.ativo_id
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
        3 as prioridade
    from f_latest f
    cross join lateral (
        values
            ('ROIC'::text,f.roic),
            ('ROE'::text,f.roe),
            ('MARGEM_LIQUIDA'::text,f.margem_liquida),
            ('MARGEM_EBITDA'::text,f.margem_ebitda),
            ('DIVIDA_LIQ_EBITDA'::text,f.divida_liquida_ebitda),
            ('LIQUIDEZ_CORRENTE'::text,f.liquidez_corrente),
            ('FCF_YIELD'::text,f.fcf_yield),
            (
                'EV_EBITDA'::text,
                case
                    when f.ev_ebitda>0 then f.ev_ebitda
                end
            )
    ) x(indicador_codigo,valor)
    where x.valor is not null
),
multiplos_long as (
    select
        m.ativo_id,
        m.data_preco as data_referencia,
        x.indicador_codigo,
        x.valor,
        'MULTIPLOS_ATUAIS_V1'::text as fonte,
        m.qualidade_dado,
        2 as prioridade
    from investimento.vw_multiplos_atuais_v1 m
    cross join lateral (
        values
            ('PL'::text,m.pl),
            ('PVP'::text,m.pvp),
            ('FCF_YIELD'::text,m.fcf_yield)
    ) x(indicador_codigo,valor)
    where m.elegivel_multiplos
      and x.valor is not null
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
      on u.ativo_id=c.ativo_id
     and u.universo_investivel_v1
    where coalesce(
        c.fechamento_ajustado,
        c.fechamento
    )>0
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
            )>0
                then
                    sum(
                        case
                            when p.tipo in ('DIVIDENDO','JCP')
                             and p.fonte_oficial
                             and p.status='CONFIRMADO'
                             and coalesce(
                                   p.data_pagamento,
                                   p.data_ex,
                                   p.data_com
                                 )
                                 > q.data-interval '1 year'
                             and coalesce(
                                   p.data_pagamento,
                                   p.data_ex,
                                   p.data_com
                                 )
                                 <= q.data
                                then coalesce(
                                    p.valor_por_unidade,
                                    0
                                )
                            else 0
                        end
                    )
                    /coalesce(
                        q.fechamento_ajustado,
                        q.fechamento
                    )
        end as valor,
        'PROVENTOS_RECORRENTES+COTACOES'::text as fonte,
        0.95::numeric as qualidade_dado,
        2 as prioridade
    from quote_latest q
    left join investimento.proventos p
      on p.ativo_id=q.ativo_id
    where q.classe='ACAO'
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
        'MARGEM_SEGURANCA'::text as indicador_codigo,
        coalesce(
            v.margem_seguranca,
            case
                when v.valor_justo_base>0
                 and v.preco_referencia is not null
                    then
                        (
                            v.valor_justo_base
                            - v.preco_referencia
                        )
                        / v.valor_justo_base
            end
        ) as valor,
        'VALUATION:'::text||v.metodo as fonte,
        v.confianca as qualidade_dado,
        1 as prioridade
    from investimento.motor_valuations v
    join investimento.vw_motor_universo_v1 u
      on u.ativo_id=v.ativo_id
     and u.universo_investivel_v1
    cross join (
        select max(data) as data_maxima
        from investimento.cotacoes_diarias
    ) mq
    where v.data_referencia>=mq.data_maxima-30
      and coalesce(
            v.margem_seguranca,
            case
                when v.valor_justo_base>0
                 and v.preco_referencia is not null
                    then
                        (
                            v.valor_justo_base
                            - v.preco_referencia
                        )
                        / v.valor_justo_base
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
      on u.ativo_id=m.ativo_id
     and u.universo_investivel_v1
    where m.valor is not null
),
todas as (
    select * from observadas
    union all
    select * from fund_long
    union all
    select * from multiplos_long
    union all
    select * from dy
    where valor is not null
    union all
    select * from valuation_metric
),
ranked as (
    select
        t.*,
        row_number() over(
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
  on u.ativo_id=r.ativo_id
where r.rn=1;
