import re
from html import unescape
from pathlib import Path



_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-", "=": "-"})


def iter_spec_pdfs(specs_dir: Path) -> list[Path]:
    """Return specification PDFs at any depth in stable relative-path order."""
    return sorted(
        (path for path in specs_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.relative_to(specs_dir).as_posix(),
    )


def normalize_valve_model(value: str) -> str:
    candidate = re.sub(r"\s+", "", value.upper().translate(_DASHES))
    if re.fullmatch(r"\d{2}-[0-9A-Z]{5,}", candidate):
        return candidate
    if re.fullmatch(r"\d{2}[0-9A-Z]{5,}", candidate):
        return f"{candidate[:2]}-{candidate[2:]}"
    return ""


def _flatten_markup(text: str) -> str:
    """Turn local document/table OCR HTML into line-oriented parser text."""
    if not re.search(r"<\/?(?:html|body|table|tbody|tr|td|div|p)\b", text, re.I):
        return text
    flattened = re.sub(r"</td\s*>\s*<td[^>]*>", "\n", text, flags=re.I)
    flattened = re.sub(r"<br\s*/?>", "\n", flattened, flags=re.I)
    flattened = re.sub(r"</(?:tr|p|div|table|tbody|body|html)\s*>", "\n", flattened, flags=re.I)
    flattened = re.sub(r"<[^>]+>", " ", flattened)
    return unescape(flattened)


def _parser_text(text: str) -> str:
    flattened = _flatten_markup(text)
    return flattened if flattened != text else text


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", _parser_text(text)).strip()


def _first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return ""


# These aliases identify fields, not arbitrary numeric strings.  Keep the same
# boundaries for parsing and for detecting nonblank values needing review.
_SPEC_LABELS = {
    "actuator_model_size": r"\bModel\s*/\s*Size(?:\s*/\s*Fail\s*Position)?",
    "actuator_model": r"\bActuator\s+Model(?:\s*(?:Number|No\.?))?",
    "actuator_size": r"\bActuator\s+Size",
    "positioner_model": r"\bPositioner\s+Model(?:\s*(?:Number|No\.?))?",
    "model_no": r"\b(?:Valve\s+)?Model\s*(?:Number|No\.?)",
    "manufacturer": r"\bManufacturer",
    "valve_size": r"\b(?:Valve\s+Size|Body\s+Size|Size\s+in)",
    "pressure_class": r"\b(?:Pressure\s+Class|Rating(?:\s+(?:ASME|Class))?)",
    "rated_cv": r"\bRated\s+Cv",
    "leakage_class": r"\bLeakage\s+(?:Class|Spec\.?)",
    "fail_position": r"\bFail\s+Position",
    "supply_pressure": r"\bSupply\s+Pressure",
    "bench_range": r"\b(?:Bench|Spring)\s+Range(?:\s*/\s*Travel)?",
    "actuator_type": r"\bActuator\s+Type\.?",
    "body_type": r"\bBody\s+Type",
    "end_connection": r"\bEnd\s+Connection",
    "flow_action": r"\bFlow\s+(?:Action|to)",
    "design_pressure": r"\bDesign\s+Press(?:ure)?\.?",
    "design_temperature": r"\bDesign\s+Temp(?:erature)?\.?",
    "serial_number": r"\bSerial\s*(?:Number|No\.?)",
    "_boundary": (
        r"\b(?:Tag(?:\s*(?:Number|No\.?))?|Test\s+Status|Shutoff\s+Pressure|"
        r"Air\s+(?:Set|Action)|Positioner\s+Type|Body\s+Studs|Trim\s+(?:Size|Form)|"
        r"Characteristic|Rated\s+Travel|Travel\s*/\s*Stroke\s+Time|Handwheel|"
        r"Mounting\s*/\s*Installation)"
    ),
}
_SPEC_LABEL_PATTERN = re.compile(
    "|".join(rf"(?P<{key}>{pattern})(?![\w])" for key, pattern in _SPEC_LABELS.items()),
    re.I,
)
_SPEC_BLANK_VALUES = {"", "-", "--", "—", "n/a", "n.a.", "not provided", "未提供", "无数据", "栏位为空"}


def specification_source_values(text: str) -> dict[str, str]:
    """Return labelled raw values, retaining unsupported values for review.

    Stop at the next known field or physical value line, never search ahead for
    an arbitrary number.  Blank labels and explicit empty placeholders stay absent.
    """
    text = _parser_text(text)
    matches = list(_SPEC_LABEL_PATTERN.finditer(text))
    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.lastgroup or ""
        if key == "_boundary":
            continue
        # Unqualified Model No/Number must not be taken from an accessory row.
        prefix = text[max(0, text.rfind("\n", 0, match.start()) + 1):match.start()]
        if key == "model_no" and not re.match(r"Valve\b", match.group(0), re.I):
            previous = matches[index - 1] if index else None
            owned_type_row = (
                previous is not None
                and re.fullmatch(r"(?:Positioner|Actuator)\s+Type\.?", previous.group(0), re.I)
                and "\n" not in text[previous.end():match.start()]
            )
            if owned_type_row or re.search(r"\b(?:Positioner|Actuator)\s*$", prefix, re.I):
                continue
        if key == "actuator_model_size" and re.search(r"\b(?:Valve|Positioner)\s*$", text[:match.start()], re.I):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.end():end]
        tail = segment.lstrip(" \t\r\n:：|[_")
        raw = tail.splitlines()[0].strip(" \t|]") if tail else ""
        # A different labelled row is not the value of the preceding blank row.
        if segment.lstrip(" \t:：|[_").startswith(("\n", "\r")) and re.match(r"[^:：]{1,80}[:：]", raw):
            continue
        if key == "actuator_model_size":
            # Slash characters inside the explicit N/A placeholder are not
            # column separators.  Evaluate each column independently so a
            # printed fail position cannot invent an actuator model or size.
            protected = re.sub(r"\bN/A\b", lambda item: item.group(0).replace("/", "∕"), raw, flags=re.I)
            parts = [part.strip().replace("∕", "/") for part in protected.split("/")]
            if len(parts) in {2, 3}:
                for component, value in zip(("actuator_model", "actuator_size", "fail_position"), parts):
                    if value.casefold() not in _SPEC_BLANK_VALUES:
                        values.setdefault(component, value)
                continue
        if raw.casefold() not in _SPEC_BLANK_VALUES:
            values.setdefault(key, raw)
    return values


