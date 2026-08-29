alter table investimento.ativos
    drop constraint if exists ativos_status_validacao_check;

alter table investimento.ativos
    add constraint ativos_status_validacao_check check (
        status_validacao in (
            'PENDENTE_VALIDACAO',
            'VALIDADO_B3',
            'VALIDADO_OFICIAL',
            'INATIVO',
            'DIVERGENTE',
            'DUVIDOSO',
            'SEM_ISIN'
        )
    );

create table if not exists investimento.validacoes_ativos_oficiais (
    id bigserial primary key,
    ticker text not null,
    classe text not null,
    fonte_codigo text not null,
    tipo_validacao text not null,
    identificador_oficial text,
    resultado text not null check (
        resultado in (
            'CONFIRMADO',
            'DIVERGENTE',
            'INATIVO',
            'DUVIDOSO',
            'NAO_ENCONTRADO'
        )
    ),
    data_referencia date,
    detalhes jsonb not null default '{}'::jsonb,
    validado_em timestamptz not null default now()
);

create index if not exists idx_validacoes_ativos_oficiais_ticker
    on investimento.validacoes_ativos_oficiais (ticker);

create index if not exists idx_validacoes_ativos_oficiais_fonte_codigo
    on investimento.validacoes_ativos_oficiais (fonte_codigo);

create index if not exists idx_validacoes_ativos_oficiais_resultado
    on investimento.validacoes_ativos_oficiais (resultado);

comment on table investimento.validacoes_ativos_oficiais is
    'Histórico das validações oficiais por ativo, classe e fonte.';

comment on column investimento.ativos.elegivel_analise is
    'Só pode ser true depois de todas as validações oficiais obrigatórias da respectiva classe.';
