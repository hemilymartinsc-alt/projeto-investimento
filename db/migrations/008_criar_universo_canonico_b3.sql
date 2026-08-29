alter table investimento.ativos
    add column if not exists instrumento_canonico boolean not null default false,
    add column if not exists tipo_variante_b3 text,
    add column if not exists ticker_canonico text;

-- Corrige qualquer elegibilidade legada concedida apenas pela validação B3.
update investimento.ativos
   set elegivel_analise = false,
       motivo_exclusao = coalesce(
           motivo_exclusao,
           'Aguarda validações oficiais complementares obrigatórias da classe.'
       ),
       atualizado_em = now()
 where elegivel_analise = true
   and status_validacao <> 'VALIDADO_OFICIAL';

-- Preserva elegibilidades oficiais preexistentes durante a transição de schema.
update investimento.ativos
   set instrumento_canonico = true,
       ticker_canonico = coalesce(ticker_canonico, ticker),
       atualizado_em = now()
 where elegivel_analise = true
   and status_validacao = 'VALIDADO_OFICIAL';

alter table investimento.ativos
    drop constraint if exists ativos_status_validacao_check;

alter table investimento.ativos
    add constraint ativos_status_validacao_check check (
        status_validacao in (
            'PENDENTE_VALIDACAO',
            'VALIDADO_B3',
            'VALIDADO_OFICIAL',
            'NAO_CANONICO',
            'INATIVO',
            'DIVERGENTE',
            'DUVIDOSO',
            'SEM_ISIN'
        )
    );

alter table investimento.ativos
    drop constraint if exists ativos_nao_canonico_inelegivel_check;

alter table investimento.ativos
    add constraint ativos_nao_canonico_inelegivel_check check (
        status_validacao <> 'NAO_CANONICO'
        or (
            instrumento_canonico = false
            and elegivel_analise = false
            and tipo_variante_b3 in (
                'EQUITY_BLOCK_TRADING_LOT',
                'ETF_PRIMARY_MARKET',
                'ODD_LOT',
                'RIGHTS',
                'RECEIPTS',
                'WARRANT',
                'INDEX'
            )
        )
    );

alter table investimento.ativos
    drop constraint if exists ativos_elegibilidade_oficial_canonica_check;

alter table investimento.ativos
    add constraint ativos_elegibilidade_oficial_canonica_check check (
        elegivel_analise = false
        or (
            instrumento_canonico = true
            and status_validacao = 'VALIDADO_OFICIAL'
        )
    );

alter table investimento.b3_instrumentos_snapshot
    add column if not exists instrumento_canonico boolean,
    add column if not exists tipo_variante_b3 text,
    add column if not exists ticker_canonico text;

create index if not exists idx_ativos_instrumento_canonico
    on investimento.ativos (instrumento_canonico);

create index if not exists idx_ativos_tipo_variante_b3
    on investimento.ativos (tipo_variante_b3);

create index if not exists idx_ativos_ticker_canonico
    on investimento.ativos (ticker_canonico);

create index if not exists idx_b3_snapshot_data_canonico
    on investimento.b3_instrumentos_snapshot (
        data_referencia,
        instrumento_canonico
    );

comment on column investimento.ativos.instrumento_canonico is
    'Indica estruturalmente que o ticker representa o instrumento principal, independentemente de a atividade já estar confirmada por fonte oficial.';

comment on column investimento.ativos.tipo_variante_b3 is
    'Tipo estrutural/operacional da variante B3; não deve registrar dúvidas de atividade ou validação.';

comment on column investimento.ativos.ticker_canonico is
    'Ticker principal associado por igualdade de ISIN quando essa relação oficial é inequívoca.';

comment on column investimento.ativos.elegivel_analise is
    'Só pode ser true para instrumento canônico com status VALIDADO_OFICIAL, depois de todas as validações oficiais obrigatórias da respectiva classe.';

comment on column investimento.b3_instrumentos_snapshot.instrumento_canonico is
    'Resultado estrutural da regra canônica, independente de datas de atividade; o registro bruto oficial permanece integral no snapshot.';

update investimento.fontes_dados
   set finalidade = 'Snapshot integral, identificação canônica e classificação preliminar; elegibilidade exige validações oficiais complementares.',
       atualizado_em = now()
 where codigo = 'B3_INSTRUMENTS';

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
    atualizado_em,
    instrumento_canonico,
    tipo_variante_b3,
    ticker_canonico
from investimento.ativos
where elegivel_analise = true;