def _parse_labelled_fields(text: str) -> dict[str, str]:
    """Parse verified aliases using complete bounded values and explicit units."""
    raw = specification_source_values(text)
    fields: dict[str, str] = {}
    manufacturer = raw.get("manufacturer", "")
    if re.fullmatch(r"[A-Za-z][A-Za-z .&()-]{1,80}", manufacturer):
        fields["manufacturer"] = manufacturer
    model = raw.get("model_no", "")
    model_token = re.sub(r"\s*[-=‐‑‒–—−]\s*", "-", model)
    # A separate trailing word is table/annotation text, not a model suffix.
    normalized = "" if re.search(r"\s+[A-Z]", model_token, re.I) else normalize_valve_model(model_token)
    if normalized:
        fields["model_no"] = normalized
    elif re.fullmatch(r"\d{5}", model):
        # A labelled numeric model is literal data, not a series-prefix mapping.
        fields["model_no"] = model
    size = raw.get("valve_size", "")
    number = r"\d+(?:\.\d+|-\d+/[1-9]\d*|\s+\d+/[1-9]\d*|/[1-9]\d*)?"
    size_match = re.fullmatch(rf"({number})\s*(in(?:ch(?:es)?)?\.?|[\"″]|mm)", size, re.I)
    dn_match = re.fullmatch(r"DN\s*(\d+)", size, re.I)
    if size_match:
        unit = "mm" if size_match.group(2).lower() == "mm" else "in"
        fields["valve_size"] = f"{size_match.group(1)} {unit}"
    elif dn_match:
        fields["valve_size"] = f"DN {dn_match.group(1)}"
    pressure_class = raw.get("pressure_class", "")
    if re.fullmatch(r"(?:(?:ANSI|ASME)\s*(?:(?:CL|CLASS)\s*)?|(?:CL|CLASS|PN)\s*)\d+", pressure_class, re.I):
        fields["pressure_class"] = pressure_class
    cv = raw.get("rated_cv", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", cv):
        fields["rated_cv"] = cv
    leakage = raw.get("leakage_class", "")
    if re.fullmatch(r"(?:CLASS\s*)?(?:I|II|III|IV|V|VI|[1-6])", leakage, re.I):
        fields["leakage_class"] = leakage
    fail = raw.get("fail_position", "")
    if re.fullmatch(r"Open|Close|Closed|Lock", fail, re.I):
        fields["fail_position"] = fail
    supply = raw.get("supply_pressure", "")
    if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:psi(?:\s*[ga])?|bar(?:\s*[ga])?|[Mk]?Pa(?:\s*[ga])?)", supply, re.I):
        fields["supply_pressure"] = supply
    return fields


