"""Полный отток, сопоставимые когорты год к году и работа по возврату B2B.

Численность — сумма месячных событий ухода, а не уникальные физлица за квартал.
B2B/B2C определяется на исходном грейне ГОСБ × ИНН × месяц до свёртки.
Возврат — факт витрины возвратов, успех задачи сам по себе возвратом не является.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ... import agency, progress
from ...db import KerberosTicketError, read_sql
from . import queries as Q, segments, text_rules

RETURN_TARGET = .5
# Цель возврата для оттока 1–2 получателей: такой уход чаще текучка, и вернуть
# его труднее — цель ниже, чем у B2B.
RETURN_TARGET_B2C = .15
UNKNOWN_REASON = "Причина не зафиксирована"
NO_MZP = "МЗП не указан"
NO_INDUSTRY = "Отрасль не указана"
# «Прочее» в справочнике отраслей — не отрасль, а отсутствие классификации:
# в топах и закономерностях оно забивало бы всё остальное.
NOT_INDUSTRY = {NO_INDUSTRY, "Прочее"}
TOP_N = 5
# Порог значимости группы в рейтингах: меньше — шум, где 1 из 1 даёт 100%.
MIN_RATING_FL = 5
# Все роли воронки — в порядке от первой линии к руководству. Роль без задач
# показывается строкой с нулём: «никто из руководства не брал отток» — тоже вывод.
ROLE_ORDER = ["РН по Сервису", "СЗП", "МЗП", "МКК", "МКК ЦА", "КМ", "МС-СМС", "РН",
              "НО", "НУ", "ДУ", "ЗУПР РБ", "УПР", "ЗАМУПР", "ЗАМПРЕД", "ИД"]
ROLE_LABEL = {"РН по Сервису": "Сервис (РН по сервису)", "ЗАМПРЕД": "ЗамПред",
              "ЗАМУПР": "ЗамУпр", "ЗУПР РБ": "ЗУПР"}


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
            source.facts = _with_industry(engine, source.facts, params)
        # Т-2 считается от ПРОГНОЗНОГО месяца отчёта: к нему возвраты уже успели
        # дойти, и вердикт сотрудника «отток не подтверждён» можно проверить фактом.
        if dates.get("ref_cur"):
            t2 = (pd.Timestamp(dates["ref_cur"]).to_period("M") - 2).to_timestamp("M").date()
            source.dates["out_t2"] = t2
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


def _with_industry(engine, facts: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Отрасль организации из ЕПК; недоступность витрины не останавливает отчёт."""
    try:
        ind = read_sql(engine, Q.OUTFLOW_INDUSTRY, params)
    except KerberosTicketError:
        raise
    except Exception as err:  # noqa: BLE001 — разрез необязательный
        progress.warn(f"Отток: отрасли организаций недоступны ({str(err)[:120]})")
        return facts
    if ind.empty:
        return facts
    ind["inn"] = pd.to_numeric(ind["inn"], errors="coerce")
    ind = (ind.dropna(subset=["inn"]).sort_values("prio")
              .drop_duplicates("inn")[["inn", "industry_name"]])
    ind["inn"] = ind["inn"].astype("int64")
    out = facts.drop(columns=["industry_name"], errors="ignore")
    out["inn"] = pd.to_numeric(out["inn"]).astype("int64")
    out = out.merge(ind, on="inn", how="left")
    known = out["industry_name"].notna().sum()
    progress.done(f"Отток: отрасль известна у {known} из {len(out)} строк")
    return out


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
        task["unconfirmed"] = (text_rules.outflow_unconfirmed(
            _text(task.get("task_questionnaire")), _text(task.get("task_comment")))
            if task.get("task_type") == "Отток" else "")
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
        row["tb_name"] = _text(row.get("tb_name"), f'ТБ {row.get("tb_id")}')
        row["gosb_name"] = _text(row.get("gosb_name"), "ГОСБ не определён")
        row["mzp"] = _text(row.get("mzp_fio"))
        row["industry"] = _text(row.get("industry_name"), NO_INDUSTRY)
        row["industry_known"] = row["industry"] not in NOT_INDUSTRY
        row["holding"] = _text(row.get("holding_name"))
        force = row.get("is_force")
        row["agency"] = (agency.classify(row["company_name"], True if _truth(force) else None,
                                         row.get("industry_name"))
                         if row["seg"] == "РГС" else "")
        row["gone"] = max(0., float(row["gone"]))
        row["returned"] = min(row["gone"], max(0., float(row.get("returned") or 0)))
        row["kept"] = row["gone"] - row["returned"]
        row["kind"] = "B2B" if row["gone"] >= 3 else "B2C"
        # На уровне сети клиент = ТБ × ИНН, внутри ТБ = ГОСБ × ИНН.
        row["client"] = (int(row["tb_id"]) if tb_id is None else uid, inn)
        row["out_tasks"] = {str(t["task_code"]) for t in relevant}
        row["out_task_list"] = relevant
        row["month"] = month
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
    # ФЛ клиента — сумма его месячных уходов; задача роли «несёт» ФЛ всех строк
    # клиента, к которым она привязана (месяц ухода или следующий).
    fl_of: dict = {}
    for r in b2b:
        for code in r["out_tasks"]:
            g = fl_of.setdefault(code, {})
            cur = g.setdefault(r["client"], [0., 0.])
            cur[0] += r["gone"]
            cur[1] += r["returned"]
    roles: dict = {}
    for t in selected:
        role = _text(t.get("role_code"), "Роль не указана")
        g = roles.setdefault(role, {"role": role, "tasks": 0, "success": 0,
                                    "closed_other": 0, "open": 0, "overdue": 0,
                                    "_fl": {}, "_worked": set()})
        g["tasks"] += 1
        success = _truth(t.get("is_task_closed_success"))
        closed = success or _truth(t.get("is_task_closed"))
        g["success" if success else "closed_other" if closed else "open"] += 1
        due = pd.to_datetime(t.get("plan_close_task_dttm"), errors="coerce")
        if not closed and pd.notna(due) and due.date() < today:
            g["overdue"] += 1
        for client, (gone, ret) in fl_of.get(str(t["task_code"]), {}).items():
            g["_fl"][client] = (gone, ret)
            if closed:
                g["_worked"].add(client)
    for role in ROLE_ORDER:
        roles.setdefault(role, {"role": role, "tasks": 0, "success": 0, "closed_other": 0,
                                "open": 0, "overdue": 0, "_fl": {}, "_worked": set()})
    for g in roles.values():
        fl = g.pop("_fl")
        worked = g.pop("_worked")
        g["label"] = ROLE_LABEL.get(g["role"], g["role"])
        g["fl"] = sum(v[0] for v in fl.values())
        g["fl_returned"] = sum(v[1] for v in fl.values())
        g["fl_worked"] = sum(fl[c][0] for c in worked)
        g["worked_pct"] = (g["success"]+g["closed_other"])/g["tasks"]*100 if g["tasks"] else None
        g["return_pct"] = g["fl_returned"]/g["fl"]*100 if g["fl"] else None
    order = {r: i for i, r in enumerate(ROLE_ORDER)}
    gone = _sum(b2b)
    covered_rows = [r for r in b2b if r["client"] in covered]
    covered_gone = _sum(covered_rows)
    covered_ret = _sum(covered_rows, "returned")
    return {"available": ok, "clients": len(clients), "covered": len(covered),
            "percent": len(covered)/len(clients)*100 if clients else None,
            "gone": gone, "covered_gone": covered_gone,
            "covered_returned": covered_ret,
            "covered_return_pct": covered_ret/covered_gone*100 if covered_gone else None,
            "gone_percent": covered_gone/gone*100 if gone else None,
            "tasks": len(selected),
            "roles": sorted(roles.values(), key=lambda g: (-g["tasks"], order.get(g["role"], 99),
                                                           g["role"]))}


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


