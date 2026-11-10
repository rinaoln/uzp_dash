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
        + _cards(o.get("cards") or [])
        + '<details class="oa-cases"><summary>Регионы обслуживания — полная таблица</summary>'
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
        + '</div></details><details class="oa-cases"><summary>Крупнейшие клиенты и причины — до 15 случаев</summary>'
        '<p class="oa-note">Случай — клиент × регион × причина. Клиент с несколькими '
        'причинами или регионами может занимать несколько строк. «Не вернулись» — '
        'остаток на дату сборки, который ещё можно вернуть.</p><div class="tbl-scroll">'
        + C.table(["Организация", "Регион", "ГОСБ", "Отток, чел", "Не вернулись, чел", "Причина / контекст"],
                  case_rows, [3, 4]) + '</div></details>', "oa-concentration")


def _tone(value, good: float, warn: float) -> str:
    if value is None:
        return '<span class="oa-muted">—</span>'
    kind = "good" if value >= good else "warn" if value >= warn else "bad"
    return f'<span class="oa-t-{kind}">{value:.0f}%</span>'


def _service_note(s: dict) -> str:
    """Почему у ролей сервиса пусто: что у них есть по клиентам с оттоком на самом деле."""
    if not s.get("available"):
        return ""
    if not s["total"]:
        return ('<p class="oa-note"><b>Роли сервиса.</b> По клиентам с оттоком в месяц ухода '
                'или следующий у ролей сервиса (РН по сервису, МС/СМС) задач нет ни одного типа.</p>')
    other = ", ".join(f'«{C.esc(k)}» — {C.fmt_num(v)}' for k, v in s["other"])
    lost = s["outflow"] - s["outflow_same"]
    parts = [f'<b>Роли сервиса.</b> По клиентам с оттоком у ролей сервиса (РН по сервису, '
             f'МС/СМС) {C.fmt_num(s["total"])} задач в месяц ухода или следующий']
    if s["outflow"]:
        parts.append(f'типа «Отток» — {C.fmt_num(s["outflow"])}'
                     + (f', из них {C.fmt_num(lost)} заведены на другой ГОСБ внутри ТБ и в '
                        'таблицу не попали' if lost else ''))
    else:
        parts.append('типа «Отток» среди них нет')
    if other:
        parts.append(f'других типов: {other} — в таблицу они не входят, она считает только задачи на отток')
    return '<p class="oa-note">' + "; ".join(parts) + '.</p>'


def _quality(t: dict) -> str:
    """Качество отработки: вывод сотрудника в задаче против факта возврата."""
    q = t.get("quality")
    if not q or not t["tasks"]:
        return ""
    p, c = q["promised"], q["confirmed"]
    return (
        '<h4 class="oa-h4">Качество отработки задач</h4>'
        '<div class="oa-task-metrics">'
        f'<div><span>{C.esc(p["label"])}</span><b class="oa-t-bad">{C.fmt_num(p["flagged"])} {C.plural(p["flagged"], "задача", "задачи", "задач")}</b>'
        f'<small>из {C.fmt_num(p["tasks"])} с причиной «вернутся»</small></div>'
        f'<div><span>Не вернулись по этим задачам</span><b>{C.fmt_num(p["kept"])} ФЛ</b>'
        f'<small>из {C.fmt_num(p["gone"])} ФЛ оттока</small></div>'
        f'<div><span>{C.esc(c["label"])}</span><b class="oa-t-warn">{C.fmt_num(c["flagged"])} {C.plural(c["flagged"], "задача", "задачи", "задач")}</b>'
        f'<small>из {C.fmt_num(c["tasks"])} с причиной «ушли окончательно»</small></div>'
        f'<div><span>Вернулись по этим задачам</span><b>{C.fmt_num(c["returned"])} ФЛ</b>'
        f'<small>из {C.fmt_num(c["gone"])} ФЛ оттока</small></div></div>'
        '<p class="oa-note">Категория задачи — по причине из чек-листа и справочнику '
        '«Качество отработки». «Обещали вернуть» — причина временная (перенос выплат, '
        'наличные, текучка, разовое зачисление), а по клиентам задачи вернулись не все ФЛ. '
        '«Подтверждённый отток» — уход назван окончательным (другой банк, ликвидация, '
        'сокращение, политика головной компании), а возврат по клиентам есть. Причина '
        f'распознана по справочнику у {C.fmt_num(q["classified"])} из {C.fmt_num(t["tasks"])} '
        'задач; недозвон, «пункт не актуален» и отказ от разговора в категории не входят. '
        'ФЛ считаются по клиентам без повторов.</p>')


