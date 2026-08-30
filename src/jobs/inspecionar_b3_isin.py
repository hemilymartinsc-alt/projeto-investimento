from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import re
from zipfile import BadZipFile, ZipFile

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
    encodings = ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1")
    for encoding in encodings:
        try:
            text = content.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
        # Evita aceitar latin-1 para conteúdo claramente binário.
        if encoding == "latin-1":
            sample = text[:4000]
            printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in sample)
            if sample and printable / len(sample) < 0.75:
                continue
        return text, encoding
    raise B3InspectionError("conteúdo não parece texto em encoding suportado")


def sanitize_preview(text: str) -> str:
    # Não publicar documentos completos nos logs.
    text = re.sub(r"(?<!\d)(\d{14})(?!\d)", lambda m: "***" + m.group(1)[-4:], text)
    text = re.sub(
        r"(?<!\d)(\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2})(?!\d)",
        "***CNPJ***",
        text,
    )
    return text.replace("\x00", "\\0")


def binary_diagnostics(label: str, content: bytes) -> dict:
    sample = content[:128]
    result = {
        "label": label,
        "bytes": len(content),
        "magic_hex": sample[:16].hex(),
        "starts_zip": content.startswith(b"PK\x03\x04"),
        "starts_gzip": content.startswith(b"\x1f\x8b"),
        "newline_count_first_64k": content[:65536].count(b"\n"),
        "carriage_return_count_first_64k": content[:65536].count(b"\r"),
    }
    progress("DIAGNOSTICO_BINARIO | " + json.dumps(result, sort_keys=True))
    return result


def detect_delimiter(text: str) -> str | None:
    lines = [line for line in text.splitlines() if line.strip()][:20]
    if not lines:
        return None
    sample = "\n".join(lines)
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,|\t").delimiter
    except csv.Error:
        candidates = [";", ",", "|", "\t"]
        counts = {
            delimiter: sum(line.count(delimiter) for line in lines[:5])
            for delimiter in candidates
        }
        delimiter, total = max(counts.items(), key=lambda item: item[1])
        return delimiter if total > 0 else None


def normalized_header(value: str) -> str:
    value = value.strip().upper()
    return re.sub(r"\s+", " ", value)


