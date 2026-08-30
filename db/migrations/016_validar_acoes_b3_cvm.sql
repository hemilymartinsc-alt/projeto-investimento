-- Validação oficial das ações brasileiras.
-- B3 Instruments -> B3 Empresas Listadas -> CVM Cadastro de Companhias.

create table if not exists investimento.acoes_validacao_oficial_auditoria (
    id bigserial primary key,
    executado_em timestamptz not null default now(),
    ativo_id bigint not null,
    ticker text not null,
    codigo_cvm text,
    cnpj_b3 text,
    situacao_registro_cvm text,
    situacao_emissor_cvm text,
    status_validacao_antes text,
    elegivel_antes boolean,
    classe_antes text,
    subclasse_antes text,
    acao_aplicada text not null
);

create index if not exists idx_acoes_validacao_auditoria_ticker
    on investimento.acoes_validacao_oficial_auditoria (ticker);

create or replace view investimento.vw_acoes_validacao_oficial_atual as
with ultima_b3 as (
    select max(data_referencia) as data_referencia
    from investimento.b3_instrumentos_snapshot
    where status_arquivo = 'Final'
), base_acoes as (
    select a.id as ativo_id, a.ticker, a.isin, a.classe, a.subclasse,
           a.status_validacao, a.elegivel_analise,
           trim(s.ativo_base) as codigo_emissor_b3
    from investimento.ativos a
    join ultima_b3 u on true
    join investimento.b3_instrumentos_snapshot s
      on s.ticker = a.ticker and s.data_referencia = u.data_referencia
    where a.classe = 'ACAO'
      and a.instrumento_canonico = true
      and a.atividade_confirmada_b3 = true
), ponte as (
    select b.*, e.codigo_cvm,
           regexp_replace(coalesce(e.cnpj, ''), '\D', '', 'g') as cnpj_b3,
           e.razao_social, c.cnpj as cnpj_cvm_formatado,
           c.situacao_registro, c.situacao_emissor, c.denominacao_social
    from base_acoes b
    left join investimento.b3_empresas_listadas e
      on upper(trim(e.codigo_emissor)) = upper(trim(b.codigo_emissor_b3))
    left join investimento.cvm_cadastro_companhias c
      on c.codigo_cvm = e.codigo_cvm
)
select p.*,
       case
         when p.codigo_cvm is null then 'SEM_EMPRESA_B3'
         when p.cnpj_cvm_formatado is null then 'SEM_CVM'
         when p.situacao_registro = 'SUSPENSO(A) - DECISÃO ADM' then 'REGISTRO_CVM_SUSPENSO'
         when p.situacao_registro <> 'ATIVO' then 'REGISTRO_CVM_NAO_ATIVO'
         when p.situacao_emissor = 'LIQUIDAÇÃO EXTRAJUDICIAL' then 'EM_LIQUIDACAO'
         when p.situacao_emissor = 'PARALISADA' then 'EMISSOR_PARALISADO'
         else 'VALIDADO_OFICIAL'
       end as status_resolucao,
       (
         p.codigo_cvm is not null
         and p.cnpj_cvm_formatado is not null
         and p.situacao_registro = 'ATIVO'
         and coalesce(p.situacao_emissor, '') not in ('LIQUIDAÇÃO EXTRAJUDICIAL', 'PARALISADA')
       ) as apto_validacao_oficial
from ponte p;

insert into investimento.acoes_validacao_oficial_auditoria (
    ativo_id, ticker, codigo_cvm, cnpj_b3, situacao_registro_cvm,
    situacao_emissor_cvm, status_validacao_antes, elegivel_antes,
    classe_antes, subclasse_antes, acao_aplicada
)
select a.id, a.ticker, v.codigo_cvm, v.cnpj_b3, v.situacao_registro,
       v.situacao_emissor, a.status_validacao, a.elegivel_analise,
       a.classe, a.subclasse,
       case when v.apto_validacao_oficial
            then 'VALIDAR_OFICIAL_E_LIBERAR_ANALISE'
            else 'MANTER_FORA_DA_FERRAMENTA' end
from investimento.ativos a
join investimento.vw_acoes_validacao_oficial_atual v on v.ativo_id = a.id;

update investimento.ativos a
set cnpj = nullif(v.cnpj_b3, ''),
    codigo_cvm = v.codigo_cvm,
    status_validacao = 'VALIDADO_OFICIAL',
    elegivel_analise = true,
    motivo_exclusao = null,
    fonte_validacao = 'B3_LISTED_COMPANIES+CVM_CAD_CIA',
    validado_em = now(),
    atualizado_em = now()
from investimento.vw_acoes_validacao_oficial_atual v
where v.ativo_id = a.id and v.apto_validacao_oficial = true;

update investimento.ativos a
set cnpj = nullif(v.cnpj_b3, ''),
    codigo_cvm = v.codigo_cvm,
    status_validacao = 'INATIVO',
    elegivel_analise = false,
    motivo_exclusao = case v.status_resolucao
      when 'REGISTRO_CVM_SUSPENSO' then 'Registro CVM atual suspenso por decisão administrativa.'
      when 'EM_LIQUIDACAO' then 'Emissor consta em liquidação extrajudicial na CVM.'
      when 'EMISSOR_PARALISADO' then 'Emissor consta como paralisado na CVM.'
      when 'REGISTRO_CVM_NAO_ATIVO' then 'Registro CVM atual não está ativo.'
      when 'SEM_EMPRESA_B3' then 'Sem correspondência exata na base oficial de empresas listadas da B3.'
      when 'SEM_CVM' then 'Sem correspondência exata no cadastro oficial de companhias da CVM.'
      else 'Não aprovado na validação oficial complementar.'
    end,
    fonte_validacao = 'B3_LISTED_COMPANIES+CVM_CAD_CIA',
    validado_em = now(),
    atualizado_em = now()
from investimento.vw_acoes_validacao_oficial_atual v
where v.ativo_id = a.id and v.apto_validacao_oficial = false;
