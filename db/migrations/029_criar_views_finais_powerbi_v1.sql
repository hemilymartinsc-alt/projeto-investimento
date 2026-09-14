create or replace view investimento.vw_powerbi_oportunidades_atual
with (security_invoker = true)
as
with cotacao_atual as (
    select distinct on (c.ativo_id)
        c.ativo_id,
        c.data as data_cotacao,
        coalesce(c.fechamento_ajustado, c.fechamento) as preco_atual,
        c.volume
    from investimento.cotacoes_diarias c
    order by c.ativo_id, c.data desc, c.id desc
),
resultado_atual as (
    select distinct on (r.ativo_id)
        r.ativo_id,
        r.data_referencia,
        r.modelo_codigo,
        r.perfil,
        r.score_ativo,
        r.score_oportunidade,
        r.score_confianca,
        r.decisao_ativo,
        r.motivo_principal,
        r.bloqueado,
        r.explicacoes,
        r.calculado_em
    from investimento.motor_resultados r
    order by
        r.ativo_id,
        r.data_referencia desc,
        r.calculado_em desc
),
valuation_atual as (
    select distinct on (v.ativo_id)
        v.ativo_id,
        v.data_referencia as data_valuation,
        v.metodo,
        v.valor_justo_conservador,
        v.valor_justo_base,
        v.valor_justo_otimista,
        v.preco_referencia,
        v.margem_seguranca,
        v.confianca as confianca_valuation
    from investimento.motor_valuations v
    order by
        v.ativo_id,
        v.data_referencia desc,
        v.calculado_em desc,
        v.id desc
),
proventos_12m as (
    select
        p.ativo_id,
        sum(p.valor_por_unidade)
            filter (where p.tipo = 'DIVIDENDO') as dividendo_12m,
        sum(p.valor_por_unidade)
            filter (where p.tipo = 'JCP') as jcp_12m,
        sum(p.valor_por_unidade)
            filter (where p.tipo = 'RENDIMENTO') as rendimento_12m,
        sum(p.valor_por_unidade) as proventos_12m
    from investimento.proventos p
    where coalesce(p.data_ex, p.data_com, p.data_pagamento)
          between current_date - 365 and current_date
    group by p.ativo_id
),
base as (
    select
        b.carteira_id,
        b.ativo_id,
        b.ticker,
        a.nome,
        a.nome_pregao,
        b.classe,
        a.subclasse,
        a.setor,
        a.segmento,
        b.data_referencia,
        q.data_cotacao,
        q.preco_atual,
        q.volume,
        b.score_ativo,
        b.score_oportunidade,
        b.score_confianca,
        b.decisao_ativo,
        b.quantidade_atual,
        b.decisao_carteira_base,
        b.prioridade_base,
        r.motivo_principal,
        r.bloqueado,
        r.perfil,
        v.data_valuation,
        v.metodo as metodo_valuation,
        v.valor_justo_conservador,
        v.valor_justo_base,
        v.valor_justo_otimista,
        v.margem_seguranca,
        v.confianca_valuation,
        coalesce(p.dividendo_12m, 0) as dividendo_12m,
        coalesce(p.jcp_12m, 0) as jcp_12m,
        coalesce(p.rendimento_12m, 0) as rendimento_12m,
        coalesce(p.proventos_12m, 0) as proventos_12m,
        coalesce(p.proventos_12m, 0)
            / nullif(q.preco_atual, 0) as dy_12m
    from investimento.vw_motor_recomendacao_base_carteira_v1 b
    join investimento.ativos a
      on a.id = b.ativo_id
    left join resultado_atual r
      on r.ativo_id = b.ativo_id
     and r.data_referencia = b.data_referencia
    left join cotacao_atual q
      on q.ativo_id = b.ativo_id
    left join valuation_atual v
      on v.ativo_id = b.ativo_id
    left join proventos_12m p
      on p.ativo_id = b.ativo_id
)
select
    base.*,
    case
        when decisao_carteira_base = 'COMPRAR'
        then row_number() over (
            partition by carteira_id
            order by
                (decisao_carteira_base = 'COMPRAR') desc,
                prioridade_base desc nulls last,
                score_oportunidade desc nulls last,
                score_confianca desc nulls last,
                ticker
        )
    end as rank_oportunidade,
    case
        when margem_seguranca is not null
        then row_number() over (
            partition by carteira_id
            order by
                margem_seguranca desc nulls last,
                score_confianca desc nulls last,
                ticker
        )
    end as rank_maior_desconto
