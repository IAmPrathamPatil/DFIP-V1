"""Structural inspection of DFIP SQL migrations.

Parses CREATE TABLE / INDEX / INSERT text. Does not connect to PostgreSQL
and does not execute transformations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TableDef:
    name: str
    columns: list[str] = field(default_factory=list)
    primary_key: tuple[str, ...] | None = None
    unique: list[tuple[str, ...]] = field(default_factory=list)
    foreign_keys: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""


def strip_sql_comments(sql: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    out: list[str] = []
    i = 0
    in_string = False
    while i < len(without_block):
        char = without_block[i]
        if in_string:
            out.append(char)
            if char == "'":
                if i + 1 < len(without_block) and without_block[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if char == "'":
            in_string = True
            out.append(char)
            i += 1
            continue
        if char == "-" and i + 1 < len(without_block) and without_block[i + 1] == "-":
            while i < len(without_block) and without_block[i] != "\n":
                i += 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _dollar_quote_tag(text: str, start: int) -> str | None:
    """Return `$tag$` at *start*, or None if this `$` is not a dollar-quote opener."""
    if start >= len(text) or text[start] != "$":
        return None
    index = start + 1
    while index < len(text) and (text[index].isalnum() or text[index] == "_"):
        index += 1
    if index < len(text) and text[index] == "$":
        return text[start : index + 1]
    return None


def split_statements(sql: str) -> list[str]:
    cleaned = strip_sql_comments(sql)
    parts: list[str] = []
    current: list[str] = []
    in_string = False
    dollar_tag: str | None = None
    i = 0
    while i < len(cleaned):
        if dollar_tag is not None:
            if cleaned.startswith(dollar_tag, i):
                current.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            current.append(cleaned[i])
            i += 1
            continue
        char = cleaned[i]
        if in_string:
            current.append(char)
            if char == "'":
                if i + 1 < len(cleaned) and cleaned[i + 1] == "'":
                    current.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if char == "'":
            in_string = True
            current.append(char)
            i += 1
            continue
        tag = _dollar_quote_tag(cleaned, i)
        if tag is not None:
            dollar_tag = tag
            current.append(tag)
            i += len(tag)
            continue
        if char == ";":
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    i = 0
    while i < len(body):
        char = body[i]
        if in_string:
            current.append(char)
            if char == "'":
                if i + 1 < len(body) and body[i + 1] == "'":
                    current.append(body[i + 1])
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if char == "'":
            in_string = True
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_tables(sql: str) -> dict[str, TableDef]:
    tables: dict[str, TableDef] = {}
    pattern = re.compile(
        r"CREATE TABLE\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)",
        re.S | re.I,
    )
    for stmt in split_statements(sql):
        match = pattern.search(stmt)
        if not match:
            continue
        name = match.group(1)
        body = match.group(2)
        table = TableDef(name=name, body=body)
        for item in _split_top_level(body):
            pk = re.match(r"(?:CONSTRAINT\s+\S+\s+)?PRIMARY KEY\s*\(([^)]+)\)", item, re.I)
            if pk:
                table.primary_key = tuple(c.strip() for c in pk.group(1).split(","))
                continue
            unique = re.match(r"(?:CONSTRAINT\s+\S+\s+)?UNIQUE\s*\(([^)]+)\)", item, re.I)
            if unique:
                table.unique.append(tuple(c.strip() for c in unique.group(1).split(",")))
                continue
            inline_unique = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s+.*\bUNIQUE\b", item, re.I)
            fk = re.search(r"REFERENCES\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]+)\)", item, re.I)
            if fk:
                local = item.strip().split()[0]
                if local.upper() in {"CONSTRAINT", "FOREIGN"}:
                    local_match = re.search(r"FOREIGN KEY\s*\(([^)]+)\)", item, re.I)
                    local = local_match.group(1).split(",")[0].strip() if local_match else ""
                table.foreign_keys.append((local, fk.group(1)))
            if item.upper().startswith("CONSTRAINT") or item.upper().startswith("PRIMARY"):
                continue
            col = item.strip().split()[0]
            if col.upper() in {"CONSTRAINT", "UNIQUE", "CHECK", "FOREIGN", "PRIMARY"}:
                continue
            table.columns.append(col)
            if inline_unique and "PRIMARY KEY" not in item.upper():
                table.unique.append((col,))
        tables[name] = table
    return tables


def parse_indexes(sql: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    indexes: dict[str, tuple[str, tuple[str, ...]]] = {}
    pattern = re.compile(
        r"CREATE INDEX\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+ON\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]+)\)",
        re.I,
    )
    for stmt in split_statements(sql):
        match = pattern.search(stmt)
        if not match:
            continue
        cols = tuple(c.strip().split()[0] for c in match.group(3).split(","))
        indexes[match.group(1)] = (match.group(2), cols)
    return indexes


def parse_insert_tuples(sql: str, table: str) -> list[tuple[str, ...]]:
    """Return value-tuples from INSERT INTO <table> statements (string literals kept)."""
    rows: list[tuple[str, ...]] = []
    header = re.compile(
        rf"^INSERT INTO\s+{re.escape(table)}\s*\((.*?)\)\s*VALUES\s*(.*)$",
        re.S | re.I,
    )
    for stmt in split_statements(sql):
        match = header.match(stmt.strip())
        if not match:
            continue
        values_sql = match.group(2).strip()
        for group in _split_top_level(values_sql):
            inner = group.strip()
            if inner.startswith("(") and inner.endswith(")"):
                inner = inner[1:-1]
            rows.append(tuple(part.strip() for part in _split_top_level(inner)))
    return rows


def unquote_sql_string(value: str) -> str:
    text = value.strip()
    if text.upper() == "NULL":
        return ""
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("''", "'")
    return text


def validate_sql_shape(sql: str) -> list[str]:
    """Return a list of structural problems. Empty means the SQL looks well-formed."""
    problems: list[str] = []
    statements = split_statements(sql)
    if not statements:
        problems.append("no SQL statements")
    for stmt in statements:
        head = stmt.lstrip().split()[0].upper()
        if head not in {
            "CREATE",
            "INSERT",
            "UPDATE",
            "COMMENT",
            "ALTER",
            "DROP",
            "GRANT",
            "REVOKE",
            "DO",
        }:
            problems.append(f"unexpected statement start: {head}")
        if stmt.count("'") % 2 != 0:
            problems.append(f"unbalanced quotes in statement starting {head}")
    return problems