# ==================== Фокусы руководителя ================================== #

def _month_label(m) -> str:
    return pd.Timestamp(m).strftime("%m.%Y")


def _unit(r: dict, tb_id) -> str:
    """Подразделение уровня: в сети — ТБ, внутри ТБ — ГОСБ."""
    return r["tb_name"] if tb_id is None else r["gosb_name"]


def _rate(kept: float, gone: float) -> float | None:
    return kept/gone*100 if gone else None


def _rating(groups: dict, n: int = TOP_N) -> list:
    """Рейтинг по доле невозвращённых ФЛ; при равной доле выше тот, где потеря больше."""
    out = []
    for name, g in groups.items():
        if g["gone"] < MIN_RATING_FL:
            continue
        out.append({"name": name, "clients": len(g["clients"]), "gone": g["gone"],
                    "kept": g["kept"], "share": _rate(g["kept"], g["gone"]),
                    "note": g.get("note", "")})
    return sorted(out, key=lambda g: (-g["share"], -g["kept"], g["name"]))[:n]


def _add(groups: dict, key: str, r: dict, note: str = "") -> None:
    g = groups.setdefault(key, {"gone": 0., "kept": 0., "clients": set(), "note": note})
    g["gone"] += r["gone"]
    g["kept"] += r["kept"]
    g["clients"].add(r["client"])