def _actuator_model_size_fail(text: str) -> tuple[str, str, str]:
    """Read the labelled actuator model/size row without using loose numbers."""
    clean = _compact(text)
    matches = re.finditer(
        r"\bModel\s*/\s*Size"
        r"(?:\s*/\s*Fail\s*Position)?(?:\s*[:：]\s*|\s+)"
        r"([0-9]{2}|[0-9][A-Z])\s*/\s*"
        r"(#?[0-9]{1,3}(?:\([0-9]{1,3}\))?)"
        r"(?:\s*/\s*(Open|Close|Closed|Lock))?\b",
        clean,
        re.I,
    )
    match = next((item for item in matches if not re.search(r"\b(?:Valve|Positioner)\s*$", clean[:item.start()], re.I)), None)
    if not match:
        return "", "", ""
    return (
        match.group(1).upper(),
        match.group(2).upper(),
        (match.group(3) or "").title(),
    )


def detect_spec_format(text: str) -> str:
    clean = _compact(text)
    if re.search(r"CONTROL\s+VALVE\s+SPECIFICATIONS", clean, re.I):
        return "control_valve_specifications"
    if re.search(r"ASSEMBLY\s*QUALITY\s*PLAN", clean, re.I):
        return "assembly_quality_plan"
    return ""


