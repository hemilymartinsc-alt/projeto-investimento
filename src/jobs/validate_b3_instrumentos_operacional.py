from __future__ import annotations

from src.jobs import validate_b3_instrumentos as base


FINANCING_RATE_NAME = "TAXA DE FINANCIAMENTO"
FINANCING_RATE_VARIANT = "FINANCING_RATE"
SUFFIX_G_VARIANT = "SUFFIX_G_DUPLICATE"

_original_canonical_decision = base.canonical_decision
_original_preliminary_classification = (
    base.preliminary_classification
)
_original_load_latest_valid_snapshot_profile = (
    base.load_latest_valid_snapshot_profile
)
_original_validate_snapshot_sanity = (
    base.validate_snapshot_sanity
)
_original_annotate_universe = base.annotate_universe
_original_save_snapshot = base.save_snapshot


def security_specification(inst) -> str:
    raw = inst.get("raw_json") or {}
    return base.norm(raw.get("SpcfctnCd"))


def is_financing_rate(inst) -> bool:
    ticker = base.upper(inst.get("ticker")) or ""

    return bool(
        ticker.startswith("TAXA")
        and base.norm(inst.get("categoria_b3")) == "SHARES"
        and base.norm(inst.get("nome_corporativo"))
        == FINANCING_RATE_NAME
    )


def is_cepac(inst) -> bool:
    """Reconhece CEPAC pela especificação oficial da B3."""
    return bool(
        base.norm(inst.get("categoria_b3")) == "SHARES"
        and security_specification(inst).startswith("CPA")
    )


def is_royalty_security(inst) -> bool:
    return bool(
        base.upper(inst.get("ticker")) == "PSVM11"
        and base.upper(inst.get("isin")) == "BRPSVMTRV004"
        and base.norm(inst.get("categoria_b3")) == "SHARES"
        and security_specification(inst) == "TPR"
    )


def is_bdr_unit(inst) -> bool:
    return bool(
        base.upper(inst.get("ticker")) == "PPLA11"
        and base.upper(inst.get("isin")) == "BRPPLAUNT007"
        and base.norm(inst.get("categoria_b3")) == "UNIT"
        and base.norm(inst.get("nome_corporativo"))
        == "PPLA PARTICIPATIONS LTD."
    )


def canonical_decision(
    inst,
    ref,
    fixed_income_etf_keys=frozenset(),
):
    if is_financing_rate(inst):
        return False, FINANCING_RATE_VARIANT

    return _original_canonical_decision(
        inst,
        ref,
        fixed_income_etf_keys,
    )


def preliminary_classification(inst):
    if is_cepac(inst):
        return "OUTRO", "CEPAC"

    if is_royalty_security(inst):
        return "OUTRO", "ROYALTY_SECURITY"

    if is_bdr_unit(inst):
        return "BDR", "UNIT_BDR"

    return _original_preliminary_classification(inst)


def annotate_universe(instruments, ref):
    """
    Aplica as regras originais e remove duplicatas técnicas
    terminadas em G quando existe o ticker-base com o mesmo ISIN.
    """
    result = _original_annotate_universe(
        instruments,
        ref,
    )

    canonical_keys = {
        (
            base.upper(inst.get("ticker")),
            base.upper(inst.get("isin")),
        )
        for inst in result
        if inst.get("instrumento_canonico") is True
    }

    for inst in result:
        ticker = base.upper(inst.get("ticker")) or ""
        isin = base.upper(inst.get("isin"))

        if (
            inst.get("instrumento_canonico") is True
            and len(ticker) >= 2
            and ticker.endswith("G")
            and ticker[-2].isdigit()
            and (ticker[:-1], isin) in canonical_keys
        ):
            inst["instrumento_canonico"] = False
            inst["tipo_variante_b3"] = SUFFIX_G_VARIANT
            inst["ticker_canonico"] = ticker[:-1]
            inst["em_escopo_mestre"] = False

    return result


