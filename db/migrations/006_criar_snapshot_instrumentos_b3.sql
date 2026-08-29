create table if not exists investimento.b3_instrumentos_snapshot (
    id bigserial primary key,
    data_referencia date not null,
    ticker text not null,
    isin text,
    ativo_base text,
    descricao_ativo text,
    segmento_b3 text,
    mercado_b3 text,
    categoria_b3 text,
    descricao_b3 text,
    cfi_code text,
    moeda text,
    nome_corporativo text,
    nivel_governanca text,
    data_inicio_negociacao date,
    data_fim_negociacao date,
    data_expiracao date,
    status_arquivo text,
    fonte text not null default 'B3_INSTRUMENTS',
    raw_json jsonb,
    coletado_em timestamptz not null default now()
);

create index if not exists idx_b3_instr_snapshot_data_ticker
    on investimento.b3_instrumentos_snapshot (data_referencia, ticker);
create index if not exists idx_b3_instr_snapshot_isin
    on investimento.b3_instrumentos_snapshot (isin);
create index if not exists idx_b3_instr_snapshot_ticker
    on investimento.b3_instrumentos_snapshot (ticker);

insert into investimento.fontes_dados (
    codigo, nome, tipo, oficial, url_base, periodicidade, finalidade, ativa
)
values (
    'B3_INSTRUMENTS',
    'B3 - Cadastro de Instrumentos',
    'arquivo_publico',
    true,
    'https://arquivos.b3.com.br/',
    'diaria',
    'Validacao, classificacao e elegibilidade dos instrumentos negociados na B3',
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
