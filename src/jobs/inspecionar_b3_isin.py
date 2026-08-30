from __future__ import annotations

import base64
import csv
import io
import json
import re

import requests


TEXT_DOWNLOAD_URL = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "isinProxy/IsinCall/GetTextDownload/"
)
FILE_DOWNLOAD_URL = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "isinProxy/IsinCall/GetFileDownload/{token}"
)
FUNDS_DOWNLOAD_URL = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "fundsProxy/fundsCall/GetListFundDownload/{token}"
)
HTTP_TIMEOUT = (5, 45)
USER_AGENT = "projeto-investimento/1.0 (auditoria de dados publicos B3)"


class B3InspectionError(RuntimeError):
    pass


def progress(message: str) -> None:
    print(f"B3 inspeção: {message}", flush=True)


def encode_payload(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return base64.b64encode(raw).decode("ascii")


def request(session: requests.Session, url: str) -> requests.Response:
    response = session.get(
        url,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://sistemaswebb3-listados.b3.com.br/",
        },
    )
    response.raise_for_status()
    if not response.content:
        raise B3InspectionError(f"resposta vazia: {url}")
    return response


def decode_text(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(encoding, errors="strict"), encoding
        except UnicodeDecodeError:
            continue
    raise B3InspectionError("não foi possível identificar encoding do arquivo")


def detect_delimiter(sample: str) -> str:
    first_lines = "\n".join(sample.splitlines()[:10])
    try:
        dialect = csv.Sniffer().sniff(first_lines, delimiters=";,|\t")
        return dialect.delimiter
    except csv.Error:
        candidates = [";", ",", "|", "\t"]
        first = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {delimiter: first.count(delimiter) for delimiter in candidates}
        delimiter, total = max(counts.items(), key=lambda item: item[1])
        if total <= 0:
            raise B3InspectionError("delimitador não identificado")
        return delimiter


def normalized_header(value: str) -> str:
    value = value.strip().upper()
    value = re.sub(r"\s+", " ", value)
    return value


def mask_document(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def inspect_delimited_file(label: str, content: bytes) -> dict:
    text, encoding = decode_text(content)
    delimiter = detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    headers = [normalized_header(header) for header in reader.fieldnames or []]
    if not headers:
        raise B3InspectionError(f"{label}: arquivo sem cabeçalho")

    rows = 0
    isin_unique: set[str] = set()
    cnpj_nonempty = 0
    issuer_code_nonempty = 0
    samples: list[dict] = []
    header_by_normalized = {
        normalized_header(raw): raw for raw in reader.fieldnames or [] if raw
    }

    isin_headers = [h for h in headers if "ISIN" in h]
    cnpj_headers = [h for h in headers if "CNPJ" in h]
    issuer_code_headers = [
        h for h in headers if "EMISSOR" in h and ("COD" in h or "CÓD" in h)
    ]

    for row in reader:
        rows += 1
        if isin_headers:
            raw_key = header_by_normalized[isin_headers[0]]
            value = (row.get(raw_key) or "").strip().upper()
            if value:
                isin_unique.add(value)
        if cnpj_headers:
            raw_key = header_by_normalized[cnpj_headers[0]]
            if (row.get(raw_key) or "").strip():
                cnpj_nonempty += 1
        if issuer_code_headers:
            raw_key = header_by_normalized[issuer_code_headers[0]]
            if (row.get(raw_key) or "").strip():
                issuer_code_nonempty += 1
        if len(samples) < 3:
            sample = {}
            for header in reader.fieldnames or []:
                value = (row.get(header) or "").strip()
                if "CNPJ" in normalized_header(header):
                    value = mask_document(value) or ""
                sample[header] = value[:120]
            samples.append(sample)

    result = {
        "label": label,
        "bytes": len(content),
        "encoding": encoding,
        "delimiter_repr": repr(delimiter),
        "headers": list(reader.fieldnames or []),
        "rows": rows,
        "isin_headers": isin_headers,
        "cnpj_headers": cnpj_headers,
        "issuer_code_headers": issuer_code_headers,
        "unique_isin": len(isin_unique),
        "cnpj_nonempty": cnpj_nonempty,
        "issuer_code_nonempty": issuer_code_nonempty,
        "samples": samples,
    }
    progress(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def inspect_isin(session: requests.Session) -> dict:
    metadata_response = request(session, TEXT_DOWNLOAD_URL)
    try:
        metadata = metadata_response.json()
    except ValueError as exc:
        raise B3InspectionError("GetTextDownload não retornou JSON") from exc

    geral_pt = metadata.get("geralPt")
    if not isinstance(geral_pt, dict) or not geral_pt.get("id"):
        raise B3InspectionError(
            "layout de GetTextDownload mudou: geralPt.id não encontrado"
        )

    identifier = geral_pt["id"]
    token = encode_payload(identifier)
    file_response = request(session, FILE_DOWNLOAD_URL.format(token=token))
    progress(
        "ISIN download "
        + json.dumps(
            {
                "metadata_keys": sorted(metadata.keys()),
                "geralPt_keys": sorted(geral_pt.keys()),
                "content_type": file_response.headers.get("Content-Type"),
                "content_disposition": file_response.headers.get(
                    "Content-Disposition"
                ),
                "bytes": len(file_response.content),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return inspect_delimited_file("B3_ISIN", file_response.content)


def inspect_fund_download(session: requests.Session, type_fund: int) -> dict:
    payload = {"typeFund": type_fund, "pageNumber": 1, "pageSize": 20}
    token = encode_payload(payload)
    response = request(session, FUNDS_DOWNLOAD_URL.format(token=token))
    progress(
        f"funds type={type_fund} content_type="
        f"{response.headers.get('Content-Type')} bytes={len(response.content)}"
    )
    return inspect_delimited_file(
        f"B3_FUNDS_TYPE_{type_fund}", response.content
    )


def main() -> None:
    progress("iniciando inspeção somente leitura das fontes oficiais B3")
    with requests.Session() as session:
        isin = inspect_isin(session)
        fund_results = {}
        for type_fund in (7, 27):
            try:
                fund_results[type_fund] = inspect_fund_download(
                    session, type_fund
                )
            except Exception as exc:
                fund_results[type_fund] = {"erro": str(exc)}
                progress(
                    f"funds type={type_fund} indisponível para inspeção: {exc}"
                )

    summary = {
        "isin_rows": isin["rows"],
        "isin_unique": isin["unique_isin"],
        "isin_cnpj_nonempty": isin["cnpj_nonempty"],
        "isin_issuer_code_nonempty": isin["issuer_code_nonempty"],
        "fund_types": {
            str(key): {
                "rows": value.get("rows"),
                "headers": value.get("headers"),
                "erro": value.get("erro"),
            }
            for key, value in fund_results.items()
        },
    }
    progress(
        "SUCESSO | " + json.dumps(summary, ensure_ascii=False, sort_keys=True)
    )


if __name__ == "__main__":
    main()