def _t2_rating(rows: list, source: Source, tb_id) -> dict:
    """Фокус 1. Т-2: где вердикт «отток не подтверждён» опровергнут фактом.

    Берётся месяц Т-2 от прогнозного: возвраты по нему уже дошли. Строка оттока
    попадает в разбор, если по клиенту есть задача «Отток» (месяц ухода или
    следующий), закрытая с выводом, что ухода нет или он временный. Доля
    невозвращённых ФЛ в таких строках показывает, насколько этот вывод верен.
    """
    months = source.dates.get("out_months", [])
    t2 = source.dates.get("out_t2") or (months[1] if len(months) > 1 else None)
    result = {"available": bool(t2) and source.tasks_ok, "month": t2,
              "month_label": _month_label(t2) if t2 else "",
              "in_window": t2 in source.available if t2 else False}
    if not result["available"] or not result["in_window"]:
        return result
    part = [r for r in rows if r["report_dt"] == t2]
    with_task = [r for r in part if r["out_task_list"]]
    flagged = [r for r in with_task if any(t["unconfirmed"] for t in r["out_task_list"])]
    ids = {id(r) for r in flagged}
    confirmed = [r for r in with_task if id(r) not in ids]
    roles, mzp, units = {}, {}, {}
    for r in flagged:
        tasks = [t for t in r["out_task_list"] if t["unconfirmed"]]
        for role in {_text(t.get("role_code"), "Роль не указана") for t in tasks}:
            _add(roles, ROLE_LABEL.get(role, role), r)
        # МЗП — тот, кто вынес вердикт: автор задачи с ролью МЗП. ФИО берём из
        # витрины оттока (закреплённый МЗП), иначе — табельный автора.
        authors = {_text(t.get("isu_struct_saphr_id")).split(".")[0] for t in tasks
                   if _text(t.get("role_code")) == "МЗП"}
        for a in authors or ({""} if r["mzp"] else set()):
            name = r["mzp"] or (f"таб. № {a}" if a else NO_MZP)
            _add(mzp, name, r, r["gosb_name"])
        _add(units, _unit(r, tb_id), r)
    kept, gone = _sum(flagged, "kept"), _sum(flagged)
    c_kept, c_gone = _sum(confirmed, "kept"), _sum(confirmed)
    reasons: dict = {}
    for r in flagged:
        for t in r["out_task_list"]:
            if t["unconfirmed"]:
                key = t["unconfirmed"].split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
    result.update({
        "tasks_rows": len(with_task), "rows": len(flagged),
        "clients": len({r["client"] for r in flagged}),
        "gone": gone, "kept": kept, "share": _rate(kept, gone),
        "confirmed_share": _rate(c_kept, c_gone),
        "return_ok": all(_truth(r.get("return_source")) for r in part) if part else False,
        "by_source": reasons,
        "roles": _rating(roles), "mzp": _rating(mzp), "units": _rating(units),
        "unit_label": "ТБ" if tb_id is None else "ГОСБ",
    })
    return result