from base;


create or replace view investimento.vw_powerbi_carteira_posicoes
with (security_invoker = true)
as
with recursive movimentacoes_ordenadas as (
    select
        m.id,
        m.carteira_id,
        m.ativo_id,
        m.data,
        m.tipo,
        m.quantidade,
        m.preco_unitario,
        coalesce(m.taxas, 0) as taxas,
        coalesce(m.impostos, 0) as impostos,
        m.origem_lancamento,
        row_number() over (
            partition by m.carteira_id, m.ativo_id
            order by m.data, m.id
        ) as ordem
    from investimento.movimentacoes_carteira m
),
evolucao as (
    select
        m.carteira_id,
        m.ativo_id,
        m.ordem,
        m.data as ultima_movimentacao,
        case
            when m.tipo = 'COMPRA'
            then m.quantidade
            else 0::numeric
        end as quantidade_saldo,
        case
            when m.tipo = 'COMPRA'
            then (
                m.quantidade * m.preco_unitario
                + m.taxas
                + m.impostos
            )
            else 0::numeric
        end as custo_saldo
    from movimentacoes_ordenadas m
    where m.ordem = 1

    union all

    select
        e.carteira_id,
        e.ativo_id,
        m.ordem,
        m.data,
        case
            when m.tipo = 'COMPRA'
            then e.quantidade_saldo + m.quantidade
            else greatest(
                0::numeric,
                e.quantidade_saldo - m.quantidade
            )
        end,
        case
            when m.tipo = 'COMPRA'
            then e.custo_saldo
                 + (
                     m.quantidade * m.preco_unitario
                     + m.taxas
                     + m.impostos
                 )
            when e.quantidade_saldo <= 0
              or m.quantidade >= e.quantidade_saldo
            then 0::numeric
            else e.custo_saldo
                 * (
                     (e.quantidade_saldo - m.quantidade)
                     / e.quantidade_saldo
                 )
        end
    from evolucao e
    join movimentacoes_ordenadas m
      on m.carteira_id = e.carteira_id
     and m.ativo_id = e.ativo_id
     and m.ordem = e.ordem + 1
),
estado_atual as (
    select distinct on (e.carteira_id, e.ativo_id)
        e.carteira_id,
        e.ativo_id,
        e.quantidade_saldo,
        e.custo_saldo,
        e.ultima_movimentacao
    from evolucao e
    order by e.carteira_id, e.ativo_id, e.ordem desc
),
cotacao_atual as (
    select distinct on (c.ativo_id)
        c.ativo_id,
        c.data as data_cotacao,
        coalesce(c.fechamento_ajustado, c.fechamento) as preco_atual
    from investimento.cotacoes_diarias c
    order by c.ativo_id, c.data desc, c.id desc
),
resultado_atual as (
    select distinct on (r.ativo_id)
        r.ativo_id,
        r.data_referencia as data_motor,
        r.score_ativo,
        r.score_oportunidade,
        r.score_confianca,
        r.decisao_ativo,
        r.motivo_principal
    from investimento.motor_resultados r
    order by
        r.ativo_id,
        r.data_referencia desc,
        r.calculado_em desc
),
valuation_atual as (
    select distinct on (v.ativo_id)
        v.ativo_id,
        v.data_referencia as data_valuation,
        v.valor_justo_conservador,
        v.valor_justo_base,
        v.valor_justo_otimista,
        v.margem_seguranca,
        v.confianca as confianca_valuation
    from investimento.motor_valuations v
    order by
        v.ativo_id,
        v.data_referencia desc,
        v.calculado_em desc,
        v.id desc
),
proventos_12m as (
    select
        p.ativo_id,
        sum(p.valor_por_unidade)
            filter (where p.tipo = 'DIVIDENDO') as dividendo_12m_unidade,
        sum(p.valor_por_unidade)
            filter (where p.tipo = 'JCP') as jcp_12m_unidade,
        sum(p.valor_por_unidade)
            filter (where p.tipo = 'RENDIMENTO') as rendimento_12m_unidade,
        sum(p.valor_por_unidade) as proventos_12m_unidade
    from investimento.proventos p
    where coalesce(p.data_ex, p.data_com, p.data_pagamento)
          between current_date - 365 and current_date
    group by p.ativo_id
)
select
    c.usuario_id,
    c.id as carteira_id,
    c.nome as carteira_nome,
    a.id as ativo_id,
    a.ticker,
    a.nome,
    a.nome_pregao,
    a.classe,
    a.subclasse,
    a.setor,
    a.segmento,
    e.quantidade_saldo as quantidade,
    e.custo_saldo as custo_total_atual,
    e.custo_saldo
        / nullif(e.quantidade_saldo, 0) as preco_medio,
    e.ultima_movimentacao,
    q.data_cotacao,
    q.preco_atual,
    e.quantidade_saldo
        * q.preco_atual as valor_mercado,
    e.quantidade_saldo
        * q.preco_atual
        - e.custo_saldo as resultado_nao_realizado,
    (
        e.quantidade_saldo * q.preco_atual
        - e.custo_saldo
    ) / nullif(e.custo_saldo, 0)
        as rentabilidade_nao_realizada,
    r.data_motor,
    r.score_ativo,
    r.score_oportunidade,
    r.score_confianca,
    r.decisao_ativo,
    r.motivo_principal,
    v.data_valuation,
    v.valor_justo_conservador,
    v.valor_justo_base,
    v.valor_justo_otimista,
    v.margem_seguranca,
    v.confianca_valuation,
    coalesce(p.dividendo_12m_unidade, 0)
        as dividendo_12m_unidade,
    coalesce(p.jcp_12m_unidade, 0)
        as jcp_12m_unidade,
    coalesce(p.rendimento_12m_unidade, 0)
        as rendimento_12m_unidade,
    coalesce(p.proventos_12m_unidade, 0)
        as proventos_12m_unidade,
    e.quantidade_saldo
        * coalesce(p.proventos_12m_unidade, 0)
        as proventos_12m_estimados_posicao
