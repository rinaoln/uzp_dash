"""Визуальная иерархия оттока: масштаб → возврат → концентрация → задачи."""
from __future__ import annotations

from ...render import components as C


def _pct(value) -> str:
    return f"{value:.1f}%" if value is not None else "—"


def delta(d: dict) -> str:
    value, pct = d.get("absolute"), d.get("percent")
    if value is None:
        return '<span class="oa-muted">Нет сопоставимых данных</span>'
    kind = "negative" if value > 0 else "positive" if value < 0 else "oa-muted"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    absolute = f'{sign}{C.fmt_num(abs(value))} чел'
    percent = f'{sign}{abs(pct):.1f}%' if pct is not None else "база 0; % не определён"
    return f'<span class="{kind}">{absolute} · {percent}</span>'


def overview(o: dict) -> str:
    if not o:
        return ""
    if not o.get("available"):
        return C.card('<h3>Отток B2B и B2C</h3><p class="source-note">'
                      + C.esc(o.get("error") or "Данные оттока недоступны.") + '</p>')
    dates = o["dates"]
    available = dates.get("out_available", [])
    stamp = dates["out_asof"].strftime("%d.%m.%Y")
    notes = [f'Окно: {C.esc(dates["out_label"])}. Последний срез — {stamp}.']
    if not o["complete"]:
        notes.append(f'<b>Неполное окно: доступны {len(available)} из 3 месяцев.</b> '
                     'Итог отражает только поступившие данные.')
    if o.get("lag_months", 0):
        notes.append(f'Срез отстаёт от закрытого месяца на {o["lag_months"]} мес.')
    if o["comparable"]:
        notes.append('Год к году — те же три месяца прошлого года.')
    else:
        notes.append('Для сравнения год к году нужны все три месяца обоих лет; '
                     'пропуски не заменяются нулями.')
    b2b_share = o["b2b"] / o["total"] * 100 if o["total"] else 0
    return (C.card(
        '<div class="oa-total"><div><h3>Общий отток получателей</h3>'
        f'<div class="oa-number">{C.fmt_num(o["total"])} <small>чел</small></div></div>'
        '<div class="oa-yoy"><span>Изменение год к году</span><strong>'
        + delta(o["change"]) + '</strong></div></div>'
        + '<p class="oa-note">' + " ".join(notes) + '</p>'
        + '<div class="oa-split-bar" aria-hidden="true">'
        f'<i style="width:{b2b_share:.2f}%"></i></div>'
        + '<div class="oa-split">'
        + _part("B2B · от 3 получателей", o["b2b"], b2b_share, o["b2b_change"])
        + _part("B2C · 1–2 получателя", o["b2c"], 100-b2b_share if o["total"] else 0, o["b2c_change"])
        + '</div><p class="oa-note">B2B/B2C определяется по числу ушедших в одной '
        'организации и исходном ГОСБ за месяц. За окно суммируются месячные события '
        'ухода; один человек может учитываться в разных месяцах.</p>', "oa-overview")
        + _return(o))


def _part(label: str, count: float, share: float, yoy: dict) -> str:
    return ('<div><h4>' + label + '</h4>'
            f'<b>{C.fmt_num(count)} <small>чел · {share:.1f}% от общего</small></b>'
            + '<p>Год к году: ' + delta(yoy) + '</p></div>')