def _task_state(r_tasks: list) -> str:
    if not r_tasks:
        return "Задачи на отток нет"
    if any(t["unconfirmed"] for t in r_tasks):
        return "Закрыта: «отток не подтверждён»"
    if any(not (_truth(t.get("is_task_closed")) or _truth(t.get("is_task_closed_success")))
           for t in r_tasks):
        return "В работе"
    if any(_truth(t.get("is_task_closed_success")) for t in r_tasks):
        return "Закрыта успешно"
    return "Закрыта без успеха"


def _top_cases(b2b: list, tb_id, months: list) -> list:
    """Фокус 2. Топ-5 потерь окна на грейне клиента — для личного вовлечения."""
    cases: dict = {}
    for r in b2b:
        g = cases.setdefault(r["client"], {
            "name": r["company_name"], "inn": r["inn"], "unit": _unit(r, tb_id),
            "gosb": set(), "seg": r["seg"], "group": (r["agency"] if r["agency"] != agency.UNKNOWN else "")
            or (r["industry"] if r["industry_known"] else ""),
            "mzp": r["mzp"], "months": {}, "gone": 0., "returned": 0., "kept": 0.,
            "reasons": {}, "tasks": {}})
        g["gosb"].add(r["gosb_name"])
        if not g["mzp"]:
            authors = [_text(t.get("isu_struct_saphr_id")).split(".")[0] for t in r["out_task_list"]
                       if _text(t.get("role_code")) == "МЗП" and _text(t.get("isu_struct_saphr_id"))]
            g["mzp"] = f"таб. № {authors[0]}" if authors else ""
        g["months"][r["report_dt"]] = g["months"].get(r["report_dt"], 0.) + r["gone"]
        for k in ("gone", "returned", "kept"):
            g[k] += r[k]
        if r["reason_known"]:
            g["reasons"][r["reason"]] = g["reasons"].get(r["reason"], 0.) + r["gone"]
        for t in r["out_task_list"]:
            g["tasks"][str(t["task_code"])] = t
    out = []
    for g in sorted(cases.values(), key=lambda g: (-g["kept"], -g["gone"]))[:TOP_N]:
        if g["kept"] <= 0:
            continue
        tasks = list(g.pop("tasks").values())
        g["gosb"] = ", ".join(sorted(g["gosb"]))
        g["state"] = _task_state(tasks)
        g["reason"] = max(g["reasons"], key=g["reasons"].get) if g["reasons"] else UNKNOWN_REASON
        g["streak"] = _streak([g["months"].get(m, 0.) for m in reversed(months)])
        g["month_list"] = [(_month_label(m), g["months"].get(m, 0.)) for m in reversed(months)]
        g["return_pct"] = g["returned"]/g["gone"]*100 if g["gone"] else None
        g["action"] = _case_action(g)
        out.append(g)
    return out


def _case_action(g: dict) -> str:
    if g["state"] == "Задачи на отток нет":
        return "Назначить ответственного и задачу на отток"
    if g["state"].startswith("Закрыта: «отток"):
        return "Перепроверить вывод «отток не подтверждён»: люди не вернулись"
    if g["streak"] >= 2:
        return "Уход повторяется — встреча с ЛПР на уровне руководителя"
    if g["state"] == "В работе":
        return "Контроль срока задачи и согласованного возврата"
    return "Разобрать причину с ответственным и вернуть клиента в работу"


def _streak(values: list) -> int:
    """Самая длинная серия месяцев подряд с оттоком (значения — от старого к новому)."""
    best = cur = 0
    for v in values:
        cur = cur+1 if v > 0 else 0
        best = max(best, cur)
    return best


# Разрезы закономерностей в порядке приоритета и сколько строк даёт каждый.
# Ведомство, отрасль и клиент — адресные сигналы; подразделение и сегмент —
# крупные агрегаты, они почти всегда «подряд» и лишь дают фон.
PATTERN_DIMS = (("agency", "ведомство РГС", 2), ("industry", "отрасль", 2),
                ("client", "клиент", 2), ("unit", "", 1), ("seg", "сегмент", 1))


