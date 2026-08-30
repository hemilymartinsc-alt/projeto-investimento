-- Migration 013 — camada oficial B3 ISIN, tolerante a duplicidades reais.
-- IMPORTANTE: o cadastro da B3 pode repetir código de emissor e/ou ISIN.
-- Por isso, as tabelas preservam cada linha oficial e a ponte validada é
-- exposta apenas quando o vínculo ISIN -> código emissor -> CNPJ é unívoco.
-- Esta migration NÃO altera investimento.ativos.

create table if not exists investimento.b3_isin_emissores (
    id bigserial primary key,
    codigo_emissor_b3 text not null,
    nome_emissor text not null,
    cnpj_emissor_normalizado text,
    campo_4_oficial text,
    linha_arquivo integer not null,
    data_referencia date not null,
    coletado_em timestamptz not null default now(),
    dados_raw jsonb not null,
    constraint b3_isin_emissores_linha_positiva
        check (linha_arquivo > 0),
    constraint b3_isin_emissores_cnpj_formato
        check (
            cnpj_emissor_normalizado is null
            or cnpj_emissor_normalizado ~ '^[0-9]{14}$'
        )
);

create unique index if not exists ux_b3_isin_emissores_ref_linha
    on investimento.b3_isin_emissores (data_referencia, linha_arquivo);

create index if not exists idx_b3_isin_emissores_codigo
    on investimento.b3_isin_emissores (codigo_emissor_b3);

create index if not exists idx_b3_isin_emissores_cnpj
    on investimento.b3_isin_emissores (cnpj_emissor_normalizado);

create table if not exists investimento.b3_isin_cadastro (
    id bigserial primary key,
    isin text not null,
    codigo_emissor_b3 text not null,
    linha_arquivo integer not null,
    data_referencia date not null,
    situacao_b3 text,
    cfi_code text,
    descricao_instrumento text,
    moeda text,
    origem_b3 text,
    coletado_em timestamptz not null default now(),
    dados_raw jsonb not null,
    constraint b3_isin_cadastro_linha_positiva
        check (linha_arquivo > 0),
    constraint b3_isin_cadastro_isin_formato
        check (isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$')
);

create unique index if not exists ux_b3_isin_cadastro_ref_linha
    on investimento.b3_isin_cadastro (data_referencia, linha_arquivo);

create index if not exists idx_b3_isin_cadastro_isin
    on investimento.b3_isin_cadastro (isin);

create index if not exists idx_b3_isin_cadastro_codigo_emissor
    on investimento.b3_isin_cadastro (codigo_emissor_b3);

create index if not exists idx_b3_isin_cadastro_data_referencia
    on investimento.b3_isin_cadastro (data_referencia);

-- Resolve código do emissor apenas quando TODAS as linhas daquele código
-- possuem CNPJ e existe exatamente um CNPJ distinto. Duplicatas idênticas
-- são toleradas; divergências ficam fora da ponte validada.
create or replace view investimento.vw_b3_isin_emissor_cnpj_unico as
select
    codigo_emissor_b3,
    min(cnpj_emissor_normalizado) as cnpj_emissor_normalizado,
    count(*) as qtd_linhas_emissor
from investimento.b3_isin_emissores
group by codigo_emissor_b3
having count(*) = count(cnpj_emissor_normalizado)
   and count(distinct cnpj_emissor_normalizado) = 1;

-- Resolve o ISIN apenas quando todas as ocorrências apontam para um único
-- código de emissor e esse código possui CNPJ unívoco segundo a regra acima.
create or replace view investimento.vw_b3_isin_ponte_unica as
with isin_unico as (
    select
        isin,
        min(codigo_emissor_b3) as codigo_emissor_b3,
        count(*) as qtd_linhas_isin
    from investimento.b3_isin_cadastro
    group by isin
    having count(distinct codigo_emissor_b3) = 1
)
select
    i.isin,
    i.codigo_emissor_b3,
    e.cnpj_emissor_normalizado,
    i.qtd_linhas_isin,
    e.qtd_linhas_emissor
from isin_unico i
join investimento.vw_b3_isin_emissor_cnpj_unico e
  on e.codigo_emissor_b3 = i.codigo_emissor_b3;

comment on table investimento.b3_isin_emissores is
    'Snapshot oficial corrente B3 de emissores do Banco de Dados Completo ISIN. Preserva duplicidades reais por linha. CNPJ é técnico/interno e não deve ser exibido no Power BI.';

comment on table investimento.b3_isin_cadastro is
    'Snapshot oficial corrente B3 da numeração ISIN. Preserva todas as linhas oficiais; vínculos unívocos ficam em vw_b3_isin_ponte_unica.';

comment on view investimento.vw_b3_isin_ponte_unica is
    'Ponte fail-closed ISIN -> código emissor -> CNPJ. Exclui qualquer ambiguidade de emissor/CNPJ.';

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
    'B3_ISIN',
    'B3 - Banco de Dados Completo ISIN',
    'OFICIAL',
    true,
    'https://sistemaswebb3-listados.b3.com.br/isinPage',
    'DIARIA',
    'Vínculo oficial e auditável ISIN -> emissor -> CNPJ para validações complementares',
    true
)
on conflict (codigo) do update set
    nome = excluded.nome,
    tipo = excluded.tipo,
    oficial = excluded.oficial,
    url_base = excluded.url_base,
    periodicidade = excluded.periodicidade,
    finalidade = excluded.finalidade,
    ativa = excluded.ativa,
    atualizado_em = now();
