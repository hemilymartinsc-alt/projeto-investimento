from __future__ import annotations

from src.jobs import coletar_cvm_fundos as base


MIN_PERSISTED_RECORDS = 500
MAX_PERSISTED_RECORDS = 10_000

_original_validate_dataset_sanity = (
    base.validate_dataset_sanity
)
_original_replace_current_snapshot = (
    base.replace_current_snapshot
)


def validate_dataset_sanity(
    records,
    previous_profile=None,
):
    """
    Valida integralmente os arquivos oficiais da CVM.

    O total bruto não é comparado com o snapshot anterior,
    pois o Supabase armazena somente o recorte necessário
    para os fundos negociados na B3.
    """
    comparable_profile = previous_profile

    if previous_profile is not None:
        comparable_profile = dict(previous_profile)
        comparable_profile[
            "total_registros_persistidos"
        ] = previous_profile.get("total_registros")
        comparable_profile["total_registros"] = None

    return _original_validate_dataset_sanity(
        records,
        comparable_profile,
    )


def _matches_candidate(record, match):
    match_type = match.get("cvm_tipo_registro")

    if (
        match_type
        and record.get("tipo_registro") != match_type
    ):
        return False

    match_cnpj = match.get("cvm_cnpj")
    if (
        match_cnpj
        and record.get("cnpj_registro_normalizado")
        == match_cnpj
    ):
        return True

    match_code = base.clean(match.get("cvm_codigo"))
    if (
        match_code
        and base.clean(record.get("codigo_cvm"))
        == match_code
    ):
        return True

    match_name = base.normalize_name(
        match.get("cvm_nome")
    )
    if (
        match_name
        and record.get("denominacao_normalizada")
        == match_name
    ):
        return True

    return False


def select_relevant_records(
    records,
    b3_funds,
):
    """
    Seleciona os registros CVM relacionados aos fundos
    canônicos negociados na B3 e preserva sua hierarquia
    de fundo, classe e subclasse.
    """
    matcher = base.FundMatcher(records)

    matches = [
        matcher.match(fund)
        for fund in b3_funds
    ]

    direct_indexes = {
        index
        for index, record in enumerate(records)
        if any(
            _matches_candidate(record, match)
            for match in matches
            if match.get("cvm_nome")
            or match.get("cvm_cnpj")
            or match.get("cvm_codigo")
        )
    }

    fund_ids = {
        records[index].get("id_registro_fundo")
        for index in direct_indexes
        if records[index].get("id_registro_fundo")
    }

    class_ids = {
        records[index].get("id_registro_classe")
        for index in direct_indexes
        if records[index].get("id_registro_classe")
    }

    selected = []
    seen = set()

    for index, record in enumerate(records):
        belongs_to_scope = bool(
            index in direct_indexes
            or (
                record.get("id_registro_fundo")
                and record.get("id_registro_fundo")
                in fund_ids
            )
            or (
                record.get("id_registro_classe")
                and record.get("id_registro_classe")
                in class_ids
            )
        )

        if not belongs_to_scope:
            continue

        key = (
            record.get("regime_regulatorio"),
            record.get("tipo_registro"),
            record.get("identificador_oficial"),
            record.get("fonte_arquivo"),
        )

        if key in seen:
            continue

        seen.add(key)
        selected.append(record)

    minimum_expected = max(
        MIN_PERSISTED_RECORDS,
        len(b3_funds) // 2,
    )

    if len(selected) < minimum_expected:
        raise base.CvmFundsError(
            "recorte CVM insuficiente: "
            f"selecionados={len(selected)} "
            f"mínimo={minimum_expected} "
            f"fundos_b3={len(b3_funds)}"
        )

    if len(selected) > MAX_PERSISTED_RECORDS:
        raise base.CvmFundsError(
            "recorte CVM excessivo; gravação bloqueada: "
            f"selecionados={len(selected)} "
            f"máximo={MAX_PERSISTED_RECORDS}"
        )

    base.progress(
        "redução antes do Supabase: "
        f"arquivo_integral={len(records)} "
        f"fundos_b3={len(b3_funds)} "
        f"registros_selecionados={len(selected)} "
        f"descartados={len(records) - len(selected)}"
    )

    return selected


def replace_current_snapshot(
    conn,
    records,
):
    b3_funds = base.load_b3_funds(conn)

    if not b3_funds:
        raise base.CvmFundsError(
            "nenhum fundo canônico da B3 encontrado"
        )

    selected = select_relevant_records(
        records,
        b3_funds,
    )

    return _original_replace_current_snapshot(
        conn,
        selected,
    )


def main():
    base.validate_dataset_sanity = (
        validate_dataset_sanity
    )
    base.replace_current_snapshot = (
        replace_current_snapshot
    )

    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