def _parse_generic_fields(text: str) -> dict[str, str]:
    clean = _compact(text)
    clean = re.sub(r"(?<=\w)\s*[:：]\s*", " ", clean)
    actuator_model, actuator_size, actuator_fail = _actuator_model_size_fail(text)
    sizing_selection = bool(re.search(r"Sizing\s+and\s+Selection\s+Report", clean, re.I))
    model_patterns = [
            r"Masoneilan Valves\s+([0-9A-Z-]+)",
            r"\b([0-9]{2}-[0-9A-Z]+)\s+Masoneilan Valves",
            r"\b([0-9]{2}-[0-9A-Z]+)\s+\[VCM",
            r"\bStatus\s+Ordered\s+Model\s+([0-9]{2}-[0-9A-Z]+)\b",
            r"\bModel\s+([0-9]{2}-[0-9A-Z]{5,})\b",
    ]
    normalized_model = ""
    for pattern in model_patterns:
        for match in re.finditer(pattern, clean, re.I):
            if re.match(r"Model\b", match.group(0), re.I) and re.search(
                r"\b(?:Actuator|Positioner)\s*$", clean[:match.start()], re.I
            ):
                continue
            normalized_model = normalize_valve_model(match.group(1))
            if normalized_model:
                break
        if normalized_model:
            break
    manufacturer = _first(
        _parser_text(text),
        [
            r"\b(Masoneilan|Baker\s+Hughes|Fisher|Emerson|Flowserve|Metso|Samson)\s+Valves?\b",
            r"\bManufacturer[ \t]*[:：]?[ \t]*([A-Za-z][A-Za-z ]{2,})\b",
        ],
    )
    fields = {
        "manufacturer": manufacturer,
        "model_no": normalized_model,
        "valve_size": _first(
            clean,
            [
                r"Size\s+in\s+\**\s*[0-9.]+\s+\**\s*([0-9.]+)\s+\**\s*[0-9.]+\s+Rating",
                r"Size\s+\(In\s*x\s*Body\s*x\s*Out\)\s+[0-9.]+\s*x\s*([0-9.]+)\s*x\s*[0-9.]+",
                r"In\s*x\s*Body\s*x\s*Out\s+Size\s+[0-9.]+\s*x\s*([0-9.]+)\s*x\s*[0-9.]+\s*In\b",
                r"Size\s+in\s+([0-9]+(?:\s*x\s*[0-9]+(?:\s*x\s*[0-9]+)?)?)",
            ],
        ),
        "pressure_class": _first(clean, [r"Rating\s+ASME\s+(CL\s*[0-9]+)", r"\b(CL\s*[0-9]+)\b"]),
        "end_connection": _first(
            clean,
            [r"End Connection(?:\s+CL\s*\d+)?\s+([A-Z]{1,3}\s+Flanges)"],
        ),
        "design_pressure": _first(clean, [r"Design Press\s*[:：]?\s*([0-9.]+\s*MPa\s*g?)", r"Design Press\s*[:：]?\s*([0-9.]+\s*MPag)"]),
        "design_temperature": _first(clean, [r"Design Temp\s*[:：]?\s*([-0-9/ .]+deg\s*C)", r"Design Temp\s*[:：]?\s*([-0-9/ .]+degC)"]),
        "body_material": _first(clean, [r"Material\s+([A-Z][0-9]{3}\s+gr\s+[A-Z0-9() .-]+?)\s+Body Studs"]),
        "rated_cv": _first(clean, [r"Rated Cv\s+([0-9.]+)"]),
        "leakage_class": _first(clean, [r"Leakage Class\s+([IVX]+)"]),
        "flow_action": _first(clean, [r"Flow Action\s+(Flow to (?:Open|Close))"]),
        "actuator_model": actuator_model
        or _first(clean, [r"\bModel\s+([0-9]{2})\s+Shutoff Pressure"]),
        "actuator_size": actuator_size
        or _first(
            clean,
            [
                r"\bModel\s+[0-9]{2}\s+Shutoff Pressure.{0,100}?\bType\s+"
                r"(?:Spring-diaphragm|Piston \(Cylinder\)).{0,180}?\bSize\s+([0-9A-Z#()]+)"
            ],
        ),
        "actuator_type": _first(clean, [r"Type\s+((?:Spring-diaphragm|Piston \(Cylinder\)))"]),
        "bench_range": _first(
            clean,
            [
                r"Bench Range(?:\s*/\s*Travel)?\s+([0-9.]+\s*[-‐‑‒–—−]\s*[0-9.]+\s*psi\s*g?)",
                r"\b([0-9.]+\s*[-‐‑‒–—−]\s*[0-9.]+\s*psi\s*g?)\b(?=\s+Air to (?:Open|Close))",
            ],
        ).translate(_DASHES),
        "supply_pressure": _first(clean, [r"Supply Pressure\s+([0-9.]+\s*psi\s*g?)"]),
        "actuator_travel": _first(
            clean,
            [r"Travel\s*/\s*Stroke Time\s+([0-9.]+\s*deg)\s*/"],
        ),
        "fail_position": actuator_fail
        or _first(
            clean,
            [
                r"Fail Position\s+(Open|Close|Closed|Lock)\b",
                r"\bAir to (?:Open|Close)\s+(Open|Close|Closed|Lock)\b",
            ],
        ),
    }
    if fields.get("valve_size") and re.search(
        r"In\s*x\s*Body\s*x\s*Out\s+Size", clean, re.I
    ):
        fields["valve_size"] = f"{fields['valve_size']} in"
    if sizing_selection:
        flow_leakage = re.search(
            r"Flow\s+Direction\s*/\s*Leakage\s+Class\s+"
            r"Flow\s+to\s+(Open|Close)\s*/\s*([IVX]+)\b",
            clean,
            re.I,
        )
        actuator_model_size = _actuator_model_size_fail(text)
        spring_travel = re.search(
            r"Spring\s+Range\s*/\s*Travel\s+"
            r"([0-9.]+\s*[-‐‑‒–—−]\s*[0-9.]+\s*bar\s*g?)\s*/\s*"
            r"([0-9.]+\s*deg)\b",
            clean,
            re.I,
        )
        if flow_leakage:
            fields["flow_action"] = f"Flow to {flow_leakage.group(1).title()}"
            fields["leakage_class"] = flow_leakage.group(2).upper()
        fields.update(
            {
                "actuator_type": _first(
                    clean,
                    [r"(?:Actuator|Aduahpor|Acduhpor)\s+Type\s+(Piston\s*\([^)]*Cylinder[^)]*\))"],
                ),
                "actuator_action": _first(clean, [r"\bAction\s+(Double\s+Acting|Single\s+Acting)\b"]),
                "fail_position": _first(
                    clean,
                    [r"Fa(?:i|l)l\s+Pos\.\s*/\s*A(?:i|l)r\s+Action\s+(Open|Closed|Close|Lock)\b"],
                ),
                "positioner_model": _first(
                    clean,
                    [r"\bModel\s+(SVI\s*2\s*AP\s*[-–—]?\s*[0-9]{8})\b"],
                ),
                "positioner_diagnostics_level": _first(
                    clean,
                    [r"D(?:i|l)agnostic\s+Level\s+(Standard|Advanced(?:\s+Diagnostics)?)\b"],
                ),
                "end_connection": fields.get("end_connection")
                or _first(clean, [r"End\s+Connec+t?ion\s+([A-Z]{1,3}\s+Flanges)"]),
            }
        )
        if actuator_model_size[0]:
            fields["actuator_model"] = actuator_model_size[0]
            fields["actuator_size"] = actuator_model_size[1]
            if actuator_model_size[2]:
                fields["fail_position"] = actuator_model_size[2]
        if spring_travel:
            fields["bench_range"] = spring_travel.group(1).translate(_DASHES)
            fields["actuator_travel"] = spring_travel.group(2)
        supply = _first(
            clean,
            [r"Supply\s*/\s*Safety\s+Factor\s+A(?:i|l)r,?\s*([0-9.]+\s*bar\s*g?)\s*/"],
        )
        if supply:
            fields["supply_pressure"] = supply
    return {key: value for key, value in fields.items() if value}


