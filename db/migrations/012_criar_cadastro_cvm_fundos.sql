-- Snapshot oficial corrente do cadastro de fundos, classes e subclasses da CVM.
-- Esta camada é apenas cadastral/auditável: não valida nem altera o Cadastro
-- Mestre investimento.ativos.
create table if not exists investimento.cvm_fundos_cadastro (
    id bigserial primary key,
    fonte_codigo text not null default 'CVM_FUNDOS_CAD',
    fonte_arquivo text not null,
    regime_regulatorio text not null,
    tipo_registro text not null check (
        tipo_registro in ('FUNDO', 'CLASSE', 'SUBCLASSE')
    ),
    identificador_oficial text not null,
    id_registro_fundo text,
    id_registro_classe text,
    id_subclasse text,
    codigo_cvm text,
    cnpj_registro_normalizado text,
    cnpj_fundo_normalizado text,
    cnpj_classe_normalizado text,
    denominacao_social text not null,
    denominacao_normalizada text not null,
    situacao text,
    tipo_fundo text,
    tipo_classe text,
    categoria_regulatoria text,
    classificacao_anbima text,
    data_registro date,
    data_constituicao date,
    data_inicio_atividade date,
    data_inicio_situacao date,
    data_cancelamento date,
    data_adaptacao_rcvm175 date,
    administrador_cnpj_normalizado text,
    administrador_nome text,
    gestor_documento_normalizado text,
    gestor_nome text,
    data_referencia date not null,
    coletado_em timestamptz not null default now(),
    dados_raw jsonb not null
);

create index if not exists idx_cvm_fundos_cadastro_cnpj_registro
    on investimento.cvm_fundos_cadastro (cnpj_registro_normalizado);

create index if not exists idx_cvm_fundos_cadastro_cnpj_fundo
    on investimento.cvm_fundos_cadastro (cnpj_fundo_normalizado);

create index if not exists idx_cvm_fundos_cadastro_cnpj_classe
    on investimento.cvm_fundos_cadastro (cnpj_classe_normalizado);

create index if not exists idx_cvm_fundos_cadastro_codigo_cvm
    on investimento.cvm_fundos_cadastro (codigo_cvm);

create index if not exists idx_cvm_fundos_cadastro_identificador
    on investimento.cvm_fundos_cadastro (identificador_oficial);

create index if not exists idx_cvm_fundos_cadastro_denominacao
    on investimento.cvm_fundos_cadastro (denominacao_normalizada);

create index if not exists idx_cvm_fundos_cadastro_tipo_registro
    on investimento.cvm_fundos_cadastro (tipo_registro);

comment on table investimento.cvm_fundos_cadastro is
    'Snapshot oficial corrente CVM de fundos, classes e subclasses. Não altera validação, classe, subclasse ou elegibilidade do Cadastro Mestre.';

comment on column investimento.cvm_fundos_cadastro.cnpj_registro_normalizado is
    'Identificador interno para cruzamentos oficiais. CNPJ não deve ser exposto no Power BI.';

comment on column investimento.cvm_fundos_cadastro.dados_raw is
    'Registro oficial integral conforme recebido da CVM, preservado para auditoria.';

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
    'CVM_FUNDOS_CAD',
    'CVM - Cadastro de Fundos de Investimento',
    'OFICIAL',
    true,
    'https://dados.cvm.gov.br/dados/FI/CAD/',
    'DIARIA',
    'Cadastro oficial de fundos, classes e subclasses para auditoria e validações oficiais complementares',
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