def _return(o: dict) -> str:
    execution = o["execution"]
    missing = not o["return_source_ok"]
    state = 'Цель достигнута' if execution is not None and execution >= 100 else 'Цель пока не достигнута'
    if missing:
        state = 'Возвраты поступили не за все месяцы'
    elif not o["complete"]:
        state = 'Возврат по доступным месяцам'
    if o["b2b"] and not o["return_available"]:
        state = 'Данные возврата ещё не поступили'
    if not o["b2b"]:
        state = 'Оттока B2B в доступных данных нет'
    fill = min(100, execution or 0)
    color = "good" if (execution or 0) >= 100 else "warn"
    return C.card(
        '<div class="oa-return-head"><div><h3>Возврат оттока B2B</h3>'
        '<p class="oa-note">Цель — вернуть 50% ушедших получателей B2B</p></div>'
        f'<span class="oa-state {color}">{state}</span></div>'
        + '<div class="oa-return-metrics">'
        f'<div><span>Подтверждённый возврат</span><b>{C.fmt_num(o["returned"]) if o["return_available"] else "—"} чел</b>'
        f'<small>{_pct(o["return_rate"])} от оттока B2B</small></div>'
        f'<div><span>Цель в получателях</span><b>{C.fmt_num(o["return_target"], digits=1)} чел</b>'
        f'<small>50% × {C.fmt_num(o["b2b"])} чел</small></div>'
        f'<div><span>Выполнение цели</span><b>{_pct(execution)}</b>'
        '<small>Возврат ÷ цель в получателях</small></div>'
        f'<div><span>Осталось вернуть до цели</span><b>{C.fmt_num(o["return_gap"], digits=1) if o["return_gap"] is not None else "—"} чел</b>'
        '<small>По подтверждённым возвратам</small></div></div>'
        f'<div class="oa-progress" role="progressbar" aria-label="Выполнение цели возврата B2B" '
        f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{fill:.1f}">'
        f'<i style="width:{fill:.2f}%"></i></div>'
        '<p class="oa-note">Возврат берётся из витрины возвратов по тому же ИНН, '
        'ГОСБ и месяцу ухода, в пределах численности оттока. Успешное закрытие задачи '
        'не заменяет фактический возврат. Если записи возврата нет, возврат не подтверждён. '
        'B2C в цель не входит.</p>', "oa-return")


def concentration(o: dict) -> str:
    if not o.get("available"):
        return ""
    regions = o.get("regions", [])
    if not regions:
        return C.card('<h3>Концентрация оттоков B2B</h3><p>В доступных данных оттока B2B нет.</p>')
    total = o["b2b"]
    lead = regions[0]
    share = lead["gone"]/total*100 if total else 0
    region_rows = [[C.esc(r["name"]), C.fmt_num(r["clients"]), C.fmt_num(r["gone"]),
                    f'{r["gone"]/total*100:.1f}%' if total else "—", delta(r["change"]),
                    C.esc(r["leading_reason"])] for r in regions]
    case_rows = [[f'<b>{C.esc(r["name"])}</b><small class="oa-cell-note">Орг. {r["inn"]}</small>',
                  C.esc(r["region"]), C.esc(r["unit"]), C.fmt_num(r["gone"]),
                  C.fmt_num(r["kept"]), C.esc(r["reason"])
                  + (f'<small class="oa-cell-note">Источник: {C.esc(r["reason_source"])}</small>'
                     if r["reason_source"] else "")
                  + (f'<details class="oa-evidence"><summary>Комментарий сотрудника</summary>'
                     f'<p>{C.esc(r["evidence"])}</p></details>' if r["evidence"] else "")]
                 for r in o.get("cases", [])]
    known = o["known_reason_gone"]/total*100 if total else 0
    return C.card(
        '<h3>Концентрация оттоков B2B</h3>'
        f'<p class="oa-lead"><b>{C.esc(lead["name"])}</b> — '
        f'{C.fmt_num(lead["gone"])} чел, <b>{share:.1f}%</b> оттока B2B. '
        f'Крупнейшая категория в данных: {C.esc(lead["leading_reason"])}.</p>'
        '<p class="oa-note">География — регион обслуживания из справочника ГОСБ. '
        'Он может отличаться от места регистрации организации. Причины зафиксированы '
        f'для {known:.1f}% оттока B2B; остальные показаны отдельной категорией. '
        'Причина из задачи — объяснение сотрудника, а не установленный внешней проверкой факт.</p>'
        + '<div class="tbl-scroll">'
        + C.table(["Регион обслуживания", "Клиентов", "Отток B2B, чел", "Доля B2B",
                   "Год к году: чел · %", "Крупнейшая категория"], region_rows, [1, 2, 3])
        + '</div><details class="oa-cases" open><summary>Крупнейшие клиенты и причины — до 15 случаев</summary>'
        '<p class="oa-note">Случай — клиент × регион × причина. Клиент с несколькими '
        'причинами или регионами может занимать несколько строк. «Не вернулись» — '
        'остаток на дату сборки, который ещё можно вернуть.</p><div class="tbl-scroll">'
        + C.table(["Организация", "Регион", "ГОСБ", "Отток, чел", "Не вернулись, чел", "Причина / контекст"],
                  case_rows, [3, 4]) + '</div></details>', "oa-concentration")