from estado_atual e
join investimento.carteiras c
  on c.id = e.carteira_id
 and c.ativa = true
join investimento.ativos a
  on a.id = e.ativo_id
left join cotacao_atual q
  on q.ativo_id = e.ativo_id
left join resultado_atual r
  on r.ativo_id = e.ativo_id
left join valuation_atual v
  on v.ativo_id = e.ativo_id
left join proventos_12m p
  on p.ativo_id = e.ativo_id
where e.quantidade_saldo > 0;


create or replace view investimento.vw_powerbi_carteira_resumo
with (security_invoker = true)
as
with posicoes as (
    select
        p.carteira_id,
        count(*) as quantidade_ativos,
        sum(p.custo_total_atual) as custo_total_posicoes,
        sum(p.valor_mercado) as valor_mercado_posicoes,
        sum(p.resultado_nao_realizado) as resultado_nao_realizado,
        sum(p.proventos_12m_estimados_posicao)
            as proventos_12m_estimados
    from investimento.vw_powerbi_carteira_posicoes p
    group by p.carteira_id
),
proventos_recebidos as (
    select
        f.carteira_id,
        sum(f.valor) as proventos_recebidos_total
    from investimento.carteira_fluxos_caixa f
    where f.tipo = 'PROVENTO_RECEBIDO'
    group by f.carteira_id
)
select
    c.usuario_id,
    c.id as carteira_id,
    c.nome as carteira_nome,
    c.moeda_base,
    c.principal,
    case
        when coalesce(p.quantidade_ativos, 0) = 0
        then 'SEM_MOVIMENTACOES'
        else 'ATIVA_COM_POSICOES'
    end as status_carteira,
    coalesce(p.quantidade_ativos, 0) as quantidade_ativos,
    coalesce(p.custo_total_posicoes, 0) as custo_total_posicoes,
    coalesce(p.valor_mercado_posicoes, 0) as valor_mercado_posicoes,
    coalesce(p.resultado_nao_realizado, 0) as resultado_nao_realizado,
    coalesce(p.resultado_nao_realizado, 0)
        / nullif(p.custo_total_posicoes, 0)
        as rentabilidade_nao_realizada,
    coalesce(cx.caixa_residual, 0) as caixa_residual,
    coalesce(p.valor_mercado_posicoes, 0)
        + coalesce(cx.caixa_residual, 0)
        as patrimonio_total_estimado,
    coalesce(pr.proventos_recebidos_total, 0)
        as proventos_recebidos_total,
    coalesce(p.proventos_12m_estimados, 0)
        as proventos_12m_estimados_posicao,
    coalesce(cfg.aporte_novo_informado, 0)
        as proximo_aporte_informado,
    coalesce(cfg.limite_extraordinario, 0)
        as limite_extraordinario,
    greatest(
        0::numeric,
        coalesce(cx.caixa_residual, 0)
        + coalesce(cfg.aporte_novo_informado, 0)
    ) as capital_disponivel_normal,
    o.meta_retorno_real_liquido_anual,
    o.horizonte_anos,
    o.observacoes as observacoes_objetivo
