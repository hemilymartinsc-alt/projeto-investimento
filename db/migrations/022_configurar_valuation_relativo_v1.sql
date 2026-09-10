insert into investimento.motor_parametros (
    modelo_codigo,
    parametro,
    valor_numerico,
    valor_texto,
    descricao,
    atualizado_em
)
values
    (
        'MOTOR_V1',
        'VALUATION_GRUPO_PEQUENO_MIN',
        5,
        null,
        'Número mínimo de companhias para usar um grupo comparável pequeno no valuation relativo V1.',
        now()
    ),
    (
        'MOTOR_V1',
        'VALUATION_CONFIANCA_MAX_RELATIVA',
        0.85,
        null,
        'Teto de confiança do valuation relativo P/L + P/VP V1; métodos intrínsecos futuros podem ter configuração própria.',
        now()
    )
on conflict (modelo_codigo,parametro)
do update set
    valor_numerico=excluded.valor_numerico,
    valor_texto=excluded.valor_texto,
    descricao=excluded.descricao,
    atualizado_em=now();