def mask_document(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def inspect_text(label: str, content: bytes) -> dict:
    text, encoding = decode_text(content)
    lines = [line for line in text.splitlines() if line.strip()]
    delimiter = detect_delimiter(text)

    if delimiter is None:
        previews = [sanitize_preview(line[:500]) for line in lines[:5]]
        result = {
            "label": label,
            "format": "TEXT_NO_DELIMITER",
            "bytes": len(content),
            "encoding": encoding,
            "line_count": len(lines),
            "first_line_length": len(lines[0]) if lines else 0,
            "previews": previews,
        }
        progress("TEXTO_SEM_DELIMITADOR | " + json.dumps(
            result, ensure_ascii=False, sort_keys=True
        ))
        return result

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    headers = list(reader.fieldnames or [])
    normalized_headers = [normalized_header(header) for header in headers]
    if not headers:
        raise B3InspectionError(f"{label}: arquivo sem cabeçalho")

    rows = 0
    isin_unique: set[str] = set()
    cnpj_nonempty = 0
    issuer_code_nonempty = 0
    samples: list[dict] = []
    header_by_normalized = {
        normalized_header(raw): raw for raw in headers if raw
    }

    isin_headers = [h for h in normalized_headers if "ISIN" in h]
    cnpj_headers = [h for h in normalized_headers if "CNPJ" in h]
    issuer_code_headers = [
        h for h in normalized_headers
        if "EMISSOR" in h and ("COD" in h or "CÓD" in h)
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
            for header in headers:
                value = (row.get(header) or "").strip()
                if "CNPJ" in normalized_header(header):
                    value = mask_document(value) or ""
                sample[header] = sanitize_preview(value[:120])
            samples.append(sample)

    result = {
        "label": label,
        "format": "DELIMITED_TEXT",
        "bytes": len(content),
        "encoding": encoding,
        "delimiter_repr": repr(delimiter),
        "headers": headers,
        "rows": rows,
        "isin_headers": isin_headers,
        "cnpj_headers": cnpj_headers,
        "issuer_code_headers": issuer_code_headers,
        "unique_isin": len(isin_unique),
        "cnpj_nonempty": cnpj_nonempty,
        "issuer_code_nonempty": issuer_code_nonempty,
        "samples": samples,
    }
    progress("ARQUIVO_DELIMITADO | " + json.dumps(
        result, ensure_ascii=False, sort_keys=True
    ))
    return result


def inspect_zip(label: str, content: bytes) -> dict:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            bad = archive.testzip()
            names = [name for name in archive.namelist() if not name.endswith("/")]
            result = {
                "label": label,
                "format": "ZIP",
                "bytes": len(content),
                "corrupt_member": bad,
                "members_count": len(names),
                "members": names[:50],
            }
            progress("ZIP | " + json.dumps(result, ensure_ascii=False, sort_keys=True))
            members = []
            for name in names[:25]:
                payload = archive.read(name)
                member_label = f"{label}::{name}"
                try:
                    members.append(inspect_payload(member_label, payload, nested=True))
                except Exception as exc:
                    diag = binary_diagnostics(member_label, payload)
                    diag["erro"] = str(exc)
                    members.append(diag)
            result["member_results"] = members
            return result
    except BadZipFile as exc:
        raise B3InspectionError(f"{label}: assinatura ZIP inválida") from exc


def inspect_payload(label: str, content: bytes, *, nested: bool = False) -> dict:
    binary_diagnostics(label, content)

    if content.startswith(b"PK\x03\x04"):
        if nested:
            return {
                "label": label,
                "format": "NESTED_ZIP_SKIPPED",
                "bytes": len(content),
            }
        return inspect_zip(label, content)

    if content.startswith(b"\x1f\x8b"):
        decompressed = gzip.decompress(content)
        progress(f"GZIP | label={label} bytes_descomprimidos={len(decompressed)}")
        return inspect_payload(f"{label}::gzip", decompressed, nested=nested)

    return inspect_text(label, content)


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
                "dataGeracao": geral_pt.get("dataGeracao"),
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
    return inspect_payload("B3_ISIN", file_response.content)


def inspect_fund_download(session: requests.Session, type_fund: int) -> dict:
    payload = {"typeFund": type_fund, "pageNumber": 1, "pageSize": 20}
    token = encode_payload(payload)
    response = request(session, FUNDS_DOWNLOAD_URL.format(token=token))
    progress(
        f"funds type={type_fund} content_type="
        f"{response.headers.get('Content-Type')} bytes={len(response.content)}"
    )
    return inspect_payload(f"B3_FUNDS_TYPE_{type_fund}", response.content)


def main() -> None:
    progress("iniciando inspeção v2 somente leitura das fontes oficiais B3")
    results = {}
    with requests.Session() as session:
        try:
            results["isin"] = inspect_isin(session)
        except Exception as exc:
            results["isin"] = {"erro": str(exc)}
            progress(f"ISIN: diagnóstico parcial concluído com erro: {exc}")

        for type_fund in (7, 27):
            key = f"funds_type_{type_fund}"
            try:
                results[key] = inspect_fund_download(session, type_fund)
            except Exception as exc:
                results[key] = {"erro": str(exc)}
                progress(f"funds type={type_fund}: diagnóstico parcial: {exc}")

    progress(
        "FIM_DIAGNOSTICO | "
        + json.dumps(results, ensure_ascii=False, sort_keys=True)[:12000]
    )

    # A sonda é diagnóstica: só falha se nenhuma das três fontes respondeu.
    successes = sum(1 for value in results.values() if "erro" not in value)
    if successes == 0:
        raise B3InspectionError("nenhuma fonte B3 pôde ser inspecionada")


if __name__ == "__main__":
    main()
