"""
qcs_replay.data — Excel / CSV data loading for parametrized tests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def load_excel_rows(path: str | Path, sheet: str | int = 0) -> list[dict[str, Any]]:
    """
    Read an Excel workbook and return a list of row dicts (header → value).
    Empty / all-None rows are skipped.
    """
    import openpyxl  # noqa: PLC0415

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[sheet] if isinstance(sheet, int) else wb[sheet]

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
    result: list[dict] = []

    for row in rows[1:]:
        if all(v is None for v in row):
            continue
        result.append(dict(zip(headers, row)))

    return result


def load_csv_rows(path: str | Path) -> list[dict[str, Any]]:
    """Fallback loader for plain CSV files."""
    import csv  # noqa: PLC0415

    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
