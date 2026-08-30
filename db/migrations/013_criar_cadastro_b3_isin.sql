-- Cadastro oficial B3 de emissores e numeração ISIN.
-- Camada técnica/auditável. Não altera investimento.ativos.

create table if not exists investimento.b3_isin_emissores (
    codigo_emissor_b3 text primary key,
    nome_emissor text not null,
    cnpj_emissor_normalizado text,
    campo_4_oficial text,
    data_referencia date not null,
    coletado_em timestamptz not null default now(),
    dados_raw jsonb not null
);

create index if not exists idx_b3_isin_emissores_cnpj
    on investimento.b3_isin_emissores (cnpj_emissor_normalizado);

create table if not exists investimento.b3_isin_cadastro (
    isin text primary key,
    codigo_emissor_b3 text not null,
    data_referencia date not null,
    situacao_b3 text,
    cfi_code text,
    descricao_instrumento text,
    moeda text,
    origem_b3 text,
    coletado_em timestamptz not null default now(),
    dados_raw jsonb not null,
    constraint b3_isin_cadastro_emissor_fk
        foreign key (codigo_emissor_b3)
        references investimento.b3_isin_emissores (codigo_emissor_b3)
        on update cascade
        on delete restrict
);

create index if not exists idx_b3_isin_cadastro_codigo_emissor
    on investimento.b3_isin_cadastro (codigo_emissor_b3);

create index if not exists idx_b3_isin_cadastro_data_referencia
    on investimento.b3_isin_cadastro (data_referencia);

comment on table investimento.b3_isin_emissores is
    'Snapshot oficial B3 de emissores do Banco de Dados Completo ISIN. CNPJ é identificador técnico interno e não deve ser exposto no Power BI.';

comment on table investimento.b3_isin_cadastro is
    'Snapshot oficial B3 da numeração ISIN. Faz a ponte ISIN -> código do emissor -> CNPJ oficial B3. Não altera o Cadastro Mestre.';

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
    'Vínculo oficial ISIN -> emissor -> CNPJ para validações complementares e auditoria',
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