def tasks(o: dict) -> str:
    if not o.get("available"):
        return ""
    t = o["tasks"]
    if not t["available"]:
        return C.card('<h3>Работа с задачами на отток B2B</h3>'
                      '<p class="source-note">Данные задач недоступны. Покрытие и роли нельзя оценить.</p>')
    uncovered = t["clients"]-t["covered"]
    rows = [[C.esc(r["role"]), C.fmt_num(r["tasks"]),
             f'{r["tasks"]/t["tasks"]*100:.1f}%' if t["tasks"] else "—",
             C.fmt_num(r["success"]), C.fmt_num(r["closed_other"]),
             C.fmt_num(r["open"]), C.fmt_num(r["overdue"])] for r in t["roles"]]
    return C.card(
        '<h3>Работа с задачами на отток B2B</h3>'
        '<div class="oa-task-metrics">'
        f'<div><span>Клиенты с задачей на отток</span><b>{_pct(t["percent"])}</b>'
        f'<small>{C.fmt_num(t["covered"])} из {C.fmt_num(t["clients"])} клиентов</small></div>'
        f'<div><span>Клиенты без задачи на отток</span><b>{C.fmt_num(uncovered)}</b>'
        '<small>Нужны назначение и ответственный</small></div>'
        f'<div><span>Доля оттока у клиентов с задачей</span><b>{_pct(t["gone_percent"])}</b>'
        f'<small>{C.fmt_num(t["covered_gone"])} из {C.fmt_num(t["gone"])} чел</small></div>'
        f'<div><span>Задач на отток</span><b>{C.fmt_num(t["tasks"])}</b>'
        '<small>Уникальных кодов задач</small></div></div>'
        '<p class="oa-note">Клиент — ИНН в подразделении выбранного уровня '
        '(в сети: ТБ × ИНН; внутри ТБ: ГОСБ × ИНН). Учитываются только задачи типа '
        '«Отток», созданные в месяц ухода или следующий месяц; один код задачи '
        'учитывается один раз. Прочие контакты не заменяют задачу на отток.</p>'
        + ('<div class="tbl-scroll">' + C.table(
            ["Роль", "Задач", "Доля", "Успешно закрыты", "Закрыты без успеха", "Открыты", "Из открытых просрочены"],
            rows, [1, 2, 3, 4, 5, 6]) + '</div>' if rows else '<p>Задачи на отток B2B не найдены.</p>'),
        "oa-tasks")


def reasons(o: dict) -> str:
    if not o.get("available"):
        return '<p class="oa-note">Данные для свода причин недоступны.</p>'
    rows = [[C.esc(r["name"]), C.fmt_num(r["clients"]), C.fmt_num(r["gone"]),
             C.fmt_num(r["kept"]),
             C.fmt_num(r["change"]["previous"]) if r["change"]["previous"] is not None else "—",
             delta(r["change"])] for r in o["reasons"]]
    if not rows:
        return '<p class="oa-note">Оттока B2B в доступных данных нет.</p>'
    known = o["known_reason_gone"]/o["b2b"]*100 if o["b2b"] else 0
    old_total = sum(r["change"]["previous"] or 0 for r in o["reasons"])
    old_known = o["previous_known_reason_gone"]/old_total*100 if old_total else 0
    coverage = f'Причины известны для {known:.1f}% текущего оттока B2B'
    if o["comparable"]:
        coverage += f' и {old_known:.1f}% оттока год назад'
    return ('<h4>Свод причин оттока B2B с динамикой год к году</h4>'
            '<p class="oa-note">Причина берётся из задачи в месяц ухода или следующий '
            'месяц, приоритет у чек-листа. Неизвестные причины не исключаются из итога. '
            'Один клиент может иметь разные причины в разных месяцах. '
            + coverage + '.</p>'
            '<div class="tbl-scroll">' + C.table(
                ["Причина", "Клиентов", "Отток, чел", "Не вернулись, чел", "Отток год назад, чел", "Год к году: чел · %"],
                rows, [1, 2, 3, 4]) + '</div>')
