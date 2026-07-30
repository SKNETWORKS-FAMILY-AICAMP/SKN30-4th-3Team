from __future__ import annotations

import html
import io
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


DATA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DATA_DIR.parent
OUTPUT_DIR = DATA_DIR / "interim" / "all_NCPMS"
DEFAULT_ENDPOINT = "http://ncpms.rda.go.kr/npmsAPI/service"
# The existing repository collector and the current NCPMS search services use
# AA003. Keep it configurable because older manuals/examples sometimes show a
# different response-type code for individual services.
DEFAULT_SERVICE_TYPE = "AA003"
DEFAULT_PAGE_SIZE = 50
IMAGE_FIELD_PARTS = ("image", "img", "photo", "thumb")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def load_env() -> dict[str, str]:
    values = dict(__import__("os").environ)
    for path in (REPO_ROOT / ".env", DATA_DIR / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip("\"'"))
    return values


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(?:p|div|li|ul|ol)\b[^>]*>", "\n", text)
    text = re.sub(r"(?i)</?[a-z][^>]*>", " ", text)
    text = unicodedata.normalize("NFC", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def normalize_name(value: Any) -> str:
    return normalize_text(value).replace(" ", "")


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_element_to_obj(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return normalize_text(element.text)
    result: dict[str, Any] = {}
    for child in children:
        key = strip_namespace(child.tag)
        value = xml_element_to_obj(child)
        if key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result


def parse_payload(raw: bytes) -> Any:
    text = raw.decode("utf-8-sig", errors="replace").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return xml_element_to_obj(ET.fromstring(text))
        except ET.ParseError as exc:
            raise ValueError(f"NCPMS returned neither JSON nor XML: {text[:300]}") from exc


def download_bytes(url: str, *, timeout: float = 60.0, retries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Farmhani-NCPMS-Collector/1.0"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"Download failed after {retries} attempts: {url}") from last_error


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in archive.namelist():
        return []
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(archive.read(path))
    return [
        "".join(node.text or "" for node in item.iter(f"{namespace}t"))
        for item in root.findall(f"{namespace}si")
    ]


def _xlsx_cell_value(
    cell: ET.Element,
    *,
    shared_strings: list[str],
    namespace: str,
) -> str:
    value = cell.find(f"{namespace}v")
    if value is None:
        inline = cell.find(f"{namespace}is")
        if inline is None:
            return ""
        return normalize_text("".join(node.text or "" for node in inline.iter(f"{namespace}t")))
    text = value.text or ""
    if cell.get("t") == "s" and text:
        return normalize_text(shared_strings[int(text)])
    return normalize_text(text)


def extract_ncpms_crop_codes_from_manual_zip(manual_zip: bytes) -> list[dict[str, str]]:
    """Extract the official cropCode sheet without requiring openpyxl.

    The public NCPMS manual ZIP contains an XLSX code list. We deliberately
    identify the crop sheet by its FC/VC/etc. code pattern instead of relying
    on a localized filename or fixed worksheet number.
    """

    with zipfile.ZipFile(io.BytesIO(manual_zip)) as outer:
        xlsx_names = [name for name in outer.namelist() if name.lower().endswith(".xlsx")]
        if not xlsx_names:
            raise ValueError("The NCPMS manual ZIP contains no XLSX code list.")
        xlsx_bytes = outer.read(xlsx_names[0])

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    # Most codes are two letters plus six digits, but the official sheet also
    # contains alphanumeric suffixes such as FL0121A1 and FG0156WB.
    code_pattern = re.compile(r"^[A-Z]{2}[A-Z0-9]{6}$")
    best_rows: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as workbook:
        shared_strings = _xlsx_shared_strings(workbook)
        sheet_names = sorted(
            name
            for name in workbook.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        for sheet_name in sheet_names:
            root = ET.fromstring(workbook.read(sheet_name))
            rows: list[dict[str, str]] = []
            for row in root.iter(f"{namespace}row"):
                values: dict[str, str] = {}
                for cell in row.findall(f"{namespace}c"):
                    reference = cell.get("r") or ""
                    column = re.sub(r"\d", "", reference)
                    values[column] = _xlsx_cell_value(
                        cell,
                        shared_strings=shared_strings,
                        namespace=namespace,
                    )
                code = values.get("A", "")
                name = values.get("B", "")
                if code_pattern.fullmatch(code) and name:
                    rows.append({"crop_code": code, "crop_name": name})
            if len(rows) > len(best_rows):
                best_rows = rows
    if not best_rows:
        raise ValueError("No crop codes were found in the NCPMS XLSX code list.")
    return best_rows


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def extract_records(payload: Any, signature_fields: Iterable[str]) -> list[dict[str, Any]]:
    signatures = set(signature_fields)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in _walk(payload):
        if not signatures.intersection(value):
            continue
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if encoded in seen:
            continue
        seen.add(encoded)
        records.append(value)
    return records


def find_first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (dict, list)):
            continue
        text = normalize_text(value)
        if text:
            return text
    return ""


def remove_image_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [remove_image_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: remove_image_fields(item)
        for key, item in value.items()
        if not any(part in key.lower() for part in IMAGE_FIELD_PARTS)
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_no}") from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def dedupe_rows(rows: Iterable[dict[str, Any]], key_fields: Iterable[str]) -> list[dict[str, Any]]:
    fields = tuple(key_fields)
    result: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(normalize_text(row.get(field)) for field in fields)
        if not any(key):
            continue
        result[key] = row
    return list(result.values())


@dataclass
class NcpmsClient:
    api_key: str
    endpoint: str = DEFAULT_ENDPOINT
    service_type: str = DEFAULT_SERVICE_TYPE
    timeout: float = 30.0
    delay: float = 0.2
    retries: int = 3

    def request(self, service_code: str, params: dict[str, Any] | None = None) -> Any:
        query: dict[str, Any] = {
            "apiKey": self.api_key,
            "serviceCode": service_code,
            "serviceType": self.service_type,
        }
        query.update({key: value for key, value in (params or {}).items() if value not in (None, "")})
        url = f"{self.endpoint}?{urllib.parse.urlencode(query)}"
        safe_url = f"{self.endpoint}?{urllib.parse.urlencode({k: v for k, v in query.items() if k != 'apiKey'})}"
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Farmhani-NCPMS-Collector/1.0"},
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = parse_payload(response.read())
                self._raise_api_error(payload, safe_url)
                if self.delay:
                    time.sleep(self.delay)
                return payload
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"NCPMS request failed after {self.retries} attempts: {safe_url}") from last_error

    @staticmethod
    def _raise_api_error(payload: Any, safe_url: str) -> None:
        for row in _walk(payload):
            code = find_first(row, "errorCode", "errCode", "resultCode")
            message = find_first(row, "errorMsg", "errMsg", "resultMsg", "message")
            if code.upper().startswith("ERR") or (code and code not in {"00", "0", "SUCCESS"}):
                raise ValueError(f"NCPMS API error {code}: {message} ({safe_url})")

    def paged_records(
        self,
        service_code: str,
        *,
        params: dict[str, Any] | None,
        signature_fields: Iterable[str],
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = 10_000,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        previous_page_fingerprint = ""
        for page in range(max_pages):
            request_params = dict(params or {})
            request_params["displayCount"] = page_size
            request_params["startPoint"] = page * page_size + 1
            payload = self.request(service_code, request_params)
            page_rows = extract_records(payload, signature_fields)
            fingerprint = json.dumps(page_rows, ensure_ascii=False, sort_keys=True)
            if not page_rows or fingerprint == previous_page_fingerprint:
                break
            previous_page_fingerprint = fingerprint
            records.extend(page_rows)
            if len(page_rows) < page_size:
                break
        return records


def build_client(args: Any) -> NcpmsClient:
    env = load_env()
    api_key = getattr(args, "api_key", "") or env.get("NCPMS_API_KEY", "")
    if not api_key:
        raise RuntimeError("NCPMS_API_KEY is required. Put it in the root .env; do not commit it.")
    return NcpmsClient(
        api_key=api_key,
        endpoint=args.endpoint,
        service_type=args.service_type,
        timeout=args.timeout,
        delay=args.delay,
        retries=args.retries,
    )


def add_client_arguments(parser: Any) -> None:
    parser.add_argument("--api-key", default="", help="Prefer NCPMS_API_KEY in .env.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--service-type", default=DEFAULT_SERVICE_TYPE)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=10_000)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")


def progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
