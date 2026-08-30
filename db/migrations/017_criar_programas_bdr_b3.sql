-- Snapshot oficial dos programas de BDR publicados pelo Banco B3.
-- Esta migration NÃO altera elegibilidade nem classificação do Cadastro Mestre.

insert into investimento.fontes_dados (
    codigo, nome, tipo, oficial, url_base, periodicidade, finalidade, ativa,
    atualizado_em
)
values (
    'B3_BDR_PROGRAMS',
    'Banco B3 - Programas de BDR',
    'WEB_OFICIAL',
    true,
    'https://finservices.b3.com.br/',
    'DIARIA',
    'Validar programas vigentes de BDR Não Patrocinado e BDR de ETF.',
    true,
    now()
)
on conflict (codigo) do update
set nome = excluded.nome,
    tipo = excluded.tipo,
    oficial = excluded.oficial,
    url_base = excluded.url_base,
    periodicidade = excluded.periodicidade,
    finalidade = excluded.finalidade,
    ativa = excluded.ativa,
    atualizado_em = now();

create table if not exists investimento.b3_bdr_programas_snapshot (
    id bigserial primary key,
    data_referencia date not null,
    tipo_programa text not null,
    ticker text not null,
    nome_programa text not null,
    restrito_qualificados boolean not null default false,
    url_site_emissor text,
    url_documento_b3 text,
    url_pagina_fonte text not null,
    pagina integer not null,
    coletado_em timestamptz not null default now(),
    dados_raw jsonb not null default '{}'::jsonb,
    constraint b3_bdr_programas_tipo_check
        check (tipo_programa in ('NAO_PATROCINADO', 'ETF')),
    constraint b3_bdr_programas_ticker_check
        check (ticker = upper(trim(ticker))),
    unique (data_referencia, tipo_programa, ticker)
);

create index if not exists idx_b3_bdr_programas_ref_tipo
    on investimento.b3_bdr_programas_snapshot
       (data_referencia desc, tipo_programa);

create index if not exists idx_b3_bdr_programas_ticker
    on investimento.b3_bdr_programas_snapshot (ticker);

create or replace view investimento.vw_b3_bdr_programas_atual as
with ultima as (
    select max(data_referencia) as data_referencia
    from investimento.b3_bdr_programas_snapshot
)
select s.*
from investimento.b3_bdr_programas_snapshot s
join ultima u using (data_referencia);
