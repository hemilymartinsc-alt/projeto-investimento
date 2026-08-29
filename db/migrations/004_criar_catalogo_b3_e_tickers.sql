create table if not exists investimento.b3_empresas_listadas (
    codigo_cvm text primary key,
    cnpj text,
    razao_social text,
    nome_pregao text,
    codigo_emissor text,
    segmento_listagem text,
    mercado text,
    tipo_bdr text,
    atividade text,
    classificacao_setorial text,
    site text,
    data_listagem date,
    fonte text not null default 'B3_LISTED_COMPANIES',
    coletado_em timestamptz not null default now()
);

create index if not exists idx_b3_empresas_cnpj
    on investimento.b3_empresas_listadas (cnpj);

create index if not exists idx_b3_empresas_codigo_emissor
    on investimento.b3_empresas_listadas (codigo_emissor);

create table if not exists investimento.b3_tickers (
    ticker text primary key,
    codigo_cvm text not null
        references investimento.b3_empresas_listadas(codigo_cvm)
        on delete cascade,
    isin text,
    principal boolean not null default false,
    fonte text not null default 'B3_LISTED_COMPANIES',
    coletado_em timestamptz not null default now()
);

create index if not exists idx_b3_tickers_codigo_cvm
    on investimento.b3_tickers (codigo_cvm);
