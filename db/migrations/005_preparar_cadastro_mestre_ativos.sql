alter table investimento.ativos
    drop constraint if exists ativos_classe_check;

alter table investimento.ativos
    add constraint ativos_classe_check check (
        classe in (
            'NAO_CLASSIFICADO',
            'ACAO',
            'FII',
            'ETF',
            'BDR',
            'ETP',
            'RENDA_FIXA',
            'FUNDO',
            'EXTERIOR',
            'OUTRO'
        )
    );

alter table investimento.ativos
    add column if not exists subclasse text,
    add column if not exists tipo_instrumento text,
    add column if not exists categoria_b3 text,
    add column if not exists segmento_b3 text,
    add column if not exists mercado_b3 text,
    add column if not exists codigo_emissor_b3 text,
    add column if not exists status_validacao text not null default 'PENDENTE_VALIDACAO',
    add column if not exists elegivel_analise boolean not null default false,
    add column if not exists motivo_exclusao text,
    add column if not exists fonte_validacao text,
    add column if not exists validado_em timestamptz;

alter table investimento.ativos
    drop constraint if exists ativos_status_validacao_check;

alter table investimento.ativos
    add constraint ativos_status_validacao_check check (
        status_validacao in (
            'PENDENTE_VALIDACAO',
            'VALIDADO_B3',
            'INATIVO',
            'DIVERGENTE',
            'DUVIDOSO',
            'SEM_ISIN'
        )
    );

create index if not exists idx_ativos_classe
    on investimento.ativos (classe);

create index if not exists idx_ativos_status_validacao
    on investimento.ativos (status_validacao);

create index if not exists idx_ativos_elegivel_analise
    on investimento.ativos (elegivel_analise);

create index if not exists idx_ativos_isin
    on investimento.ativos (isin);

-- Camada destinada ao Power BI: o CNPJ continua na tabela técnica,
-- mas não é exposto nesta view.
create or replace view investimento.vw_ativos_analise as
select
    id,
    ticker,
    nome,
    nome_pregao,
    classe,
    subclasse,
    tipo_instrumento,
    setor,
    segmento,
    categoria_b3,
    segmento_b3,
    mercado_b3,
    isin,
    codigo_cvm,
    moeda,
    status_validacao,
    elegivel_analise,
    fonte_cadastro,
    fonte_validacao,
    validado_em,
    atualizado_em
from investimento.ativos
where elegivel_analise = true;