def _previous_financing_rate_counts(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            with ultima_referencia as (
                select max(data_referencia) as data_referencia
                from investimento.b3_instrumentos_snapshot
                where status_arquivo = 'Final'
            )
            select
                count(*) filter (
                    where s.instrumento_canonico = true
                ) as canonicos,
                count(*) filter (
                    where s.instrumento_canonico = true
                      and nullif(
                          s.data_inicio_negociacao,
                          date '9999-12-31'
                      ) is not null
                      and nullif(
                          s.data_inicio_negociacao,
                          date '9999-12-31'
                      ) <= s.data_referencia
                      and (
                          nullif(
                              s.data_fim_negociacao,
                              date '9999-12-31'
                          ) is null
                          or nullif(
                              s.data_fim_negociacao,
                              date '9999-12-31'
                          ) >= s.data_referencia
                      )
                      and (
                          nullif(
                              s.data_expiracao,
                              date '9999-12-31'
                          ) is null
                          or nullif(
                              s.data_expiracao,
                              date '9999-12-31'
                          ) >= s.data_referencia
                      )
                ) as confirmados
            from investimento.b3_instrumentos_snapshot s
            join ultima_referencia u
              on u.data_referencia = s.data_referencia
            where s.status_arquivo = 'Final'
              and upper(trim(s.ticker)) like 'TAXA%'
              and upper(
                  trim(coalesce(s.categoria_b3, ''))
              ) = 'SHARES'
              and upper(
                  trim(coalesce(s.nome_corporativo, ''))
              ) = 'TAXA DE FINANCIAMENTO'
            """
        )

        row = cur.fetchone() or (0, 0)

    return int(row[0] or 0), int(row[1] or 0)


def load_latest_valid_snapshot_profile(conn):
    profile = _original_load_latest_valid_snapshot_profile(
        conn
    )

    if profile is None:
        return None

    canonical_taxa, confirmed_taxa = (
        _previous_financing_rate_counts(conn)
    )

    if not canonical_taxa and not confirmed_taxa:
        return profile

    adjusted = dict(profile)

    adjusted["total_canonicos"] = max(
        0,
        adjusted["total_canonicos"] - canonical_taxa,
    )

    adjusted["total_canonicos_confirmados"] = max(
        0,
        adjusted["total_canonicos_confirmados"]
        - confirmed_taxa,
    )

    classes = dict(
        adjusted["canonicos_confirmados_por_classe"]
    )

    classes["ACAO"] = max(
        0,
        classes.get("ACAO", 0) - confirmed_taxa,
    )

    adjusted[
        "canonicos_confirmados_por_classe"
    ] = classes

    adjusted[
        "ajuste_baseline_taxa_financiamento"
    ] = {
        "canonicos_excluidos": canonical_taxa,
        "confirmados_excluidos": confirmed_taxa,
    }

    return adjusted


def validate_snapshot_sanity(
    instruments,
    status,
    previous_profile=None,
    ref=None,
):
    """
    Valida o arquivo integral em memória sem comparar seu
    volume bruto com o snapshot canônico reduzido.
    """
    comparable_profile = previous_profile

    if previous_profile is not None:
        comparable_profile = dict(previous_profile)

        comparable_profile[
            "total_registros_persistidos"
        ] = previous_profile.get("total_registros")

        comparable_profile["total_registros"] = None

    return _original_validate_snapshot_sanity(
        instruments,
        status,
        previous_profile=comparable_profile,
        ref=ref,
    )


def _correct_suffix_g_variants(
    conn,
    instruments,
    ref,
):
    variants = [
        inst
        for inst in instruments
        if inst.get("tipo_variante_b3")
        == SUFFIX_G_VARIANT
    ]

    if not variants:
        return 0

    updated = 0

    with conn.cursor() as cur:
        for inst in variants:
            cur.execute(
                """
                update investimento.ativos
                   set ativo = false,
                       instrumento_canonico = false,
                       tipo_variante_b3 = %s,
                       ticker_canonico = %s,
                       elegivel_analise = false,
                       status_validacao = 'NAO_CANONICO',
                       motivo_exclusao =
                           'Duplicata técnica com sufixo G e '
                           'mesmo ISIN do ticker canônico.',
                       fonte_validacao = %s,
                       validado_em = now(),
                       atividade_confirmada_b3 = false,
                       status_atividade_b3 =
                           'VARIANTE_NAO_CANONICA',
                       motivo_atividade_b3 =
                           'SUFFIX_G_DUPLICATE',
                       data_referencia_b3 = %s,
                       verificado_b3_em = now(),
                       atualizado_em = now()
                 where upper(trim(ticker)) = %s
                   and upper(trim(isin)) = %s
                """,
                (
                    SUFFIX_G_VARIANT,
                    inst.get("ticker_canonico"),
                    base.SOURCE_CODE,
                    ref,
                    base.upper(inst.get("ticker")),
                    base.upper(inst.get("isin")),
                ),
            )

            updated += cur.rowcount

    return updated


def save_snapshot(conn, instruments, ref):
    """
    Envia ao Supabase somente instrumentos canônicos.
    O arquivo integral existe apenas na memória temporária.
    """
    canonical_instruments = [
        inst
        for inst in instruments
        if inst.get("instrumento_canonico") is True
    ]

    if not canonical_instruments:
        raise base.SnapshotSanityError(
            "nenhum instrumento canônico encontrado"
        )

    base.progress(
        "redução antes do Supabase: "
        f"arquivo_integral={len(instruments)} "
        f"instrumentos_canonicos={len(canonical_instruments)} "
        f"descartados="
        f"{len(instruments) - len(canonical_instruments)}"
    )

    inserted = _original_save_snapshot(
        conn,
        canonical_instruments,
        ref,
    )

    corrected_variants = _correct_suffix_g_variants(
        conn,
        instruments,
        ref,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            delete from investimento.b3_instrumentos_snapshot
            where data_referencia <> %s
            """,
            (ref,),
        )

        removed_old_rows = cur.rowcount

    base.progress(
        "retenção do snapshot aplicada: "
        f"linhas_atuais={inserted} "
        f"linhas_antigas_removidas={removed_old_rows} "
        f"variantes_g_corrigidas={corrected_variants}"
    )

    return inserted


def main():
    base.canonical_decision = canonical_decision
    base.preliminary_classification = (
        preliminary_classification
    )
    base.annotate_universe = annotate_universe
    base.load_latest_valid_snapshot_profile = (
        load_latest_valid_snapshot_profile
    )
    base.validate_snapshot_sanity = (
        validate_snapshot_sanity
    )
    base.save_snapshot = save_snapshot

    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
