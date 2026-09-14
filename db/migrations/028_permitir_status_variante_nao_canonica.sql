alter table investimento.ativos
drop constraint if exists ativos_status_atividade_b3_check;

alter table investimento.ativos
add constraint ativos_status_atividade_b3_check
check (
    status_atividade_b3 in (
        'CONFIRMADA',
        'PENDENTE_DATA_INICIO',
        'INICIO_FUTURO',
        'INATIVA_B3',
        'AUSENTE_B3_ATUAL',
        'PENDENTE',
        'VARIANTE_NAO_CANONICA'
    )
);