def _patterns(b2b: list, tb_id, months: list, n: int = 8) -> list:
    """Фокус 2. Закономерности за три месяца: где отток идёт подряд, а возврат ниже цели.

    Группа значима, если в месяце на неё приходится заметная часть оттока уровня
    (для клиента — любой B2B-уход). Серия — месяцы подряд со значимым оттоком.
    В вывод идут серии от двух месяцев с возвратом ниже цели, сортировка — по
    невозвращённым ФЛ внутри разреза; разрезы идут по приоритету (PATTERN_DIMS),
    у каждого свой лимит строк, чтобы вывод не состоял из одних агрегатов.
    """
    order = list(reversed(months))
    total_m = {m: sum(r["gone"] for r in b2b if r["report_dt"] == m) for m in order}
    total = sum(total_m.values())
    found = []
    for prio, (dim, title, _cap) in enumerate(PATTERN_DIMS):
        groups: dict = {}
        for r in b2b:
            if dim == "agency":
                key = r["agency"] if r["agency"] and r["agency"] != agency.UNKNOWN else ""
            elif dim == "industry":
                key = r["industry"] if r["industry_known"] else ""
            elif dim == "unit":
                key = _unit(r, tb_id)
            elif dim == "client":
                key = r["client"]
            else:
                key = r["seg"]
            if not key:
                continue
            g = groups.setdefault(key, {"m": {}, "gone": 0., "returned": 0., "kept": 0.,
                                        "name": r["company_name"] if dim == "client" else key})
            g["m"][r["report_dt"]] = g["m"].get(r["report_dt"], 0.) + r["gone"]
            for k in ("gone", "returned", "kept"):
                g[k] += r[k]
        for g in groups.values():
            if dim != "client" and g["gone"] < .02*total:
                continue
            series = [g["m"].get(m, 0.) for m in order]
            sig = [v if (dim == "client" or v >= .01*total_m[m]) and v > 0 else 0
                   for v, m in zip(series, order)]
            streak = _streak(sig)
            rate = g["returned"]/g["gone"]*100 if g["gone"] else 0.
            if streak < 2 or rate >= RETURN_TARGET*100:
                continue
            growing = all(b > a for a, b in zip(series, series[1:])) and series[0] > 0
            found.append({"prio": prio, "dim": dim, "dim_title": title or ("ТБ" if tb_id is None else "ГОСБ"),
                          "name": g["name"], "streak": streak, "growing": growing,
                          "series": [(_month_label(m), v) for m, v in zip(order, series)],
                          "gone": g["gone"], "kept": g["kept"], "return_pct": rate,
                          "share": g["gone"]/total*100 if total else 0.})
    found.sort(key=lambda p: (p["prio"], -p["kept"], -p["streak"]))
    caps = {d: c for d, _, c in PATTERN_DIMS}
    picked, per_dim = [], {}
    for p in found:
        if per_dim.get(p["dim"], 0) >= caps[p["dim"]]:
            continue
        per_dim[p["dim"]] = per_dim.get(p["dim"], 0) + 1
        picked.append(p)
        if len(picked) >= n:
            break
    return picked


def _segments(rows: list, previous: list, comparable: bool, return_available: bool) -> dict:
    """Оттоки по сегментам: B2B (3+ ФЛ, цель 50%) и 1–2 получателя (цель 15%)."""
    out = {}
    for kind, target in (("B2B", RETURN_TARGET), ("B2C", RETURN_TARGET_B2C)):
        cur = [r for r in rows if r["kind"] == kind]
        old = [r for r in previous if r["kind"] == kind]
        names = [x for x in segments.ORDER if any(r["seg"] == x for r in cur)]
        names += sorted({r["seg"] for r in cur} - set(names))
        table = []
        for name in names + ["Итого"]:
            part = cur if name == "Итого" else [r for r in cur if r["seg"] == name]
            before = old if name == "Итого" else [r for r in old if r["seg"] == name]
            gone, ret = _sum(part), _sum(part, "returned")
            rate = ret/gone*100 if gone and return_available else None
            table.append({
                "name": name, "gone": gone, "total": name == "Итого",
                "change": change(gone, _sum(before) if comparable else None),
                "returned": ret if return_available else None, "rate": rate,
                "gap": ret - target*gone if return_available else None,
                "gap_pp": rate - target*100 if rate is not None else None})
        out[kind] = {"target": target, "rows": table}
    return out


