alter table investimento.ativos
drop constraint if exists ativos_nao_canonico_inelegivel_check;

alter table investimento.ativos
add constraint ativos_nao_canonico_inelegivel_check
check (
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
            'INDEX',
            'SUFFIX_G_DUPLICATE'
        )
    )
);
