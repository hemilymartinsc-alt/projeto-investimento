from __future__ import annotations

import math

from src.jobs import coletar_cvm_fundos as base


MIN_PERSISTED_RECORDS = 50
MAX_PERSISTED_RECORDS = 5_000
MIN_B3_CNPJ_MATCH_RATIO = 0.80

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
    pois o Supabase armazena somente o recorte relacionado
    aos fundos negociados na B3.
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


def _record_cnpjs(record):
    return {
        cnpj
        for cnpj in (
            record.get("cnpj_registro_normalizado"),
            record.get("cnpj_fundo_normalizado"),
            record.get("cnpj_classe_normalizado"),
        )
        if cnpj
    }


def _successful_match(match):
    method = match.get("metodo") or ""

    return method.startswith(
        (
            "A_",
            "B_",
            "C_",
        )
    )


def select_relevant_records(
    records,
    b3_funds,
):
    """
    Seleciona somente registros CVM relacionados aos fundos
    canônicos negociados na B3.

    A seleção prioriza os CNPJs e códigos oficiais da tabela
    de ativos. Depois, inclui os registros pertencentes à
    mesma hierarquia de fundo, classe e subclasse.
    """
    matcher = base.FundMatcher(records)

    matches = [
        matcher.match(fund)
        for fund in b3_funds
    ]

    successful_matches = [
        match
        for match in matches
        if _successful_match(match)
    ]

    b3_cnpjs = {
        cnpj
        for cnpj in (
            base.normalize_cnpj(fund.get("cnpj"))
            for fund in b3_funds
        )
        if cnpj
    }

    b3_codes = {
        code
        for code in (
            base.clean(fund.get("codigo_cvm"))
            for fund in b3_funds
        )
        if code
    }

    b3_names = {
        name
        for fund in b3_funds
        for name in (
            base.normalize_name(fund.get("nome")),
            base.normalize_name(
                fund.get("nome_pregao")
            ),
        )
        if name
    }

    matched_cnpjs = {
        cnpj
        for cnpj in (
            base.normalize_cnpj(
                match.get("cvm_cnpj")
            )
            for match in successful_matches
        )
        if cnpj
    }

    matched_codes = {
        code
        for code in (
            base.clean(match.get("cvm_codigo"))
            for match in successful_matches
        )
        if code
    }

    matched_names = {
        name
        for name in (
            base.normalize_name(
                match.get("cvm_nome")
            )
            for match in successful_matches
        )
        if name
    }

    target_cnpjs = b3_cnpjs | matched_cnpjs
    target_codes = b3_codes | matched_codes
    target_names = b3_names | matched_names

    direct_indexes = set()
    matched_b3_cnpjs = set()

    for index, record in enumerate(records):
        record_cnpjs = _record_cnpjs(record)

        cnpj_match = bool(
            record_cnpjs & target_cnpjs
        )

        record_code = base.clean(
            record.get("codigo_cvm")
        )
        code_match = bool(
            record_code
            and record_code in target_codes
        )

        record_name = record.get(
            "denominacao_normalizada"
        )
        name_match = bool(
            record_name
            and record_name in target_names
        )

        if not (
            cnpj_match
            or code_match
            or name_match
        ):
            continue

        direct_indexes.add(index)
        matched_b3_cnpjs.update(
            record_cnpjs & b3_cnpjs
        )

    minimum_cnpj_matches = math.ceil(
        len(b3_cnpjs)
        * MIN_B3_CNPJ_MATCH_RATIO
    )

    if (
        b3_cnpjs
        and len(matched_b3_cnpjs)
        < minimum_cnpj_matches
    ):
        raise base.CvmFundsError(
            "cobertura de CNPJ B3 insuficiente: "
            f"encontrados={len(matched_b3_cnpjs)} "
            f"mínimo={minimum_cnpj_matches} "
            f"cnpjs_b3={len(b3_cnpjs)}"
        )

    fund_ids = {
        records[index].get(
            "id_registro_fundo"
        )
        for index in direct_indexes
        if records[index].get(
            "id_registro_fundo"
        )
    }

    class_ids = {
        records[index].get(
            "id_registro_classe"
        )
        for index in direct_indexes
        if records[index].get(
            "id_registro_classe"
        )
    }

    selected = []
    seen = set()

    for index, record in enumerate(records):
        record_fund_id = record.get(
            "id_registro_fundo"
        )
        record_class_id = record.get(
            "id_registro_classe"
        )

        belongs_to_scope = bool(
            index in direct_indexes
            or (
                record_fund_id
                and record_fund_id in fund_ids
            )
            or (
                record_class_id
                and record_class_id in class_ids
            )
        )

        if not belongs_to_scope:
            continue

        key = (
            record.get("regime_regulatorio"),
            record.get("tipo_registro"),
            record.get(
                "identificador_oficial"
            ),
            record.get("fonte_arquivo"),
        )

        if key in seen:
            continue

        seen.add(key)
        selected.append(record)

    if len(selected) < MIN_PERSISTED_RECORDS:
        raise base.CvmFundsError(
            "recorte CVM insuficiente: "
            f"selecionados={len(selected)} "
            f"mínimo={MIN_PERSISTED_RECORDS} "
            f"fundos_b3={len(b3_funds)}"
        )

    if len(selected) > MAX_PERSISTED_RECORDS:
        raise base.CvmFundsError(
            "recorte CVM excessivo; "
            "gravação bloqueada: "
            f"selecionados={len(selected)} "
            f"máximo={MAX_PERSISTED_RECORDS}"
        )

    coverage = (
        len(matched_b3_cnpjs)
        / len(b3_cnpjs)
        if b3_cnpjs
        else 0.0
    )

    base.progress(
        "redução antes do Supabase: "
        f"arquivo_integral={len(records)} "
        f"fundos_b3={len(b3_funds)} "
        f"cnpjs_b3={len(b3_cnpjs)} "
        f"cnpjs_encontrados="
        f"{len(matched_b3_cnpjs)} "
        f"cobertura_cnpj={coverage:.2%} "
        f"correspondencias_diretas="
        f"{len(direct_indexes)} "
        f"registros_selecionados="
        f"{len(selected)} "
        f"descartados="
        f"{len(records) - len(selected)}"
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
