"""Проверка недоверенных JSON/XML до сохранения или возврата документа."""

import json
import math
import xml.etree.ElementTree as ElementTree
from xml.parsers import expat

from .errors import ManagerError
from .limits import MAX_DOCUMENT_DEPTH, MAX_FILE_BYTES, MAX_XML_ELEMENTS


def check_size(content: bytes) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise ManagerError("Документ превышает лимит 8 МиБ.")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON допускает только конечные числа.")
    return number


def _invalid_constant(value: str):
    raise ValueError("JSON не допускает NaN и Infinity.")


def _unique_keys(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющийся ключ JSON.")
        result[key] = value
    return result


def json_document(content: bytes) -> bytes:
    check_size(content)
    try:
        value = json.loads(
            content, parse_constant=_invalid_constant, parse_float=_finite_float,
            object_pairs_hook=_unique_keys,
        )
        pending = [(value, 1)]
        while pending:
            item, depth = pending.pop()
            if depth > MAX_DOCUMENT_DEPTH:
                raise ValueError("Слишком глубокий JSON: максимум 64 уровня.")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        result = json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2).encode("utf-8")
        check_size(result)
        return result
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ManagerError("Некорректный JSON или превышена допустимая сложность документа.") from error


def xml_document(content: bytes) -> bytes:
    check_size(content)
    depth = 0
    elements = 0
    parser = expat.ParserCreate()

    def start_element(name, attributes):
        nonlocal depth, elements
        depth += 1
        elements += 1
        if depth > MAX_DOCUMENT_DEPTH or elements > MAX_XML_ELEMENTS:
            raise ManagerError("Превышены лимиты XML: глубина 64, элементы 10000.")

    def end_element(name):
        nonlocal depth
        depth -= 1

    def forbidden(*arguments):
        raise ManagerError("DTD, сущности и внешние ресурсы XML запрещены.")

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(content, True)
        # После проверки Expat в документе нет DTD и пользовательских сущностей.
        root = ElementTree.fromstring(content)
        result = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
        check_size(result)
        return result
    except (expat.ExpatError, ElementTree.ParseError, ValueError, RecursionError) as error:
        raise ManagerError("Некорректный XML.") from error