from investimento.carteiras c
left join posicoes p
  on p.carteira_id = c.id
left join investimento.vw_carteira_caixa cx
  on cx.carteira_id = c.id
left join investimento.carteira_configuracao_aporte cfg
  on cfg.carteira_id = c.id
left join investimento.carteira_objetivos o
  on o.carteira_id = c.id
left join proventos_recebidos pr
  on pr.carteira_id = c.id
where c.ativa = true;


create or replace view investimento.vw_powerbi_status_atualizacao
with (security_invoker = true)
as
select
    'INSTRUMENTOS_B3'::text as fonte,
    max(s.data_referencia) as data_referencia,
    max(s.coletado_em) as atualizado_em,
    count(*) filter (
        where s.data_referencia = (
            select max(x.data_referencia)
            from investimento.b3_instrumentos_snapshot x
        )
    )::bigint as registros_snapshot_atual
from investimento.b3_instrumentos_snapshot s

union all

select
    'FUNDOS_CVM',
    max(c.data_referencia),
    max(c.coletado_em),
    count(*)::bigint
from investimento.cvm_fundos_cadastro c

union all

select
    'PROGRAMAS_BDR_B3',
    max(b.data_referencia),
    max(b.coletado_em),
    count(*)::bigint
from investimento.b3_bdr_programas_snapshot b

union all

select
    'COTACOES_DIARIAS',
    max(c.data),
    max(c.coletado_em),
    count(*) filter (
        where c.data = (
            select max(x.data)
            from investimento.cotacoes_diarias x
        )
    )::bigint
from investimento.cotacoes_diarias c

union all

select
    'PROVENTOS',
    max(p.coletado_em)::date,
    max(p.coletado_em),
    count(*)::bigint
from investimento.proventos p

union all

select
    'MOTOR_RESULTADOS',
    max(r.data_referencia),
    max(r.calculado_em),
    count(*) filter (
        where r.data_referencia = (
            select max(x.data_referencia)
            from investimento.motor_resultados x
        )
    )::bigint
from investimento.motor_resultados r

union all

select
    'VALUATION',
    max(v.data_referencia),
    max(v.calculado_em),
    count(*) filter (
        where v.data_referencia = (
            select max(x.data_referencia)
            from investimento.motor_valuations x
        )
    )::bigint
from investimento.motor_valuations v;
