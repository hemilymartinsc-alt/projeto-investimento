create table if not exists investimento.cvm_cadastro_companhias (
    codigo_cvm text primary key,
    cnpj text,
    denominacao_social text,
    denominacao_comercial text,
    situacao_registro text,
    situacao_emissor text,
    setor_atividade text,
    tipo_mercado text,
    categoria_registro text,
    municipio text,
    uf text,
    pais text,
    email text,
    data_registro date,
    data_constituicao date,
    data_cancelamento date,
    fonte text not null default 'CVM_CAD_CIA',
    atualizado_fonte_em timestamptz,
    coletado_em timestamptz not null default now()
);

create index if not exists idx_cvm_cadastro_cnpj
    on investimento.cvm_cadastro_companhias (cnpj);

create index if not exists idx_cvm_cadastro_situacao
    on investimento.cvm_cadastro_companhias (situacao_registro);
