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

    group by
        codigo_cvm
),

dre_base as (
    select
        d.codigo_cvm,
        d.data_referencia,
        d.fonte,

        max(d.valor) filter (
            where d.codigo_conta = '3.01'
        ) as receita,

        max(d.descricao_conta) filter (
            where d.codigo_conta = '3.01'
        ) as receita_descricao,

        max(d.valor) filter (
            where d.codigo_conta = '3.11'
        ) as lucro_311,

        max(d.valor) filter (
            where d.codigo_conta = '3.09'
        ) as lucro_309,

        max(d.valor) filter (
            where d.codigo_conta = '3.05'
              and (
                  lower(d.descricao_conta)
                      like '%resultado antes do resultado financeiro%'
                  or lower(d.descricao_conta)
                      like '%resultado operacional%'
              )
        ) as ebit_305,

        max(d.valor) filter (
            where d.codigo_conta = '3.07'
              and lower(d.descricao_conta)
                  like '%resultado antes do resultado financeiro%'
        ) as ebit_307,

        max(d.valor) filter (
            where d.codigo_conta = '3.07'
              and (
                  lower(d.descricao_conta)
                      like '%resultado antes dos tributos sobre o lucro%'
                  or lower(d.descricao_conta)
                      like '%resultado antes tributação%'
                  or lower(d.descricao_conta)
                      like '%resultado antes tributacao%'
              )
        ) as resultado_antes_tributos,

        max(d.valor) filter (
            where d.codigo_conta = '3.08'
              and (
                  lower(d.descricao_conta)
                      like '%imposto de renda e contribuição social%'
                  or lower(d.descricao_conta)
                      like '%imposto de renda e contribuicao social%'
                  or lower(d.descricao_conta)
                      like '%provisão para ir e contribuição social%'
                  or lower(d.descricao_conta)
                      like '%provisao para ir e contribuicao social%'
              )
        ) as imposto_renda_cs,

        max(d.descricao_conta) filter (
            where d.codigo_conta = '3.09'
        ) as lucro_309_descricao

    from investimento.demonstracoes_financeiras d

    where d.fonte in (
        'CVM_DFP',
        'CVM_ITR'
    )
      and d.tipo_demonstracao = 'DRE'
      and d.codigo_conta in (
          '3.01',
          '3.05',
          '3.07',
          '3.08',
          '3.09',
          '3.11'
      )
      and d.codigo_cvm is not null

    group by
        d.codigo_cvm,
        d.data_referencia,
        d.fonte
),