def _parse_control_valve_specifications(text: str) -> dict[str, str]:
    parser_text = _parser_text(text)
    clean = re.sub(r"(?<=\w)\s*[:：]\s*", " ", _compact(parser_text))
    raw_model = _model_no_from_labelled_line(parser_text)
    raw_size = _first(
        clean,
        [r"Body\s*Size\s*[\[|]*\s*([0-9]+(?:\s*-\s*[0-9]+\s*/\s*[0-9]+|\s*/\s*[0-9]+|\.[0-9]+)?)\s*(?:in\b|[\"″])"],
    )
    fields = {
        "manufacturer": _first(
            clean,
            [
                r"\b(Masoneilan|Baker\s+Hughes|Fisher|Emerson|Flowserve|Metso|Samson)\s+Valves?\b",
                r"\bManufacturer\s*[:：]?\s*([A-Za-z][A-Za-z ]{2,})\b",
            ],
        ),
        "serial_number": _first(
            clean,
            [r"Serial\s*No\.?\s*([A-Z]{2}[0-9]{2}[A-Z][0-9]{4}-[0-9]{1,2})\b"],
        ),
        "model_no": normalize_valve_model(raw_model),
        "body_type": _first(clean, [r"Body\s*Type\s*[\[|]*\s*(GLOBE|BUTTERFLY|BALL)\b"]),
        "valve_size": (re.sub(r"\s*([-\/])\s*", r"\1", raw_size) + " in") if raw_size else "",
        "pressure_class": _first(clean, [r"Rating\s*[\[|]*\s*(ASME\s+CLASS\s+\d+)"]),
        "end_connection": _first(
            clean,
            [r"End\s*Connection\s*[\[|]*\s*(RTJ|RF\s+Flanges|BW|SW|HUB)\b"],
        ),
        "rated_cv": _first(clean, [r"Rated\s*Cv\s*[\[|]*\s*([0-9.]+)"]),
        "leakage_class": _first(
            clean,
            [r"Leakage\s*Spec\.?\s*[\[|]*\s*(CLASS\s*[0-9IVX]+|[0-9.]+%/Max\.Diff\.Pr\.)"],
        ),
        "trim_size": _first(clean, [r"Trim\s*Size\s*[\[|]*\s*([A-Z0-9() .-]+?)\s+Trim\s*Form"]),
        "trim_form": _first(clean, [r"Trim\s*Form\s*[\[|]*\s*([A-Z0-9() .-]+?)\s+Characteristic"]),
        "characteristic": _first(clean, [r"Characteristic\s*[\[|]*\s*([A-Z0-9%() .-]+?)\s+Rated\s*Cv"]),
        "flow_action": _first(clean, [r"Flow\s*to\s*[\[|]*\s*(OPEN|CLOSE)\b"]),
        "fail_position": _first(clean, [r"Fail\s*Position\s*[\[|]*\s*(OPEN|CLOSE)\b"]),
        "actuator_type": _first(
            clean,
            [
                r"Actuator\s*Type\.?\s*[\[|]*\s*"
                r"((?:SPRING[- ]?DIAPHRAGM|PISTON(?:\s*\(\s*CYLINDER\s*\))?|"
                r"DIAPHRAGM|CYLINDER|ROTARY|LINEAR))\b"
            ],
        ),
        "actuator_size": _first(
            clean,
            [
                r"Actuator\s*Type\.?\s*[\[|]*\s*"
                r"(?:SPRING[- ]?DIAPHRAGM|PISTON(?:\s*\(\s*CYLINDER\s*\))?|"
                r"DIAPHRAGM|CYLINDER|ROTARY|LINEAR).{0,140}?\bSize\s+(#[0-9A-Z()]+)"
            ],
        ),
        "bench_range": _first(
            clean,
            [
                r"Spring\s+Range\s+(?:/\s*Travel\s+)?"
                r"([0-9.]+\s*[-‐‑‒–—−]\s*[0-9.]+\s*(?:psi\s*g?|MPa(?:\s*g)?))",
                r"Spring\s+Range\s+([0-9.]+\s*(?:psi\s*g?|MPa(?:\s*g)?))",
            ],
        ).translate(_DASHES),
        "supply_pressure": _first(
            clean,
            [r"Supply\s+Pressure\s+([0-9.]+\s*(?:psi\s*g?|MPa(?:\s*g)?))"],
        ),
        "actuator_travel": _first(
            clean,
            [r"Rated\s*Travel\s+([0-9.]+\s*mm\s*\([^)]*\))"],
        ),
        "positioner_type": _first(
            clean,
            [r"Positioner\s*Type\s+([A-Z0-9/(). -]*HART[A-Z0-9/(). -]*)\s+Case"],
        ),
        "positioner_model": _first(
            clean,
            [r"Positioner\s*Type.{0,180}?Model\s*No\.?\s*(SV[I1]2-[0-9]{8})"],
        ),
    }
    if fields.get("positioner_model"):
        fields["positioner_model"] = re.sub(
            r"^SV[1I]2-", "SVI2-", fields["positioner_model"], flags=re.I
        ).upper()
    if fields.get("actuator_type") and fields.get("actuator_size"):
        fields["actuator_model_note"] = "规格书未提供"
    if re.search(r"\bSpring\s+Range\b", clean, re.I) and not fields.get("bench_range"):
        fields["spring_range_note"] = "规格书栏位为空"
    return {key: value for key, value in fields.items() if value}


