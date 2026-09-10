alter table investimento.composicao_capital_cvm
    add column if not exists fator_escala_aplicado integer not null default 1,
    add column if not exists total_acoes_b3 bigint,
    add column if not exists divergencia_relativa_b3 numeric,
    add column if not exists validado_b3 boolean not null default false;

comment on column
    investimento.composicao_capital_cvm.fator_escala_aplicado
is
    'Fator aplicado às quantidades informadas pela CVM para normalização em unidades, ancorado na quantidade total de ações da B3.';

comment on column
    investimento.composicao_capital_cvm.total_acoes_b3
is
    'Quantidade total de ações informada pela B3 usada como âncora de validação da escala.';

comment on column
    investimento.composicao_capital_cvm.divergencia_relativa_b3
is
    'Diferença relativa entre o total CVM normalizado e o total de ações B3 na data da coleta.';

comment on column
    investimento.composicao_capital_cvm.validado_b3
is
    'Indica se o total CVM normalizado ficou suficientemente próximo da quantidade total de ações publicada pela B3.';