dre_raw as (
    select
        d.codigo_cvm,
        d.data_referencia,
        d.fonte,
        d.receita,
        d.receita_descricao,

        case
            when d.lucro_311 is not null
                then d.lucro_311

            when (
                lower(
                    coalesce(
                        s.classificacao_setorial,
                        ''
                    )
                )
                like 'financeiro / intermediários financeiros /%'

                or lower(
                    coalesce(
                        s.classificacao_setorial,
                        ''
                    )
                )
                like 'financeiro / intermediarios financeiros /%'

                or lower(
                    coalesce(
                        d.receita_descricao,
                        ''
                    )
                )
                like '%intermedia%financeir%'
            )
            then d.lucro_309

            else null
        end as lucro,

        coalesce(
            d.ebit_305,
            d.ebit_307
        ) as ebit,

        d.resultado_antes_tributos,
        d.imposto_renda_cs

    from dre_base d

    left join setores s
      on s.codigo_cvm = d.codigo_cvm
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
        lucro,
        ebit,
        resultado_antes_tributos,
        imposto_renda_cs

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

dva_base as (
    select
        d.codigo_cvm,
        d.data_referencia,
        d.fonte,

        abs(
            max(d.valor) filter (
                where d.codigo_conta = '7.04.01'
                  and (
                      lower(d.descricao_conta)
                          like '%depreciação%'
                      or lower(d.descricao_conta)
                          like '%depreciacao%'
                  )
                  and (
                      lower(d.descricao_conta)
                          like '%amortização%'
                      or lower(d.descricao_conta)
                          like '%amortizacao%'
                  )
            )
        ) as depreciacao_amortizacao

    from investimento.demonstracoes_financeiras d

    where d.fonte in (
        'CVM_DFP',
        'CVM_ITR'
    )
      and d.tipo_demonstracao = 'DVA'
      and d.codigo_conta = '7.04.01'
      and d.codigo_cvm is not null

    group by
        d.codigo_cvm,
        d.data_referencia,
        d.fonte
),

dva_atual as (
    select distinct on (
        codigo_cvm,
        data_referencia
    )
        codigo_cvm,
        data_referencia,
        fonte,
        depreciacao_amortizacao

    from dva_base

    order by
        codigo_cvm,
        data_referencia,

        case
            when fonte = 'CVM_DFP'
                then 0
            else 1
        end
),

bp_base as (
    select
        d.codigo_cvm,
        d.data_referencia,
        d.fonte,

        max(d.valor) filter (
            where d.tipo_demonstracao = 'BPP'
              and d.codigo_conta in (
                  '2.03',
                  '2.07',
                  '2.07.01',
                  '2.08',
                  '2.08.01',
                  '2.09',
                  '2.09.01'
              )
              and (
                  lower(d.descricao_conta)
                      like '%patrimônio líquido%'
                  or lower(d.descricao_conta)
                      like '%patrimonio liquido%'
              )
              and (
                  lower(d.descricao_conta)
                      like '%atribuído ao controlador%'
                  or lower(d.descricao_conta)
                      like '%atribuido ao controlador%'
                  or lower(d.descricao_conta)
                      like '%acionistas controladores%'
              )
        ) as pl_controlador,

        max(d.valor) filter (
            where d.tipo_demonstracao = 'BPP'
              and d.codigo_conta in (
                  '2.03',
                  '2.07',
                  '2.07.01',
                  '2.08',
                  '2.08.01',
                  '2.09',
                  '2.09.01'
              )
              and (
                  lower(d.descricao_conta)
                      like '%patrimônio líquido consolidado%'
                  or lower(d.descricao_conta)
                      like '%patrimonio liquido consolidado%'
              )
        ) as pl_consolidado,

        max(d.valor) filter (
            where d.tipo_demonstracao = 'BPP'
              and d.codigo_conta in (
                  '2.03',
                  '2.07',
                  '2.07.01',
                  '2.08',
                  '2.08.01',
                  '2.09',
                  '2.09.01'
              )
              and (
                  lower(d.descricao_conta)
                      like '%patrimônio líquido%'
                  or lower(d.descricao_conta)
                      like '%patrimonio liquido%'
              )
              and lower(d.descricao_conta)
                  not like '%não controlador%'
              and lower(d.descricao_conta)
                  not like '%nao controlador%'
        ) as pl_generico,

        max(d.valor) filter (
            where d.tipo_demonstracao = 'BPA'
              and d.codigo_conta = '1.01'
              and lower(d.descricao_conta)
                  = 'ativo circulante'
        ) as ativo_circulante,

        max(d.valor) filter (
            where d.tipo_demonstracao = 'BPP'
              and d.codigo_conta = '2.01'
              and lower(d.descricao_conta)
                  = 'passivo circulante'
        ) as passivo_circulante,

        max(d.valor) filter (
            where d.tipo_demonstracao = 'BPA'
              and d.codigo_conta = '1.01.01'
              and lower(d.descricao_conta)
                  like 'caixa%'
        ) as caixa,

        max(d.valor) filter (
            where d.tipo_demonstracao = 'BPP'
              and d.codigo_conta = '2.01.04'
              and (
                  lower(d.descricao_conta)
                      like '%empréstim%'
                  or lower(d.descricao_conta)
                      like '%emprestim%'
              )
              and lower(d.descricao_conta)
                  like '%financiam%'
        ) as divida_curto,

        max(d.valor) filter (
            where d.tipo_demonstracao = 'BPP'
              and d.codigo_conta = '2.02.01'
              and (
                  lower(d.descricao_conta)
                      like '%empréstim%'
                  or lower(d.descricao_conta)
                      like '%emprestim%'
              )
              and lower(d.descricao_conta)
                  like '%financiam%'
        ) as divida_longo

    from investimento.demonstracoes_financeiras d

    where d.fonte in (
        'CVM_DFP',
        'CVM_ITR'
    )
      and d.codigo_cvm is not null
      and (
          (
              d.tipo_demonstracao = 'BPA'
              and d.codigo_conta in (
                  '1.01',
                  '1.01.01'
              )
          )
          or
          (
              d.tipo_demonstracao = 'BPP'
              and d.codigo_conta in (
                  '2.01',
                  '2.01.04',
                  '2.02.01',
                  '2.03',
                  '2.07',
                  '2.07.01',
                  '2.08',
                  '2.08.01',
                  '2.09',
                  '2.09.01'
              )
          )
      )

    group by
        d.codigo_cvm,
        d.data_referencia,
        d.fonte
),

bp_raw as (
    select
        b.codigo_cvm,
        b.data_referencia,
        b.fonte,

        coalesce(
            b.pl_controlador,
            b.pl_consolidado,
            b.pl_generico
        ) as patrimonio_liquido,

        b.ativo_circulante,
        b.passivo_circulante,
        b.caixa,
        b.divida_curto,
        b.divida_longo,

        case
            when b.caixa is not null
              and (
                  b.divida_curto is not null
                  or b.divida_longo is not null
              )
                then
                    coalesce(
                        b.divida_curto,
                        0
                    )
                    + coalesce(
                        b.divida_longo,
                        0
                    )
                    - b.caixa
            else null
        end as divida_liquida_raw

    from bp_base b
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

    union

    select
        codigo_cvm,
        data_referencia
    from dva_atual
),

base as (
    select
        r.codigo_cvm,
        r.data_referencia,

        (
            lower(
                coalesce(
                    s.classificacao_setorial,
                    ''
                )
            )
            like 'financeiro / intermediários financeiros /%'

            or lower(
                coalesce(
                    s.classificacao_setorial,
                    ''
                )
            )
            like 'financeiro / intermediarios financeiros /%'

            or lower(
                coalesce(
                    s.classificacao_setorial,
                    ''
                )
            )
            like 'financeiro / previdência e seguros /%'

            or lower(
                coalesce(
                    s.classificacao_setorial,
                    ''
                )
            )
            like 'financeiro / previdencia e seguros /%'

            or lower(
                coalesce(
                    d.receita_descricao,
                    ''
                )
            )
            like '%intermedia%financeir%'

            or lower(
                coalesce(
                    d.receita_descricao,
                    ''
                )
            )
            like '%opera%segur%'

            or lower(
                coalesce(
                    d.receita_descricao,
                    ''
                )
            )
            like '%prêmio%'

            or lower(
                coalesce(
                    d.receita_descricao,
                    ''
                )
            )
            like '%premio%'
        ) as estrutura_financeira,

        d.fonte as dre_fonte,
        d.receita as receita_atual,
        d.receita_descricao,
        d.lucro as lucro_atual,
        d.ebit as ebit_atual,
        d.resultado_antes_tributos as pretax_atual,
        d.imposto_renda_cs as imposto_atual,

        v.fonte as dva_fonte,
        v.depreciacao_amortizacao as da_atual,

        b.patrimonio_liquido,
        b.ativo_circulante,
        b.passivo_circulante,
        b.divida_liquida_raw,

        anual.receita as receita_anual,
        anual.lucro as lucro_anual,
        anual.ebit as ebit_anual,
        anual.resultado_antes_tributos as pretax_anual,
        anual.imposto_renda_cs as imposto_anual,

        anterior.receita as receita_ano_anterior,
        anterior.lucro as lucro_ano_anterior,
        anterior.ebit as ebit_ano_anterior,
        anterior.resultado_antes_tributos as pretax_ano_anterior,
        anterior.imposto_renda_cs as imposto_ano_anterior,

        dva_anual.depreciacao_amortizacao as da_anual,
        dva_anterior.depreciacao_amortizacao as da_ano_anterior,

        bp_anterior.patrimonio_liquido as patrimonio_liquido_ano_anterior,
        bp_anterior.divida_liquida_raw as divida_liquida_ano_anterior

    from refs r

    left join setores s
      on s.codigo_cvm = r.codigo_cvm

    left join dre_atual d
      on d.codigo_cvm = r.codigo_cvm
     and d.data_referencia = r.data_referencia

    left join dva_atual v
      on v.codigo_cvm = r.codigo_cvm
     and v.data_referencia = r.data_referencia

    left join bp_atual b
      on b.codigo_cvm = r.codigo_cvm
     and b.data_referencia = r.data_referencia

    left join lateral (
        select
            x.receita,
            x.lucro,
            x.ebit,
            x.resultado_antes_tributos,
            x.imposto_renda_cs

        from dre_raw x

        where x.codigo_cvm = r.codigo_cvm
          and x.fonte = 'CVM_DFP'
          and x.data_referencia < r.data_referencia

        order by
            x.data_referencia desc

        limit 1
    ) anual on true

    left join lateral (
        select
            x.receita,
            x.lucro,
            x.ebit,
            x.resultado_antes_tributos,
            x.imposto_renda_cs

        from dre_raw x

        where x.codigo_cvm = r.codigo_cvm
          and x.fonte = 'CVM_ITR'
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
            x.depreciacao_amortizacao

        from dva_base x

        where x.codigo_cvm = r.codigo_cvm
          and x.fonte = 'CVM_DFP'
          and x.data_referencia < r.data_referencia

        order by
            x.data_referencia desc

        limit 1
    ) dva_anual on true

    left join lateral (
        select
            x.depreciacao_amortizacao

        from dva_base x

        where x.codigo_cvm = r.codigo_cvm
          and x.fonte = 'CVM_ITR'
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
    ) dva_anterior on true

    left join lateral (
        select
            x.patrimonio_liquido,
            x.divida_liquida_raw

        from bp_raw x

        where x.codigo_cvm = r.codigo_cvm
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
    ) bp_anterior on true
),

ttm as (
    select
        *,

        case
            when dre_fonte = 'CVM_DFP'
                then receita_atual

            when dre_fonte = 'CVM_ITR'
              and receita_anual is not null
              and receita_ano_anterior is not null
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
              and lucro_anual is not null
              and lucro_ano_anterior is not null
                then
                    lucro_anual
                    + lucro_atual
                    - lucro_ano_anterior

            else null
        end as lucro_liquido_ttm,

        case
            when dre_fonte = 'CVM_DFP'
                then ebit_atual

            when dre_fonte = 'CVM_ITR'
              and ebit_anual is not null
              and ebit_ano_anterior is not null
                then
                    ebit_anual
                    + ebit_atual
                    - ebit_ano_anterior

            else null
        end as ebit_ttm,

        case
            when dre_fonte = 'CVM_DFP'
                then pretax_atual

            when dre_fonte = 'CVM_ITR'
              and pretax_anual is not null
              and pretax_ano_anterior is not null
                then
                    pretax_anual
                    + pretax_atual
                    - pretax_ano_anterior

            else null
        end as pretax_ttm,

        case
            when dre_fonte = 'CVM_DFP'
                then imposto_atual

            when dre_fonte = 'CVM_ITR'
              and imposto_anual is not null
              and imposto_ano_anterior is not null
                then
                    imposto_anual
                    + imposto_atual
                    - imposto_ano_anterior

            else null
        end as imposto_ttm,

        case
            when dva_fonte = 'CVM_DFP'
                then da_atual

            when dva_fonte = 'CVM_ITR'
              and da_anual is not null
              and da_ano_anterior is not null
                then
                    da_anual
                    + da_atual
                    - da_ano_anterior

            else null
        end as depreciacao_amortizacao_ttm

    from base
),

metricas_base as (
    select
        *,

        case
            when estrutura_financeira
                then null

            when ebit_ttm is not null
              and depreciacao_amortizacao_ttm is not null
                then
                    ebit_ttm
                    + depreciacao_amortizacao_ttm

            else null
        end as ebitda_ttm,

        case
            when estrutura_financeira
                then null

            when pretax_ttm > 0
              and imposto_ttm < 0
              and (
                  -imposto_ttm
                  / nullif(
                      pretax_ttm,
                      0
                  )
              ) between 0 and 0.50
                then
                    -imposto_ttm
                    / pretax_ttm

            else 0.34::numeric
        end as taxa_imposto_roic

    from ttm
),

empresa as (
    select
        codigo_cvm,
        data_referencia,
        receita_ttm,
        lucro_liquido_ttm,
        patrimonio_liquido,

        case
            when estrutura_financeira
                then null
            else divida_liquida_raw
        end as divida_liquida,

        case
            when estrutura_financeira
                then null

            when ebit_ttm > 0
              and patrimonio_liquido is not null
              and patrimonio_liquido_ano_anterior is not null
              and divida_liquida_raw is not null
              and divida_liquida_ano_anterior is not null
              and (
                  patrimonio_liquido
                  + divida_liquida_raw
              ) > 0
              and (
                  patrimonio_liquido_ano_anterior
                  + divida_liquida_ano_anterior
              ) > 0
                then
                    (
                        ebit_ttm
                        * (
                            1
                            - taxa_imposto_roic
                        )
                    )
                    /
                    nullif(
                        (
                            (
                                patrimonio_liquido
                                + divida_liquida_raw
                            )
                            +
                            (
                                patrimonio_liquido_ano_anterior
                                + divida_liquida_ano_anterior
                            )
                        )
                        / 2,
                        0
                    )

            else null
        end as roic,

        case
            when patrimonio_liquido > 0
              and patrimonio_liquido_ano_anterior > 0
              and lucro_liquido_ttm is not null
                then
                    lucro_liquido_ttm
                    / nullif(
                        (
                            patrimonio_liquido
                            + patrimonio_liquido_ano_anterior
                        )
                        / 2,
                        0
                    )

            else null
        end as roe,

        case
            when estrutura_financeira
                then null

            when receita_ttm is not null
              and receita_ttm <> 0
              and lucro_liquido_ttm is not null
                then
                    lucro_liquido_ttm
                    / receita_ttm

            else null
        end as margem_liquida,

        case
            when estrutura_financeira
                then null

            when receita_ttm > 0
              and ebitda_ttm is not null
                then
                    ebitda_ttm
                    / receita_ttm

            else null
        end as margem_ebitda,

        case
            when estrutura_financeira
                then null

            when ativo_circulante is not null
              and passivo_circulante > 0
                then
                    ativo_circulante
                    / passivo_circulante

            else null
        end as liquidez_corrente,

        case
            when estrutura_financeira
                then null

            when divida_liquida_raw is not null
              and ebitda_ttm > 0
                then
                    divida_liquida_raw
                    / ebitda_ttm

            else null
        end as divida_liquida_ebitda

    from metricas_base
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
        e.roic,
        e.margem_liquida,
        e.margem_ebitda,
        e.liquidez_corrente,
        e.divida_liquida_ebitda

    from empresa e

    join acoes a
      on a.codigo_cvm = e.codigo_cvm

    where
        e.receita_ttm is not null
        or e.lucro_liquido_ttm is not null
        or e.patrimonio_liquido is not null
        or e.divida_liquida is not null
        or e.roe is not null
        or e.roic is not null
        or e.margem_liquida is not null
        or e.margem_ebitda is not null
        or e.liquidez_corrente is not null
        or e.divida_liquida_ebitda is not null
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
        roic,
        margem_liquida,
        margem_ebitda,
        liquidez_corrente,
        divida_liquida_ebitda,
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
        roic,
        margem_liquida,
        margem_ebitda,
        liquidez_corrente,
        divida_liquida_ebitda,
        'CVM_DFP_ITR',
        now()

    from linhas

    on conflict (
        ativo_id,
        data_referencia
    )
    do update set
        receita_ttm = excluded.receita_ttm,
        lucro_liquido_ttm = excluded.lucro_liquido_ttm,
        patrimonio_liquido = excluded.patrimonio_liquido,
        divida_liquida = excluded.divida_liquida,
        roe = excluded.roe,
        roic = excluded.roic,
        margem_liquida = excluded.margem_liquida,
        margem_ebitda = excluded.margem_ebitda,
        liquidez_corrente = excluded.liquidez_corrente,
        divida_liquida_ebitda = excluded.divida_liquida_ebitda,
        fonte_base = excluded.fonte_base,
        calculado_em = now()

    returning 1
)

select count(*)
from upserted
"""


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

    return int(log_id)


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


def contar_demonstracoes(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            select count(*)
            from investimento.demonstracoes_financeiras
            where fonte in (
                'CVM_DFP',
                'CVM_ITR'
            )
            """
        )

        total = cur.fetchone()[0]

    return int(total or 0)


