alter table investimento.ativos
    add column if not exists atividade_confirmada_b3 boolean not null default false,
    add column if not exists status_atividade_b3 text not null default 'PENDENTE',
    add column if not exists motivo_atividade_b3 text,
    add column if not exists data_referencia_b3 date,
    add column if not exists ultima_confirmacao_b3 timestamptz,
    add column if not exists verificado_b3_em timestamptz;

alter table investimento.ativos
    drop constraint if exists ativos_status_atividade_b3_check;

alter table investimento.ativos
    add constraint ativos_status_atividade_b3_check check (
        status_atividade_b3 in (
            'CONFIRMADA',
            'PENDENTE_DATA_INICIO',
            'INICIO_FUTURO',
            'INATIVA_B3',
            'AUSENTE_B3_ATUAL',
            'PENDENTE'
        )
    );

-- Backfill baseado exclusivamente no snapshot oficial Final mais recente.
-- Datas 9999-12-31 são sentinelas da B3 e equivalem a data não informada.
with ultima_referencia as (
    select max(data_referencia) as data_referencia
    from investimento.b3_instrumentos_snapshot
    where status_arquivo = 'Final'
), snapshot_atual as (
    select distinct on (upper(trim(s.ticker)))
        upper(trim(s.ticker)) as ticker,
        s.data_referencia,
        nullif(s.data_inicio_negociacao, date '9999-12-31')
            as data_inicio_negociacao,
        nullif(s.data_fim_negociacao, date '9999-12-31')
            as data_fim_negociacao,
        nullif(s.data_expiracao, date '9999-12-31')
            as data_expiracao
    from investimento.b3_instrumentos_snapshot s
    join ultima_referencia u
      on u.data_referencia = s.data_referencia
    where s.status_arquivo = 'Final'
      and s.instrumento_canonico = true
    order by
        upper(trim(s.ticker)),
        s.instrumento_canonico desc,
        s.isin nulls last
)
update investimento.ativos a
   set atividade_confirmada_b3 = case
           when (
               s.data_fim_negociacao is not null
               and s.data_fim_negociacao < s.data_referencia
           ) or (
               s.data_expiracao is not null
               and s.data_expiracao < s.data_referencia
           ) then false
           when s.data_inicio_negociacao is null then false
           when s.data_inicio_negociacao > s.data_referencia then false
           else true
       end,
       status_atividade_b3 = case
           when (
               s.data_fim_negociacao is not null
               and s.data_fim_negociacao < s.data_referencia
           ) or (
               s.data_expiracao is not null
               and s.data_expiracao < s.data_referencia
           ) then 'INATIVA_B3'
           when s.data_inicio_negociacao is null
               then 'PENDENTE_DATA_INICIO'
           when s.data_inicio_negociacao > s.data_referencia
               then 'INICIO_FUTURO'
           else 'CONFIRMADA'
       end,
       motivo_atividade_b3 = case
           when (
               s.data_fim_negociacao is not null
               and s.data_fim_negociacao < s.data_referencia
           ) or (
               s.data_expiracao is not null
               and s.data_expiracao < s.data_referencia
           ) then 'INATIVO_B3'
           when s.data_inicio_negociacao is null
               then 'DATA_INICIO_NAO_INFORMADA_B3'
           when s.data_inicio_negociacao > s.data_referencia
               then 'INICIO_NEGOCIACAO_FUTURO'
           else null
       end,
       data_referencia_b3 = s.data_referencia,
       ultima_confirmacao_b3 = case
           when (
               (s.data_fim_negociacao is null
                or s.data_fim_negociacao >= s.data_referencia)
               and (s.data_expiracao is null
                    or s.data_expiracao >= s.data_referencia)
               and s.data_inicio_negociacao is not null
               and s.data_inicio_negociacao <= s.data_referencia
           ) then now()
           else a.ultima_confirmacao_b3
       end,
       verificado_b3_em = now(),
       elegivel_analise = case
           when (
               (s.data_fim_negociacao is null
                or s.data_fim_negociacao >= s.data_referencia)
               and (s.data_expiracao is null
                    or s.data_expiracao >= s.data_referencia)
               and s.data_inicio_negociacao is not null
               and s.data_inicio_negociacao <= s.data_referencia
           ) then a.elegivel_analise
           else false
       end,
       atualizado_em = now()
  from snapshot_atual s
 where upper(trim(a.ticker)) = s.ticker
   and a.instrumento_canonico = true;

