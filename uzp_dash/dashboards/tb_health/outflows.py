"""Полный отток, сопоставимые когорты год к году и работа по возврату B2B.

Численность — сумма месячных событий ухода, а не уникальные физлица за квартал.
B2B/B2C определяется на исходном грейне ГОСБ × ИНН × месяц до свёртки.
Возврат — факт витрины возвратов, успех задачи сам по себе возвратом не является.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ... import progress
from ...db import KerberosTicketError, read_sql
from . import queries as Q, text_rules

RETURN_TARGET = .5
UNKNOWN_REASON = "Причина не зафиксирована"


@dataclass
class Source:
    facts: pd.DataFrame = field(default_factory=pd.DataFrame)
    tasks: pd.DataFrame = field(default_factory=pd.DataFrame)
    dates: dict = field(default_factory=dict)
    available: set = field(default_factory=set)
    tasks_ok: bool = False
    error: str = ""


def period_dates(latest, available) -> dict:
    p = pd.Timestamp(latest).to_period("M")
    months = [(p-k).to_timestamp("M").date() for k in range(3)]
    previous = [(p-k-12).to_timestamp("M").date() for k in range(3)]
    return {
        **{f"m_out{i+1}": m for i, m in enumerate(months)},
        **{f"m_prev{i+1}": m for i, m in enumerate(previous)},
        "out_months": months, "out_prev_months": previous,
        "out_from": (p-2).to_timestamp().date(),
        "out_prev_from": (p-14).to_timestamp().date(),
        "out_label": ", ".join(m.strftime("%m.%Y") for m in reversed(months)),
        "out_asof": months[0],
        "out_available": [m for m in months if m in available],
    }


def load(engine, dates: dict, apparat: set) -> Source:
    source = Source()
    try:
        rows = read_sql(engine, Q.OUTFLOW_DATES, {"ref_closed": dates["ref_closed"]})
        source.available = set(pd.to_datetime(rows["report_dt"]).dt.date.dropna())
        if not source.available:
            source.error = "Закрытый срез оттока ещё не поступил."
            return source
        source.dates = period_dates(max(source.available), source.available)
        params = {**source.dates, "ref_funnel": dates["ref_funnel"]}
        source.facts = read_sql(engine, Q.OUTFLOW_OVERVIEW, params)
        if not source.facts.empty:
            source.facts["report_dt"] = pd.to_datetime(source.facts["report_dt"]).dt.date
            source.facts = source.facts.loc[~source.facts.new_gosb_id.isin(apparat)].copy()
        try:
            source.tasks = read_sql(engine, Q.OUTFLOW_COHORT_TASKS, params)
            source.tasks_ok = True
        except KerberosTicketError:
            raise
        except Exception as err:  # дополнительная аналитика не останавливает отчёт
            progress.warn(f"Отток: задачи для покрытия и ролей недоступны ({str(err)[:120]})")
        progress.done(f"Полный отток: срез {source.dates['out_asof']}, "
                      f"месяцев в окне {len(source.dates['out_available'])}/3, "
                      f"{len(source.facts)} строк B2B+B2C с историей")
    except KerberosTicketError:
        raise
    except Exception as err:
        source.error = "Не удалось прочитать данные для полного свода оттока."
        progress.warn(f"{source.error} {str(err)[:120]}")
    return source


def change(current: float, previous: float | None) -> dict:
    if previous is None:
        return {"previous": None, "absolute": None, "percent": None}
    delta = current - previous
    return {"previous": previous, "absolute": delta,
            "percent": delta / previous * 100 if previous else None}


def _truth(value) -> bool:
    return pd.notna(value) and str(value).lower() in {"true", "1", "1.0"}


def _text(value, default="") -> str:
    return str(value).strip() or default if pd.notna(value) else default


def _records(source: Source, tb_id: int | None) -> tuple[list, list]:
    frame = source.facts
    if frame.empty:
        return [], []
    if tb_id is not None:
        frame = frame.loc[frame.tb_id.eq(tb_id)]
    tasks = source.tasks
    if not tasks.empty:
        if tb_id is not None:
            tasks = tasks.loc[tasks.tb_id.eq(tb_id)]
        tasks = tasks.sort_values(["report_dt", "task_create_dt"]).drop_duplicates(
            "task_code", keep="last")
    index: dict = {}
    task_rows = []
    for task in tasks.to_dict("records"):
        task["month"] = pd.Timestamp(task["task_create_dt"]).to_period("M")
        reason, origin = text_rules.outflow_reason_label(
            _text(task.get("task_questionnaire")), _text(task.get("task_comment")))
        task["reason"], task["reason_source"] = reason, origin
        index.setdefault((int(task["old_gosb_id"]), int(task["inn"]), task["month"]), []).append(task)
        task_rows.append(task)
    records = []
    for row in frame.to_dict("records"):
        month = pd.Timestamp(row["report_dt"]).to_period("M")
        uid, inn = int(row["new_gosb_id"]), int(row["inn"])
        old = int(row["old_gosb_id"])
        eligible = index.get((old, inn, month), []) + index.get((old, inn, month+1), [])
        relevant = [t for t in eligible if t["task_type"] == "Отток"]
        reasons = [t for t in eligible if t["reason"]]
        best = max(reasons, key=lambda t: (
            t["reason_source"] == "чек-лист задачи", t["task_type"] == "Отток",
            t["month"] == month, str(t["task_create_dt"])), default={})
        row["reason_known"] = bool(best.get("reason"))
        row["reason"] = (best.get("reason") or
                         (UNKNOWN_REASON if source.tasks_ok else "Данные причин недоступны"))
        row["reason_source"] = best.get("reason_source", "")
        # Прямой комментарий — свидетельство сотрудника, без выдуманной причины.
        funding = [t for t in eligible if "финанс" in _text(t.get("task_comment")).lower()]
        evidence = max(funding, key=lambda t: str(t["task_create_dt"]), default={})
        full = _text(evidence.get("task_comment"))
        row["evidence"] = full[:280] + ("…" if len(full) > 280 else "")
        row["company_name"] = (_text(row.get("company_name"))
                               or next((_text(t.get("company_name")) for t in eligible
                                        if _text(t.get("company_name"))), "") or f"Орг. {inn}")
        row["region"] = _text(row.get("region"), "Регион не определён")
        row["seg"] = _text(row.get("seg"), "Сегмент не определён")
        row["gone"] = max(0., float(row["gone"]))
        row["returned"] = min(row["gone"], max(0., float(row.get("returned") or 0)))
        row["kept"] = row["gone"] - row["returned"]
        row["kind"] = "B2B" if row["gone"] >= 3 else "B2C"
        # На уровне сети клиент = ТБ × ИНН, внутри ТБ = ГОСБ × ИНН.
        row["client"] = (int(row["tb_id"]) if tb_id is None else uid, inn)
        row["out_tasks"] = {str(t["task_code"]) for t in relevant}
        records.append(row)
    return records, task_rows


def _sum(rows: list, key: str = "gone") -> float:
    return sum(r[key] for r in rows)


def _group(rows: list, previous: list, key: str, comparable: bool) -> list:
    names = {r[key] for r in rows} | ({r[key] for r in previous} if comparable else set())
    result = []
    for name in names:
        part = [r for r in rows if r[key] == name]
        before = [r for r in previous if r[key] == name]
        reasons = {}
        for r in part:
            reasons[r["reason"]] = reasons.get(r["reason"], 0.) + r["gone"]
        result.append({"name": name, "gone": _sum(part), "kept": _sum(part, "kept"),
                       "returned": _sum(part, "returned"),
                       "clients": len({r["client"] for r in part}),
                       "leading_reason": max(reasons, key=reasons.get) if reasons else "—",
                       "change": change(_sum(part), _sum(before) if comparable else None)})
    return sorted(result, key=lambda r: (-r["gone"], r["name"]))


def _tasks(rows: list, tasks: list, ok: bool, today) -> dict:
    b2b = [r for r in rows if r["kind"] == "B2B"]
    clients = {r["client"] for r in b2b}
    covered = {r["client"] for r in b2b if r["out_tasks"]}
    # Объединяем коды до подсчёта: одна задача может относиться к двум месяцам.
    codes = set().union(*(r["out_tasks"] for r in b2b)) if b2b else set()
    selected = [t for t in tasks if str(t["task_code"]) in codes]
    roles: dict = {}
    for t in selected:
        role = _text(t.get("role_code"), "Роль не указана")
        g = roles.setdefault(role, {"role": role, "tasks": 0, "success": 0,
                                    "closed_other": 0, "open": 0, "overdue": 0})
        g["tasks"] += 1
        success = _truth(t.get("is_task_closed_success"))
        closed = success or _truth(t.get("is_task_closed"))
        g["success" if success else "closed_other" if closed else "open"] += 1
        due = pd.to_datetime(t.get("plan_close_task_dttm"), errors="coerce")
        if not closed and pd.notna(due) and due.date() < today:
            g["overdue"] += 1
    gone = _sum(b2b)
    covered_gone = _sum([r for r in b2b if r["client"] in covered])
    return {"available": ok, "clients": len(clients), "covered": len(covered),
            "percent": len(covered)/len(clients)*100 if clients else None,
            "gone": gone, "covered_gone": covered_gone,
            "gone_percent": covered_gone/gone*100 if gone else None,
            "tasks": len(selected), "roles": sorted(roles.values(), key=lambda g: -g["tasks"])}


def _report(rows: list, previous: list, tasks: list, source: Source, today) -> dict:
    months = source.dates.get("out_months", [])
    prev_months = source.dates.get("out_prev_months", [])
    comparable = bool(months) and all(m in source.available for m in months+prev_months)
    total, b2b, b2c = _sum(rows), [r for r in rows if r["kind"] == "B2B"], [r for r in rows if r["kind"] == "B2C"]
    old_b2b = [r for r in previous if r["kind"] == "B2B"]
    gone, returned = _sum(b2b), _sum(b2b, "returned")
    target = gone * RETURN_TARGET
    return_ok = all(_truth(r.get("return_source")) for r in b2b)
    return_available = any(_truth(r.get("return_source")) for r in b2b)
    cases: dict = {}
    for r in b2b:
        k = (r["client"], r["region"], r["reason"])
        g = cases.setdefault(k, {"name": r["company_name"], "inn": r["inn"],
                                 "units": set(), "region": r["region"],
                                 "reason": r["reason"], "reason_source": r["reason_source"],
                                 "evidence": r["evidence"], "gone": 0., "kept": 0.})
        g["gone"] += r["gone"]
        g["kept"] += r["kept"]
        g["units"].add(r.get("gosb_name") or "—")
    for case in cases.values():
        case["unit"] = ", ".join(sorted(case.pop("units")))
    return {
        "available": not source.error and bool(source.dates), "error": source.error,
        "dates": source.dates, "complete": all(m in source.available for m in months),
        "comparable": comparable, "total": total,
        "change": change(total, _sum(previous) if comparable else None),
        "b2b": gone, "b2c": _sum(b2c),
        "b2b_change": change(gone, _sum(old_b2b) if comparable else None),
        "b2c_change": change(_sum(b2c), _sum([r for r in previous if r["kind"] == "B2C"]) if comparable else None),
        "returned": returned, "return_target": target, "return_source_ok": return_ok,
        "return_available": return_available,
        "return_rate": returned/gone*100 if gone and return_available else None,
        "execution": returned/target*100 if target and return_available else None,
        "return_gap": max(0., target-returned) if return_available else None,
        "return_record_gone": _sum([r for r in b2b if _truth(r.get("return_record"))]),
        "known_reason_gone": _sum([r for r in b2b if r["reason_known"]]),
        "previous_known_reason_gone": _sum([r for r in old_b2b if r["reason_known"]]),
        "regions": _group(b2b, old_b2b, "region", comparable),
        "reasons": _group(b2b, old_b2b, "reason", comparable),
        "cases": sorted(cases.values(), key=lambda g: -g["gone"])[:15],
        "tasks": _tasks(rows, tasks, source.tasks_ok, today),
    }


def analyze(source: Source | None, tb_id: int | None, today) -> dict:
    if source is None:
        return {}
    rows, tasks = _records(source, tb_id)
    current = [r for r in rows if r["report_dt"] in source.dates.get("out_months", [])]
    previous = [r for r in rows if r["report_dt"] in source.dates.get("out_prev_months", [])]
    report = _report(current, previous, tasks, source, pd.Timestamp(today).date())
    report["by_seg"] = {
        seg: _report([r for r in current if r["seg"] == seg],
                     [r for r in previous if r["seg"] == seg], tasks, source,
                     pd.Timestamp(today).date())
        for seg in sorted({r["seg"] for r in current+previous})}
    return report