def _card(rows: list, key, title: str, scope: str, skip: set = frozenset()) -> dict:
    groups: dict = {}
    for r in rows:
        k = key(r)
        if not k or k in skip:
            continue
        groups[k] = groups.get(k, 0.) + r["gone"]
    total = _sum(rows)
    known = sum(groups.values())
    items = sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))
    top = items[:TOP_N]
    head = items[:2]
    return {"title": title, "scope": scope, "total": total,
            "known_pct": known/total*100 if total else 0.,
            "top_share": sum(v for _, v in top)/total*100 if total else None,
            "lead": [k for k, _ in head],
            "lead_share": sum(v for _, v in head)/total*100 if total else None,
            "items": [{"name": k, "gone": v} for k, v in top]}


def _cards(b2b: list, tb_id) -> list:
    """Концентрация оттоков B2B: четыре разреза, топ-5 в каждом."""
    rgs = [r for r in b2b if r["seg"] == "РГС"]
    other = [r for r in b2b if r["seg"] != "РГС"]
    cards = [_card(rgs, lambda r: r["agency"], "Ведомства · РГС", "РГС",
                   {agency.UNKNOWN})]
    ind = _card(other, lambda r: r["industry"], "Отрасль · без РГС",
                "B2B вне РГС", NOT_INDUSTRY)
    if len(ind["items"]) < 3:
        hold = _card(other, lambda r: r["holding"], "Холдинг · без РГС",
                     "B2B вне РГС")
        if len(hold["items"]) >= len(ind["items"]):
            ind = hold
    ind["required"] = True   # разрез запрошен явно: пустой показываем с пояснением
    cards.append(ind)
    if tb_id is None:
        cards.append(_card(b2b, lambda r: r["tb_name"], "Территориальные банки", "B2B"))
        cards.append(_card(b2b, lambda r: f'{r["gosb_name"]} · {r["tb_name"]}', "ГОСБ", "B2B"))
    else:
        cards.append(_card(b2b, lambda r: r["gosb_name"], "ГОСБ", "B2B"))
        cards.append(_card(b2b, lambda r: r["region"], "Регионы обслуживания", "B2B"))
    return [c for c in cards if c["total"] and (c["items"] or c.get("required"))]


def _leader(rows: list, source: Source, tb_id) -> dict:
    """Блок «Фокусы внимания руководителя» поверх данных всего окна."""
    months = source.dates.get("out_months", [])
    b2b = [r for r in rows if r["kind"] == "B2B"]
    return {"t2": _t2_rating(rows, source, tb_id),
            "top": _top_cases(b2b, tb_id, months),
            "patterns": _patterns(b2b, tb_id, months),
            "months": [_month_label(m) for m in reversed(months)]}


def analyze(source: Source | None, tb_id: int | None, today) -> dict:
    if source is None:
        return {}
    rows, tasks = _records(source, tb_id)
    current = [r for r in rows if r["report_dt"] in source.dates.get("out_months", [])]
    previous = [r for r in rows if r["report_dt"] in source.dates.get("out_prev_months", [])]
    report = _report(current, previous, tasks, source, pd.Timestamp(today).date())
    report["leader"] = _leader(current, source, tb_id)
    report["segments"] = _segments(current, previous, report["comparable"],
                                   report["return_available"])
    report["cards"] = _cards([r for r in current if r["kind"] == "B2B"], tb_id)
    report["by_seg"] = {
        seg: _report([r for r in current if r["seg"] == seg],
                     [r for r in previous if r["seg"] == seg], tasks, source,
                     pd.Timestamp(today).date())
        for seg in sorted({r["seg"] for r in current+previous})}
    return report