def _parse_assembly_quality_plan(text: str) -> dict[str, str]:
    clean = _compact(re.sub(r"<[^>]+>", " ", text))
    raw_size = _first(
        clean,
        [
            r"\bSize(?:\s+D\.N\.)?\s*[:：]\s*DN\s*[0-9×x*]+\s*\(\s*([0-9.]+)\s*[\"″]",
            r"\bSize\s*[:：]\s*([0-9.]+)\s*Inch\b",
            r"\bDN\s*[0-9×x*]+\s*\(\s*([0-9.]+)\s*[\"″]",
        ],
    )
    raw_model = _first(
        clean,
        [r"\bType\s*[:：]\s*((?:[0-9]{2}|XX)-[0-9A-Z]+)\b"],
    )
    normalized_model = normalize_valve_model(raw_model)
    if not normalized_model and re.fullmatch(r"XX-[0-9A-Z]+", raw_model, re.I):
        normalized_model = raw_model.upper()
    fields = {
        "model_no": normalized_model,
        "valve_size": f"{raw_size} in" if raw_size else _first(
            clean, [r"\bSize\s*(?:D\.N\.\s*)?[:：]\s*(DN\s*\d+(?:\s*[×x*]\s*\d+)?)"]
        ),
        "pressure_class": _first(
            clean,
            [
                r"\bConnections?\s*[:：]\s*(?:FLANGED\s+)?(CLASS\s*\d+)\b",
                r"\bConnection\s*[:：]\s*(ASME\s*\d+)\s*#",
                r"\b(CLASS\s*(?:150|300|600|900|1500|2500))\b",
            ],
        ),
        "bench_range": _first(
            clean,
            [
                r"\bSpring range\s*[:：]\s*([0-9.]+\s*[-‐‑‒–—−]\s*[0-9.]+\s*psi)\b",
                r"\b([0-9.]+\s*[-‐‑‒–—−]\s*[0-9.]+\s*psi)\b",
            ],
        ).translate(_DASHES),
    }
    return {key: value for key, value in fields.items() if value}