def calcular_fundamentos(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(CALCULAR_SQL)
        gravados = cur.fetchone()[0]

    return int(gravados or 0)


def validar_resultado(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                count(*) as snapshots,
                count(distinct ativo_id) as ativos,
                min(data_referencia) as primeira_data,
                max(data_referencia) as ultima_data,

                count(*) filter (
                    where receita_ttm is not null
                ) as com_receita,

                count(*) filter (
                    where lucro_liquido_ttm is not null
                ) as com_lucro,

                count(*) filter (
                    where patrimonio_liquido is not null
                ) as com_pl,

                count(*) filter (
                    where roe is not null
                ) as com_roe,

                count(*) filter (
                    where roic is not null
                ) as com_roic,

                count(*) filter (
                    where margem_liquida is not null
                ) as com_margem_liquida,

                count(*) filter (
                    where margem_ebitda is not null
                ) as com_margem_ebitda,

                count(*) filter (
                    where liquidez_corrente is not null
                ) as com_liquidez,

                count(*) filter (
                    where divida_liquida is not null
                ) as com_divida,

                count(*) filter (
                    where divida_liquida_ebitda is not null
                ) as com_divida_ebitda

            from investimento.fundamentos_snapshot

            where fonte_base = %s
            """,
            (
                FONTE_BASE,
            ),
        )

        row = cur.fetchone()

    return {
        "snapshots": int(row[0] or 0),
        "ativos": int(row[1] or 0),
        "primeira_data": row[2],
        "ultima_data": row[3],
        "com_receita": int(row[4] or 0),
        "com_lucro": int(row[5] or 0),
        "com_pl": int(row[6] or 0),
        "com_roe": int(row[7] or 0),
        "com_roic": int(row[8] or 0),
        "com_margem_liquida": int(row[9] or 0),
        "com_margem_ebitda": int(row[10] or 0),
        "com_liquidez": int(row[11] or 0),
        "com_divida": int(row[12] or 0),
        "com_divida_ebitda": int(row[13] or 0),
    }


def main() -> int:
    conn = connect()
    log_id = None

    try:
        log_id = criar_log(conn)

        lidos = contar_demonstracoes(conn)
        gravados = calcular_fundamentos(conn)

        conn.commit()

        resumo = validar_resultado(conn)

        mensagem = (
            "Fundamentos calculados a partir de DFP/ITR CVM; "
            f"demonstracoes_lidas={lidos}; "
            f"linhas_atualizadas={gravados}; "
            f"snapshots={resumo['snapshots']}; "
            f"ativos={resumo['ativos']}; "
            f"roic={resumo['com_roic']}; "
            f"margem_ebitda={resumo['com_margem_ebitda']}; "
            f"divida_liq_ebitda={resumo['com_divida_ebitda']}; "
            f"periodo={resumo['primeira_data']}..{resumo['ultima_data']}."
        )

        finalizar_log(
            conn=conn,
            log_id=log_id,
            status="SUCESSO",
            lidos=lidos,
            gravados=gravados,
            mensagem=mensagem,
        )

        print("Fundamentos: SUCESSO")
        print(f"Demonstracoes lidas: {lidos}")
        print(f"Linhas atualizadas: {gravados}")
        print(f"Snapshots no banco: {resumo['snapshots']}")
        print(f"Ativos cobertos: {resumo['ativos']}")
        print(
            "Periodo: "
            f"{resumo['primeira_data']} a "
            f"{resumo['ultima_data']}"
        )
        print(f"Receita TTM: {resumo['com_receita']}")
        print(f"Lucro TTM: {resumo['com_lucro']}")
        print(f"Patrimonio liquido: {resumo['com_pl']}")
        print(f"ROE: {resumo['com_roe']}")
        print(f"ROIC: {resumo['com_roic']}")
        print(
            "Margem liquida: "
            f"{resumo['com_margem_liquida']}"
        )
        print(
            "Margem EBITDA: "
            f"{resumo['com_margem_ebitda']}"
        )
        print(
            "Liquidez corrente: "
            f"{resumo['com_liquidez']}"
        )
        print(f"Divida liquida: {resumo['com_divida']}")
        print(
            "Divida liquida/EBITDA: "
            f"{resumo['com_divida_ebitda']}"
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
                    mensagem=str(exc),
                )
            except Exception:
                pass

        print(
            f"Fundamentos: erro | {exc}",
            file=sys.stderr,
        )

        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
