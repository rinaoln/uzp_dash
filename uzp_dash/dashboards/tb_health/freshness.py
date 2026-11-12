"""Даты источников организаций отдельно от месяца витринного прогноза."""
from __future__ import annotations

import pandas as pd


def orgs_snapshot(dates: dict, level: str = "gosb") -> dict:
    """Описание выбранного среза. Отсутствующая дата не заменяется нулём."""
    key = "ref_orgs_tb" if level == "tb" else "ref_orgs"
    # Старые сохранённые анализы строились строго на ref_closed.
    raw = dates.get(key, dates.get("ref_closed"))
    expected = dates.get("ref_closed")
    target = pd.Timestamp(expected) if expected is not None else None
    selected = pd.Timestamp(raw) if raw is not None else None
    if selected is not None and pd.isna(selected):
        selected = None
    available = selected is not None
    label = selected.strftime("%d.%m.%Y") if available else "нет доступного среза"
    stale = bool(available and target is not None and selected < target)
    lag = ((target.year - selected.year) * 12 + target.month - selected.month
           if available and target is not None else None)
    if not available:
        note = "Доступный срез организаций не найден"
        if target is not None:
            note += f' на дату не позднее {target:%d.%m.%Y}'
        note += "."
    elif stale:
        note = (f'Данные организаций на {label}: последний доступный срез. '
                f'Ожидаемый срез — {target:%d.%m.%Y}.')
    else:
        note = f'Данные организаций на {label}.'
    yoy = (f'Год к году: {selected:%m.%Y} к '
           f'{selected - pd.DateOffset(years=1):%m.%Y}.' if available else "")
    return {"available": available, "date": selected.date() if available else None,
            "label": label, "stale": stale, "lag_months": lag, "note": note, "yoy": yoy}