-- Um ativo ausente não é apagado: apenas perde confirmação de atividade e
-- elegibilidade até uma nova validação oficial completa.
with ultima_referencia as (
    select max(data_referencia) as data_referencia
    from investimento.b3_instrumentos_snapshot
    where status_arquivo = 'Final'
)
update investimento.ativos a
   set atividade_confirmada_b3 = false,
       status_atividade_b3 = 'AUSENTE_B3_ATUAL',
       motivo_atividade_b3 =
           'Ticker ausente do último snapshot oficial válido da B3.',
       data_referencia_b3 = u.data_referencia,
       verificado_b3_em = now(),
       elegivel_analise = false,
       atualizado_em = now()
  from ultima_referencia u
 where u.data_referencia is not null
   and not exists (
       select 1
       from investimento.b3_instrumentos_snapshot s
       where s.data_referencia = u.data_referencia
         and s.status_arquivo = 'Final'
         and upper(trim(s.ticker)) = upper(trim(a.ticker))
   );

-- Corrige qualquer elegibilidade legada antes de instalar o gate soberano.
update investimento.ativos
   set elegivel_analise = false,
       atualizado_em = now()
 where elegivel_analise = true
   and (
       instrumento_canonico = false
       or atividade_confirmada_b3 = false
       or status_validacao <> 'VALIDADO_OFICIAL'
   );

alter table investimento.ativos
    drop constraint if exists ativos_atividade_b3_coerente_check;

alter table investimento.ativos
    add constraint ativos_atividade_b3_coerente_check check (
        atividade_confirmada_b3 = (status_atividade_b3 = 'CONFIRMADA')
    );

alter table investimento.ativos
    drop constraint if exists ativos_elegibilidade_oficial_canonica_check;

alter table investimento.ativos
    add constraint ativos_elegibilidade_oficial_canonica_check check (
        elegivel_analise = false
        or (
            instrumento_canonico = true
            and atividade_confirmada_b3 = true
            and status_validacao = 'VALIDADO_OFICIAL'
        )
    );

create index if not exists idx_ativos_status_atividade_b3
    on investimento.ativos (status_atividade_b3);

create index if not exists idx_ativos_gate_analise_b3
    on investimento.ativos (
        elegivel_analise,
        instrumento_canonico,
        atividade_confirmada_b3,
        status_validacao
    );

comment on column investimento.ativos.atividade_confirmada_b3 is
    'Gate soberano de atividade: true somente quando o último snapshot oficial válido da B3 confirma o instrumento como corrente.';

comment on column investimento.ativos.status_atividade_b3 is
    'Situação exclusiva de atividade na B3; não representa validação cadastral ou complementar da classe.';

comment on column investimento.ativos.motivo_atividade_b3 is
    'Motivo auditável quando a atividade B3 não está confirmada.';

comment on column investimento.ativos.data_referencia_b3 is
    'Data do snapshot oficial B3 usado na última verificação de atividade.';

comment on column investimento.ativos.ultima_confirmacao_b3 is
    'Instante mais recente em que a atividade foi confirmada pela B3; não é apagado em verificações negativas posteriores.';

comment on column investimento.ativos.verificado_b3_em is
    'Instante da última verificação de atividade contra snapshot oficial B3 válido.';

comment on column investimento.ativos.elegivel_analise is
    'Só pode ser true para instrumento canônico, com atividade B3 confirmada e status VALIDADO_OFICIAL após todas as validações obrigatórias da classe.';

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
    ticker_canonico,
    atividade_confirmada_b3,
    status_atividade_b3,
    motivo_atividade_b3,
    data_referencia_b3,
    ultima_confirmacao_b3,
    verificado_b3_em
from investimento.ativos
where elegivel_analise = true
  and instrumento_canonico = true
  and atividade_confirmada_b3 = true
  and status_validacao = 'VALIDADO_OFICIAL';