def _model_no_from_labelled_line(text: str) -> str:
    candidate_pattern = re.compile(
        r"(?<![0-9A-Z])\d{2}\s*(?:[-=‐-—−]\s*)?[0-9A-Z](?:[0-9A-Z]|\s(?=\d)){4,}(?![0-9A-Z])",
        re.I,
    )
    lines = _parser_text(text).splitlines()
    for index, line in enumerate(lines):
        label = re.search(r"Model\s*No\.?", line, re.I)
        if not label:
            continue
        field_text = line[label.end() : label.end() + 80]
        candidate = candidate_pattern.search(field_text)
        if not candidate:
            # Table OCR commonly places the label and value in adjacent cells,
            # which become adjacent lines after markup flattening.
            field_text = " ".join(lines[index + 1 : index + 3])[:80]
            candidate = candidate_pattern.search(field_text)
        if candidate:
            if re.search(r"[0-9A-Z]", field_text[: candidate.start()], re.I):
                continue
            candidate_text = re.sub(r"\s+(?=\d{1,2}$)", "", candidate.group(0))
            normalized = normalize_valve_model(candidate_text)
            if normalized:
                return normalized
    return ""


def parse_spec_fields(text: str) -> dict[str, str]:
    fields = _parse_generic_fields(text)
    spec_format = detect_spec_format(text)
    if spec_format == "control_valve_specifications":
        fields.update(_parse_control_valve_specifications(text))
    elif spec_format == "assembly_quality_plan":
        fields.update(_parse_assembly_quality_plan(text))
    # Complete, bounded explicit values outrank loose legacy template searches.
    # Keep the template fallback only when no supported labelled value is available.
    labelled = _parse_labelled_fields(text)
    raw_values = specification_source_values(text)
    # A prefix of an unsupported literal (e.g. "6 psi-custom") is not a value.
    # These scalar labels use complete-value validation even in legacy layouts.
    strict_fields = {"rated_cv", "supply_pressure", "fail_position"}
    # Explicit aliases must not inherit the legacy template's loose CL/size
    # searches, which would turn e.g. "proprietary CL 600" into a known class.
    for key, label in (
        ("pressure_class", r"\bPressure\s+Class\b"),
        ("valve_size", r"\bValve\s+Size\b"),
        ("leakage_class", r"\bLeakage\s+Class\b"),
        ("model_no", r"\b(?:Valve\s+)?Model\s+Number\b"),
    ):
        if re.search(label, _parser_text(text), re.I):
            strict_fields.add(key)
    for key in strict_fields:
        if key in raw_values and key not in labelled:
            fields.pop(key, None)
    fields.update(labelled)
    return fields


def _is_specification_data_page(text: str) -> bool:
    clean = _compact(text)
    return bool(
        re.search(r"\bSpecification Sheet\b", clean, re.I)
        or re.search(r"\bCONTROL VALVE SPECIFICATIONS\b", clean, re.I)
    )


def _has_report_equipment_fields(text: str) -> bool:
    fields = parse_spec_fields(text)
    return all(
        fields.get(field)
        for field in (
            "model_no",
            "valve_size",
            "pressure_class",
            "fail_position",
            "bench_range",
        )
    )
