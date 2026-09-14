from __future__ import annotations

from src.jobs import validate_b3_instrumentos as base


FINANCING_RATE_NAME = "TAXA DE FINANCIAMENTO"
FINANCING_RATE_VARIANT = "FINANCING_RATE"

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
    Valida o arquivo integral em memória.

    O snapshot anterior contém apenas instrumentos canônicos.
    Portanto, ele não pode ser comparado com o volume bruto
    do arquivo integral da B3.
    """
    comparable_profile = previous_profile

    if previous_profile is not None:
        comparable_profile = dict(previous_profile)

        comparable_profile[
            "total_registros_persistidos"
        ] = previous_profile.get("total_registros")

        # Impede a comparação incorreta:
        # aproximadamente 2.400 canônicos armazenados
        # contra mais de 100 mil registros brutos em memória.
        comparable_profile["total_registros"] = None

    return _original_validate_snapshot_sanity(
        instruments,
        status,
        previous_profile=comparable_profile,
        ref=ref,
    )


def save_snapshot(conn, instruments, ref):
    """
    Envia ao Supabase somente instrumentos canônicos.

    O arquivo integral permanece apenas na memória temporária
    do GitHub Actions e não é armazenado no banco.
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
        f"descartados={len(instruments) - len(canonical_instruments)}"
    )

    inserted = _original_save_snapshot(
        conn,
        canonical_instruments,
        ref,
    )

    # A tabela representa o estado atual, e não um histórico diário.
    # A exclusão ocorre na mesma transação: se alguma etapa falhar,
    # todo o processo será desfeito automaticamente.
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
        f"linhas_antigas_removidas={removed_old_rows}"
    )

    return inserted


def main():
    base.canonical_decision = canonical_decision
    base.preliminary_classification = (
        preliminary_classification
    )
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