def tasks(o: dict) -> str:
    if not o.get("available"):
        return ""
    t = o["tasks"]
    if not t["available"]:
        return C.card('<h3>Работа с задачами на отток B2B</h3>'
                      '<p class="source-note">Данные задач недоступны. Покрытие и роли нельзя оценить.</p>')
    uncovered = t["clients"]-t["covered"]
    goal = 50
    rows = []
    # Роли без задач в таблицу не выводятся: строка из прочерков ничего не говорит,
    # а что анализ шёл по всем ролям, сказано в пояснении над таблицей.
    active = [r for r in t["roles"] if r["tasks"]]
    for r in active:
        rows.append([
            f'<b>{C.esc(r["label"])}</b>' + (f'<small class="oa-cell-note">{C.esc(r["role"])}</small>'
                                             if r["label"] != r["role"] else ""),
            f'{C.fmt_num(r["tasks"])} <small class="oa-muted">/ {C.fmt_num(r["fl"])} ФЛ</small>',
            _tone(r["worked_pct"], 55, 45)
            + f' <small class="oa-muted">/ {C.fmt_num(r["fl_worked"])} ФЛ</small>',
            _tone(r["return_pct"], goal, goal*.8)
            + f' <small class="oa-muted">/ {C.fmt_num(r["fl_returned"])} ФЛ</small>',
            C.fmt_num(r["success"]),
            C.fmt_num(r["open"]),
            C.fmt_num(r["overdue"])])
    return C.card(
        '<h3>Покрытие задачами и отработка по ролям</h3>'
        '<div class="oa-task-metrics">'
        f'<div><span>Клиенты с задачей на отток</span><b>{_pct(t["percent"])}</b>'
        f'<small>{C.fmt_num(t["covered"])} из {C.fmt_num(t["clients"])} клиентов</small></div>'
        f'<div><span>Клиенты без задачи на отток</span><b>{C.fmt_num(uncovered)}</b>'
        '<small>Нужны назначение и ответственный</small></div>'
        f'<div><span>Доля оттока у клиентов с задачей</span><b>{_pct(t["gone_percent"])}</b>'
        f'<small>{C.fmt_num(t["covered_gone"])} из {C.fmt_num(t["gone"])} чел · '
        f'{C.fmt_num(t["tasks"])} задач</small></div>'
        f'<div class="oa-accent"><span>Завершено возвратом</span><b>{C.fmt_num(t["covered_returned"])} ФЛ</b>'
        f'<small>{_pct(t["covered_return_pct"])} от ФЛ в задачах</small></div></div>'
        + _quality(t)
        + '<h4 class="oa-h4">Отработка задач по ролям</h4>'
        '<p class="oa-note">В анализе участвовали все роли воронки. Если роли нет в '
        f'таблице, значит, задач на отток у неё за период {C.esc(o["dates"].get("out_label", ""))} '
        'не было. «Задач / ФЛ» — число задач роли и отток клиентов, к которым они привязаны. '
        '«Отработано» — доля закрытых задач и ФЛ по ним. «Возврат» — подтверждённый '
        'витриной возврат ФЛ по клиентам роли, цель 50%. Клиент с задачами нескольких '
        'ролей учитывается в каждой, поэтому сумма ФЛ по ролям больше итога.</p>'
        + ('<div class="tbl-scroll">' + C.table(
            ["Роль", "Задач / ФЛ", "Отработано, % / ФЛ", "Возврат, % / ФЛ",
             "Успешно закрыты", "Открыты", "Просрочены"],
            rows, [1, 2, 3, 4, 5, 6]) + '</div>' if rows else
           '<p class="oa-lead">Задач на отток за период нет ни у одной роли.</p>')
        + _service_note(t.get("service") or {})
        + '<p class="oa-note">Клиент — ИНН в подразделении выбранного уровня '
        '(в сети: ТБ × ИНН; внутри ТБ: ГОСБ × ИНН). Учитываются только задачи типа '
        '«Отток», созданные в месяц ухода или следующий месяц; один код задачи '
        'учитывается один раз. Прочие контакты не заменяют задачу на отток.</p>',
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


# ==================== Фокусы руководителя ================================== #

def _signed(value, digits: int = 0, unit: str = "") -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f'{sign}{C.fmt_num(abs(value), digits=digits)}{unit}'


def _loss(share) -> str:
    """Доля невозвращённых: больше половины — красным (цель возврата 50%)."""
    kind = "bad" if share > 50 else "warn" if share > 40 else "good"
    return f'<span class="oa-t-{kind}">{share:.0f}%</span>'


def _rating_list(title: str, items: list, empty: str) -> str:
    if not items:
        return (f'<div class="oa-rank"><h5>{C.esc(title)}</h5>'
                f'<p class="oa-muted">{C.esc(empty)}</p></div>')
    li = "".join(
        f'<li><span class="oa-rank-n">{i}</span><span class="oa-rank-name">{C.esc(g["name"])}'
        + (f'<small>{C.esc(g["note"])}</small>' if g.get("note") else "")
        + f'</span><span class="oa-rank-v">{_loss(g["share"])}'
        f'<small>{C.fmt_num(g["kept"])} из {C.fmt_num(g["gone"])} ФЛ</small></span></li>'
        for i, g in enumerate(items, 1))
    return f'<div class="oa-rank"><h5>{C.esc(title)}</h5><ol>{li}</ol></div>'


def _fold(summary: str, inner: str) -> str:
    """Сворачиваемая деталь фокуса: на виду вывод, таблицы — по клику."""
    return (f'<details class="oa-fold"><summary>{summary}</summary>'
            f'<div class="oa-fold-body">{inner}</div></details>')


def _focus_head(n: int, title: str) -> str:
    return (f'<div class="oa-focus-head"><span class="oa-focus-n">{n}</span>'
            f'<h4>{title}</h4></div>')


def _focus_t2(t: dict) -> str:
    month = f' · {C.esc(t.get("month_label"))}' if t.get("month_label") else ""
    head = _focus_head(1, f'Т-2{month}: «отток не подтверждён» против факта возврата')
    if not t.get("available"):
        return head + '<p class="source-note">Данные задач недоступны — рейтинг не построить.</p>'
    if not t.get("in_window"):
        return head + (f'<p class="source-note">Месяц Т-2 ({C.esc(t["month_label"])}) не входит '
                       'в доступный срез оттока.</p>')
    if not t.get("rows"):
        return head + ('<p class="oa-lead">Задач по оттоку с выводом «отток не подтверждён» '
                       f'в Т-2 нет (задач на отток с привязкой к уходу: '
                       f'{C.fmt_num(t.get("tasks_rows", 0))}).</p>')
    share, base = t["share"], t["confirmed_share"]
    compare = (f' · при подтверждённом оттоке — {base:.0f}%' if base is not None else "")
    lead = (f'<p class="oa-lead">{C.fmt_num(t["clients"])} клиентов закрыты как «отток не '
            f'подтверждён»: из {C.fmt_num(t["gone"])} ФЛ не вернулись '
            f'<b class="oa-t-bad">{C.fmt_num(t["kept"])} ({share:.0f}%)</b>{compare}.</p>')
    if not t.get("return_ok"):
        lead += '<p class="source-note">Возвраты за Т-2 поступили не полностью — доля может снизиться.</p>'
    worst = [(lbl, items[0]) for lbl, items in
             (("роль", t["roles"]), ("МЗП", t["mzp"]), (t["unit_label"], t["units"])) if items]
    summary = ("Рейтинг ролей, МЗП и " + C.esc(t["unit_label"])
               + (' · хуже всех: ' + " · ".join(
                   f'{C.esc(lbl)} <b>{C.esc(g["name"])}</b> {_loss(g["share"])}' for lbl, g in worst)
                  if worst else ""))
    lists = ('<div class="oa-ranks">'
             + _rating_list("Роли", t["roles"], "Нет ролей с оттоком от 5 ФЛ")
             + _rating_list("МЗП", t["mzp"], "Нет МЗП с оттоком от 5 ФЛ")
             + _rating_list(t["unit_label"], t["units"], "Нет подразделений с оттоком от 5 ФЛ")
             + '</div><p class="oa-note">«Отток не подтверждён» — задача «Отток» закрыта с '
               'выводом, что ухода нет или он временный (сезон, отпуска, «портфель без '
               'изменений», «сотрудники вернутся»). Доля — невернувшиеся ФЛ ÷ ушедшие ФЛ; в '
               'рейтинг входят группы от 5 ушедших ФЛ. МЗП — автор задачи с ролью МЗП (ФИО из '
               'витрины оттока, иначе табельный номер).</p>')
    return head + lead + _fold(summary, lists)


def _pattern_detail(p: dict, months: list) -> str:
    """Организации закономерности — таблица под строкой, открывается по клику."""
    rows = []
    for g in p.get("detail") or []:
        series = " → ".join(C.fmt_num(v) for v in g["month_list"])
        rows.append([
            f'<b>{C.esc(g["name"])}</b><small class="oa-cell-note">Орг. {g["inn"]}</small>',
            C.esc(g["gosb"]) + (f'<small class="oa-cell-note">МЗП: {C.esc(g["mzp"])}</small>'
                                if g["mzp"] else ""),
            series,
            f'<b class="oa-t-bad">{C.fmt_num(g["kept"])}</b> <small class="oa-muted">из {C.fmt_num(g["gone"])}</small>',
            _tone(g["return_pct"], 50, 40),
            C.esc(g["state"]) + f'<small class="oa-cell-note">{C.esc(g["reason"])}</small>'])
    if not rows:
        return ""
    total = p.get("detail_total", len(rows))
    more = (f'<p class="oa-note">Показаны {len(rows)} из {C.fmt_num(total)} организаций — '
            'по числу невернувшихся. Полный список — в выгрузке Excel раздела.</p>'
            if total > len(rows) else "")
    head = f"Отток по месяцам ({' → '.join(months)})" if months else "Отток по месяцам"
    return ('<div class="tbl-scroll">' + C.table(
        ["Организация", "ГОСБ", head, "Не вернулись, ФЛ", "Возврат", "Задача и причина"],
        rows, [2, 3, 4]) + '</div>' + more)


def _pattern_li(p: dict, months: list | None = None) -> str:
    series = " → ".join(f'{m} — {C.fmt_num(v)}' for m, v in p["series"])
    arrow = " ↑" if p["growing"] else ""
    share = f' · {p["share"]:.0f}% B2B' if p["dim"] != "client" else ""
    line = (f'<b>{C.esc(p["name"])}</b> '
            f'<span class="oa-chip">{C.esc(p["dim_title"])}</span> '
            f'{p["streak"]} мес. подряд{arrow} · возврат '
            f'<b class="oa-t-bad">{p["return_pct"]:.0f}%</b> · не вернулись '
            f'{C.fmt_num(p["kept"])} ФЛ{share}')
    detail = _pattern_detail(p, months or [])
    if not detail:
        return f'<li title="{C.esc(series)} ФЛ">{line}</li>'
    n = p.get("detail_total", 0)
    link = f'детализация: {C.fmt_num(n)} орг. ›' if n > 1 else 'детализация ›'
    return (f'<li><details class="oa-pat"><summary title="{C.esc(series)} ФЛ">{line} '
            f'<span class="oa-pat-link">{link}</span></summary>'
            f'<div class="oa-pat-body">{detail}</div></details></li>')


def _focus_top(leader: dict, unit: str) -> str:
    top = leader.get("top") or []
    head = _focus_head(2, "Личное вовлечение: топ-5 оттоков и закономерности за 3 месяца")
    if not top:
        body = '<p class="oa-lead">Невозвращённых потерь B2B в окне нет.</p>'
    else:
        rows = []
        for i, g in enumerate(top, 1):
            months = " → ".join(f'{C.fmt_num(v)}' for _, v in g["month_list"])
            group = " · ".join(x for x in (g["seg"], g["group"]) if x)
            rows.append([
                f'<span class="oa-rank-n">{i}</span>',
                f'<b>{C.esc(g["name"])}</b><small class="oa-cell-note">Орг. {g["inn"]} · {C.esc(group)}</small>',
                C.esc(g["unit"]) + (f'<small class="oa-cell-note">{C.esc(g["gosb"])}</small>'
                                    if g["gosb"] != g["unit"] else ""),
                C.esc(g["mzp"] or "—"),
                f'{months}<small class="oa-cell-note">'
                + ("отток " + str(g["streak"]) + " мес. подряд" if g["streak"] >= 2 else "разовый уход")
                + '</small>',
                f'<b class="oa-t-bad">{C.fmt_num(g["kept"])}</b> <small class="oa-muted">из {C.fmt_num(g["gone"])}</small>',
                _tone(g["return_pct"], 50, 40),
                C.esc(g["state"]) + f'<small class="oa-cell-note">{C.esc(g["reason"])}</small>',
                C.esc(g["action"])])
        months = " → ".join(leader.get("months") or [])
        table = ('<div class="tbl-scroll">' + C.table(
            ["#", "Организация", unit, "МЗП", f"Отток по месяцам ({months})",
             "Не вернулись, ФЛ", "Возврат", "Задача и причина", "Что сделать"],
            rows, [5, 6]) + '</div>')
        kept = sum(g["kept"] for g in top)
        no_task = sum(g["state"] == "Задачи на отток нет" for g in top)
        repeat = sum(g["streak"] >= 2 for g in top)
        facts = [f'не вернулись <b class="oa-t-bad">{C.fmt_num(kept)} ФЛ</b>']
        if no_task:
            facts.append(f'без задачи на отток — <b>{no_task} из {len(top)}</b>')
        if repeat:
            facts.append(f'уходят повторно — <b>{repeat}</b>')
        names = ", ".join(C.esc(g["name"]) for g in top[:3])
        body = (f'<p class="oa-lead">Топ-{len(top)}: {"; ".join(facts)}.</p>'
                + _fold(f'Таблица топ-{len(top)} с действиями · {names}'
                        + ("…" if len(top) > 3 else ""), table))
    pats = leader.get("patterns") or []
    pm = leader.get("months") or []
    if pats:
        shown, rest = pats[:3], pats[3:]
        body += ('<h5 class="oa-h5">Закономерности концентрации</h5>'
                 f'<ul class="oa-patterns">{"".join(_pattern_li(p, pm) for p in shown)}</ul>')
        note = ('<p class="oa-note">Группа попадает в вывод, если отток B2B идёт не меньше '
                'двух месяцев подряд, а возврат ниже цели 50%. Для ведомства, отрасли, '
                'сегмента и подразделения месяц считается, если на группу пришлось от 1% '
                'оттока B2B уровня. Порядок: ведомства, отрасли, клиенты, затем подразделения '
                'и сегменты как фон. Помесячные значения — в подсказке при наведении, '
                'организации закономерности — по клику на её строку.</p>')
        more = (f'<ul class="oa-patterns">{"".join(_pattern_li(p, pm) for p in rest)}</ul>'
                if rest else "")
        body += _fold((f'Ещё {len(rest)} · ' if rest else "") + 'как отбираются закономерности',
                      more + note)
    else:
        body += ('<p class="oa-note">Устойчивых закономерностей нет: ни одна группа не '
                 'теряет получателей два месяца подряд с возвратом ниже цели.</p>')
    return head + body


def leader(o: dict, unit: str = "Подразделение") -> str:
    """Вывод в начале раздела: на что руководителю смотреть в первую очередь."""
    if not o.get("available") or not o.get("leader"):
        return ""
    ld = o["leader"]
    return C.card(
        '<div class="oa-leader-title"><h3>Фокусы внимания руководителя</h3>'
        f'<span class="oa-muted">Окно: {C.esc(o["dates"].get("out_label", ""))}</span></div>'
        + '<div class="oa-focus">' + _focus_t2(ld.get("t2") or {}) + '</div>'
        + '<div class="oa-focus">' + _focus_top(ld, unit) + '</div>',
        "oa-leader")


def _seg_table(title: str, data: dict) -> str:
    target = data["target"]*100
    rows, attrs = [], []
    for r in data["rows"]:
        d = r["change"]
        dyn = ('<span class="oa-muted">—</span>' if d["percent"] is None else
               f'<span class="{"oa-t-bad" if d["percent"] > 0 else "oa-t-good"}">'
               f'{_signed(d["percent"], 1, "%")}</span>')
        ret = ("—" if r["returned"] is None else
               f'{C.fmt_num(r["returned"])}<small class="oa-cell-note">'
               + _tone(r["rate"], target, target*.8) + '</small>')
        gap = ("—" if r["gap"] is None else
               f'<span class="{"oa-t-bad" if r["gap"] < 0 else "oa-t-good"}">{_signed(r["gap"])}</span>'
               f'<small class="oa-cell-note">{_signed(r["gap_pp"], 1, " п.п.")}</small>')
        name = f'<b>{C.esc(r["name"])}</b>'
        rows.append([name, C.fmt_num(r["gone"]), dyn, ret, gap])
        attrs.append(' class="oa-total-row"' if r["total"] else "")
    return (f'<div class="oa-seg"><div class="oa-seg-head"><h4>{C.esc(title)}</h4>'
            f'<span class="oa-muted">цель возврата {target:.0f}%</span></div>'
            + C.table(["Сегмент", "Отток ФЛ", "Динамика", "Возврат ФЛ, %", "Потери до цели"],
                      rows, [1, 2, 3, 4], attrs) + '</div>')


def segments(o: dict) -> str:
    if not o.get("available") or not o.get("segments"):
        return ""
    sg = o["segments"]
    note = ('Динамика — год к году за те же три месяца.' if o.get("comparable") else
            'Динамика год к году недоступна: нет всех месяцев прошлого года.')
    return C.card(
        '<h3>Оттоки по сегментам</h3>'
        '<div class="oa-segs">'
        + _seg_table("B2B (3+ ФЛ)", sg["B2B"]) + _seg_table("1–2 получателя", sg["B2C"])
        + '</div><p class="oa-note">' + note + ' «Потери до цели» — сколько ФЛ не хватает '
        'до цели возврата (возврат − цель × отток) и разрыв в процентных пунктах. '
        'Снижение оттока — зелёным, рост — красным.</p>', "oa-segments")


def _cards(cards: list) -> str:
    if not cards:
        return ""
    out = []
    for i, c in enumerate(cards, 1):
        mx = max((it["gone"] for it in c["items"]), default=0) or 1
        lead = (" и ".join(c["lead"]) + f' — {c["lead_share"]:.0f}% {c["scope"]}'
                if c["lead"] else "Нет данных")
        items = "".join(
            f'<li><div><span>{j}. {C.esc(it["name"])}</span><b>{C.fmt_num(it["gone"])}</b></div>'
            f'<i style="width:{it["gone"]/mx*100:.1f}%"></i></li>'
            for j, it in enumerate(c["items"], 1))
        cover = (f'<small class="oa-muted">определено для {c["known_pct"]:.0f}% оттока</small>'
                 if c["known_pct"] < 99.5 else "")
        if not c["items"]:
            out.append(
                f'<div class="oa-cc oa-cc{i}"><span class="oa-cc-t">{C.esc(c["title"])}</span>'
                '<div class="oa-cc-big"><b>—</b></div>'
                '<p class="oa-muted">В витринах ЕПК и ключевых клиентов отрасль для этого '
                f'оттока не заполнена или указана как «Прочее» ({C.fmt_num(c["total"])} ФЛ).</p></div>')
            continue
        out.append(
            f'<div class="oa-cc oa-cc{i}"><span class="oa-cc-t">{C.esc(c["title"])}</span>'
            f'<div class="oa-cc-big"><b>{c["top_share"]:.0f}%</b><span>топ-5</span></div>'
            f'<p class="oa-cc-lead">{C.esc(lead)}</p>{cover}<ol>{items}</ol></div>')
    return ('<div class="oa-ccs">' + "".join(out) + '</div>'
            '<p class="oa-note">Крупный процент — доля топ-5 групп в оттоке B2B разреза '
            '(для ведомств — в оттоке РГС, для отрасли — в оттоке вне РГС). Ведомство '
            'определяется по наименованию организации и флагу силовых структур, отрасль — '
            'по витрине ЕПК; неопределённые и «Прочее» в топ не входят, но остаются в '
            'знаменателе.</p>')
