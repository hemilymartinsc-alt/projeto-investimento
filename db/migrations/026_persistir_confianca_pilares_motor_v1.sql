alter table investimento.motor_scores_pilares
    add column if not exists qualidade_media numeric,
    add column if not exists confianca_pilar numeric,
    add column if not exists cobertura_suficiente boolean;

alter table investimento.motor_scores_pilares
    drop constraint if exists motor_scores_pilares_qualidade_media_check;

alter table investimento.motor_scores_pilares
    add constraint motor_scores_pilares_qualidade_media_check
        check (
            qualidade_media is null
            or (
                qualidade_media >= 0
                and qualidade_media <= 1
            )
        );

alter table investimento.motor_scores_pilares
    drop constraint if exists motor_scores_pilares_confianca_pilar_check;

alter table investimento.motor_scores_pilares
    add constraint motor_scores_pilares_confianca_pilar_check
        check (
            confianca_pilar is null
            or (
                confianca_pilar >= 0
                and confianca_pilar <= 1
            )
        );

create or replace function investimento.fn_materializar_motor_v1(
    p_data date default current_date
)
returns jsonb
language plpgsql
as $function$
declare
    v_ind integer := 0;
    v_pil integer := 0;
    v_res integer := 0;
begin
    delete from investimento.motor_scores_indicadores
    where modelo_codigo = 'MOTOR_V1'
      and data_referencia = p_data;

    insert into investimento.motor_scores_indicadores(
        ativo_id,
        data_referencia,
        modelo_codigo,
        perfil,
        indicador_codigo,
        valor_bruto,
        percentil,
        score,
        qualidade_dado,
        grupo_comparacao
    )
    select
        ativo_id,
        p_data,
        modelo_codigo,
        perfil,
        indicador_codigo,
        valor_bruto,
        percentil,
        score,
        qualidade_dado,
        grupo_comparacao
    from investimento.vw_motor_scores_indicadores_calculados_v1;

    get diagnostics v_ind = row_count;

    delete from investimento.motor_scores_pilares
    where modelo_codigo = 'MOTOR_V1'
      and data_referencia = p_data;

    insert into investimento.motor_scores_pilares(
        ativo_id,
        data_referencia,
        modelo_codigo,
        perfil,
        pilar,
        score,
        cobertura,
        qtd_indicadores,
        qtd_indicadores_disponiveis,
        qualidade_media,
        confianca_pilar,
        cobertura_suficiente
    )
    select
        ativo_id,
        p_data,
        modelo_codigo,
        perfil,
        pilar,
        score,
        cobertura,
        qtd_indicadores,
        qtd_indicadores_disponiveis,
        qualidade_media,
        confianca_pilar,
        cobertura_suficiente
    from investimento.vw_motor_scores_pilares_calculados_v1;

    get diagnostics v_pil = row_count;

    delete from investimento.motor_resultados
    where modelo_codigo = 'MOTOR_V1'
      and data_referencia = p_data;

    insert into investimento.motor_resultados(
        ativo_id,
        data_referencia,
        modelo_codigo,
        perfil,
        score_ativo,
        score_oportunidade,
        score_confianca,
        decisao_ativo,
        motivo_principal,
        bloqueado,
        explicacoes
    )
    select
        ativo_id,
        p_data,
        modelo_codigo,
        perfil,
        score_ativo,
        score_oportunidade,
        score_confianca,
        decisao_ativo,
        motivo_principal,
        bloqueado,
        jsonb_build_object(
            'ticker',
            ticker,
            'modelo',
            'MOTOR_V1',
            'regra',
            'configuravel',
            'data_materializacao',
            p_data
        )
    from investimento.vw_motor_resultados_calculados_v1;

    get diagnostics v_res = row_count;

    return jsonb_build_object(
        'data',
        p_data,
        'scores_indicadores',
        v_ind,
        'scores_pilares',
        v_pil,
        'resultados',
        v_res
    );
end;
$function$;
