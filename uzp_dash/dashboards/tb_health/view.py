"""Сборка apple-страницы дэша tb_health.

Отчёт двухуровневый и живёт в одном файле: вкладка СБ (единица разбора — ТБ) и по
вкладке на каждый ТБ (единица — ГОСБ). Структура уровня одна и та же: вердикт области,
из чего сложился прогноз, матрица «единица × сегмент», карточки единиц с оверлеем
разбора и — только на уровне ТБ — интерактивный список организаций к работе.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

from ...registry import Context, dashboard
from ...render import components as C
from ...render import page
from ... import progress
from . import analyze, bank, prompts, segments, snapshot

SEG_ORDER = segments.ORDER   # короткие названия сегментов (КСБ, РГС, …)

# Оформление отчёта собрано пользователем и лежит рядом, в assets/:
#   ux.css / ux.js.txt — его стиль и скрипт, скопированы из его файла БАЙТ В БАЙТ.
#     Скрипт перестраивает готовую страницу (боковая колонка, поля выбора ТБ/ГОСБ,
#     сворачивание разделов) и ищет разделы по заголовкам — поэтому заголовки ниже
#     совпадают с его SECTION_DESCRIPTIONS посимвольно. Руками эти файлы не
#     причёсываем: новая версия дизайна заменяется простой перезаписью.
#   ux-fix.css — наши правки поверх его стиля (например, чтобы «Разделы страницы»
#     влезали без ползунка); держим отдельно, чтобы ux.css оставался копией макета.
#   help.* — памятка «Как пользоваться дэшбордом», всплывающее окно при открытии.
#
# ПОЧЕМУ СКРИПТЫ ЛЕЖАТ ПОД РАСШИРЕНИЕМ .js.txt, а не .js
# Проект уезжает в закрытый контур почтой, а почтовый фильтр вырезает вложения с
# расширением .js — письмо доходило без двух файлов, и отчёт собирался без скрипта.
# Содержимое при этом остаётся байт в байт тем же: меняется только имя файла, и
# свойство «копия макета» сохраняется. Новую версию дизайна класть сюда так же —
# перезаписью, переименовав в .js.txt. На готовый HTML это никак не влияет: скрипт
# всё равно встраивается в документ текстом, отдельных .js-файлов отчёт не имеет.
_ASSETS = Path(__file__).with_name("assets")
TITLE = "Анализ портфеля получателей заработной платы"
# Подзаголовок — часть названия отчёта, а не подпись конкретной сборки: месяц
# прогноза и дата обновления стоят отдельной строкой ниже (см. `meta` в page).
SUBTITLE = "Прогноз выполнения планов"

# Легенда под шапкой — из дизайна пользователя; цвета подписаны границами, потому
# что раскраска везде идёт по одному правилу (C.status_of) и по одному числу —
# выполнению прогноза. Про невыполненные сегменты говорит бейдж на карточке, а не
# цвет: единица с выполнением 107% и одним слабым сегментом — зелёная.
_LEGEND = (
    '<div class="legend">\n'
    '<span class="lg-item"><span class="lg-dot good"></span><b>план выполняется</b> — '
    'от 100%</span>\n'
    '<span class="lg-item"><span class="lg-dot warn"></span><b>план близок к выполнению</b> — '
    '95–100%</span>\n'
    '<span class="lg-item"><span class="lg-dot bad"></span><b>план не выполняется</b> — '
    'ниже 95%</span>\n'
    '<span class="lg-item"><span class="lg-arrow">→</span>карточка кликабельна — открывает '
    'подробный разбор</span>\n'
    '<span class="lg-item"><span class="lg-arrow" style="border-radius:50%">▸</span>'
    'раскрывающийся список — клик показывает организации</span>\n'
    '</div>\n'
)


# Маска на время перестройки раскладки.
#
# ux.js стоит в самом конце документа и перекладывает готовую страницу: собирает
# боковую колонку, сворачивает разделы, выносит «Управление портфелем» в окно.
# Документ весит мегабайты, браузер успевает нарисовать исходную вёрстку раньше,
# чем дойдёт до скрипта, — и читатель на секунду видит «старый дизайн», который на
# самом деле есть просто неперестроенная разметка.
#
# Класс ставит САМ СКРИПТ, а не разметка. Если JS выключен или вырезан почтовым
# фильтром, класс не появится и страница останется видимой — пустой экран вместо
# отчёта был бы куда хуже мерцания. По той же причине стоит таймаут: ошибка внутри
# ux.js не должна оставить документ под маской навсегда.
_BOOT_JS = (
    '<script>document.documentElement.classList.add("js-boot");'
    'setTimeout(function(){document.documentElement.classList.remove("js-boot");},'
    '4000);</script>'
)
# Доводка раскладки ПОСЛЕ скрипта дизайна и снятие маски.
#
# Скрипт дизайна раскладывает блоки карточки ГОСБ в своём порядке: «Разбор по
# сегментам», затем отток, затем всё остальное — и динамика за 12 месяцев уезжает
# в самый низ. Читать её там поздно: это то, с чего разбор начинается. Переставляем
# сами, потому что порядок задаётся файлом дизайнера, а он остаётся копией макета.
_REVEAL_JS = """
<script>
(function(){
  function h4text(b){ var h = b.querySelector(':scope > h4'); return h ? h.textContent.trim() : ''; }
  document.querySelectorAll('dialog.gd .gd-sheet').forEach(function(sheet){
    var blocks = Array.prototype.slice.call(sheet.querySelectorAll(':scope > .gd-block'));
    var trend = null, seg = null;
    blocks.forEach(function(b){
      var t = h4text(b);
      if(t.indexOf('Динамика за 12 месяцев') === 0) trend = b;
      else if(t.indexOf('Разбор по сегментам') === 0) seg = b;
    });
    if(trend && seg && trend.compareDocumentPosition(seg) & Node.DOCUMENT_POSITION_PRECEDING){
      sheet.insertBefore(trend, seg);
    }
  });
  document.documentElement.classList.remove('js-boot');
})();
</script>
"""


def _asset(name: str) -> str:
    """Файл оформления как есть. newline="" — чтобы копия не отличалась от оригинала."""
    # open(), а не Path.read_text(newline=...): аргумент newline у read_text появился
    # только в Python 3.13, а на DataLab стоит версия старше
    with open(_ASSETS / name, encoding="utf-8", newline="") as f:
        return f.read()


@dashboard("tb_health")
def build(ctx: Context) -> str:
    """Отчёт СБ → ТБ → ГОСБ одним файлом.

    Отчёт всегда строится по ВСЕМУ банку: вкладка СБ (единица разбора — ТБ) и по
    вкладке на каждый ТБ (единица — ГОСБ). Разбор одного ТБ и разбор банка нужны
    рядом, а не в разных файлах, поэтому уровни живут вкладками одного документа.

    Порядок шагов задан ценой запросов, а не удобством чтения: сначала ОДИН проход в
    БД по всему банку (`bank.load`), затем все уровни считаются без БД (`prepare`),
    затем ОДИН запрос текстов активностей сразу по всем аудиторским пулам, и только
    потом идёт LLM. Раньше каждый из 13 уровней ходил в БД сам, и одни и те же
    таблицы читались по 12 раз.
    """
    prompts.reset_gateway()
    b = bank.load(ctx)

    # Порядок вкладок — алфавитный, и сортируется он здесь, а не полагается на
    # ORDER BY: сортировка строк в СУБД зависит от collation базы, и в закрытом
    # контуре «СибБ» могло встать перед «СЗБ» просто из-за регистра.
    levels_tb = sorted(((int(r.tb_id), str(r.tb_short_name), str(r.tb_full_name))
                        for r in b.tbs.itertuples()),
                       key=lambda x: analyze._ru_key(x[1]))
    preps = []
    for i, (tb_id, short, full) in enumerate(levels_tb, start=1):
        progress.step(f"═══ ТБ {short} ({i} из {len(levels_tb)}) ═══")
        preps.append(analyze.prepare(b, tb_id, short, full))

    # Тексты активностей нужны ТОЛЬКО аудиту. При выключенном аудите этот запрос —
    # десятки секунд и миллионы строк впустую, поэтому его просто не делаем.
    if analyze.audit_enabled(ctx):
        text_df = bank.audit_texts(ctx.engine, b, analyze.audit_inns(preps))
    else:
        text_df = pd.DataFrame()
        progress.done("Аудит отработки отключён (llm_max_calls=0): тексты активностей "
                      "не читаем, причины берутся из правил")

    for a in preps:
        progress.step(f"═══ ТБ {a.tb_short}: разбор отработки ═══")
        analyze.finish(ctx, b, a, text_df)
        _log_llm_stats(a)

    sb = analyze.build_sb(b, preps)
    # карточка ТБ на уровне банка знает номер вкладки своего разбора — по ней и
    # устроен переход СБ → ТБ
    lvl_of = {a.tb_id: i + 1 for i, a in enumerate(preps)}
    for c in sb.gosb_cards:
        c["lvl"] = lvl_of.get(c["gosb_id"])

    # Динамика неделя к неделе. Недельного грейна в витрине нет (period_type —
    # только m/q/qtd/y/ytd), поэтому сравнение идёт с ПРОШЛОЙ СБОРКОЙ отчёта:
    # каждая сборка оставляет рядом с HTML снимок своих чисел. Подробнее — в
    # snapshot.py. Отчёт от снимков не зависит: нет базы — нет строки сравнения.
    ref_cur = str(b.dates.get("ref_cur", sb.ref_date))
    base = snapshot.load_base(ctx.output_dir, ref_cur)
    for a in [sb] + preps:
        a.wow = snapshot.compare(base, a)
    if not base:
        progress.done("Сравнивать неделя к неделе не с чем: снимков прошлых сборок "
                      "на этот прогнозный месяц нет — в плашке прогноза об этом "
                      "сказано прямо")
    snapshot.save(ctx.output_dir, ref_cur, [sb] + preps)

    # Выводы по разделам — по вызову LLM на уровень, и это основное время отчёта.
    # ПОСЛЕДОВАТЕЛЬНО: параллельный вариант пробовали, корпоративный шлюз отвечает на
    # него 429 и после ретраев роняет запрос — из 12 уровней доходил один, остальные
    # получали фолбэк вместо выводов модели.
    levels = []
    for a in [sb] + preps:
        progress.step(f"LLM: выводы по разделам — {a.tb_short}")
        levels.append((a, prompts.section_narratives(ctx, a)))

    progress.step("Сборка HTML")
    bodies = "".join(
        f'<div class="lvl" id="lvl-{i}"{"" if i == 0 else " hidden"}>'
        f'{_level_body(a, story, i)}</div>'
        for i, (a, story) in enumerate(levels))
    d = sb.dates or {}
    # Дата обновления — время СБОРКИ отчёта, а не дата витрины: читатель открывает
    # файл из почты и первым делом спрашивает, насколько он свежий. Месяц прогноза
    # и закрытый месяц стоят рядом, в той же строке, — это и есть срез данных.
    meta = (f'Прогноз на {C.esc(d.get("label", sb.ref_date))} · '
            f'база — закрытый {C.esc(d.get("closed_label", ""))} · '
            f'отчёт обновлён {datetime.now():%d.%m.%Y, %H:%M}')
    return page(
        title=TITLE,
        subtitle=SUBTITLE,
        meta=meta,
        body=(_BOOT_JS + _LEGEND + _tabs([a for a, _ in levels]) + bodies
              + _cell_dialog() + _GD_JS + _LVL_JS + _OF_JS + _CELL_JS),
        # ux-fix.css — наши правки поверх дизайна (сам ux.css остаётся копией макета)
        css=_asset("ux.css") + _asset("ux-fix.css") + _asset("help.css"),
        # скрипт дизайна — ПОСЛЕ .wrap: он переносит её содержимое в новую раскладку и
        # оборачивает gdOpen, поэтому идёт после всех остальных скриптов
        tail=(f'<script>{_asset("ux.js.txt")}</script>\n'
              # раскладка готова — снимаем маску, поставленную _BOOT_JS
              f'{_REVEAL_JS}\n'
              # кнопки выгрузки — ПОСЛЕ скрипта дизайна: он переставляет блоки,
              # и кнопка должна встать рядом с таблицей уже на новом месте
              f'{_XLS_JS}\n'
              f'{_asset("help.html")}<script>\n{_asset("help.js.txt")}</script>\n'
              # прямой потомок <body> — на него ссылается печатный CSS в
              # ux-fix.css (скрывает всё, кроме этого блока, во время печати)
              f'<div id="print-root" aria-hidden="true"></div>'),
    )


def _tabs(levels: list) -> str:
    """Панель уровней. Липкая: при длинном разборе переход наверх не нужен."""
    btns = "".join(
        f'<button class="lvl-tab{" on" if i == 0 else ""}" data-lvl="{i}" '
        f'onclick="lvlGo({i})">{C.esc(a.tb_short)}</button>'
        for i, a in enumerate(levels))
    return f'<nav class="lvls">{btns}</nav>'


def _level_body(a: analyze.Analysis, story: dict, idx: int) -> str:
    """Содержимое одного уровня. Одинаково для СБ и для ТБ — меняется единица разбора.

    `idx` уходит в id элементов: в одном документе живут 13 отчётов, и повторяющийся
    id сломал бы и оверлеи, и списки организаций (по id их находит скрипт).
    """
    body = (
        _lvl_head(a, idx)
        + _hero(a)
        + _kpis(a)
        + _portfolio_block(a, story.get("forecast"))
        + _trend_section(a)
        + _matrix(a, story.get("matrix"), idx)
        + _problem_gosb(a, story.get("gosb"), idx)
    )
    # список организаций живёт только на уровне ТБ: на уровне банка работают с ТБ,
    # а имена — один переход вниз (и это сотни тысяч строк, которые никто не листает)
    if a.level != "sb":
        body += _orgs(a, story.get("orgs"), idx)
    body += _outflow_section(a, story.get("outflow"))
    return body


def _lvl_head(a: analyze.Analysis, idx: int) -> str:
    """Шапка уровня: чьё это отчёт и кнопка выгрузки.

    Имя области стоит крупно и на каждом уровне, включая банк: в одном файле лежат
    тринадцать отчётов, вкладки переключаются мышью, и без имени на самой странице
    (а не только в подсвеченной вкладке) легко читать чужие числа как свои.
    """
    back = ('<button type="button" class="lvl-up" onclick="lvlGo(0)">← Все банки'
            '</button>' if idx else "")
    return (
        '<div class="lvl-head">'
        f'<div class="lvl-id">{back}<h2 class="lvl-name">{C.esc(a.tb_full)}</h2></div>'
        f'<button type="button" class="lvl-pdf" onclick="exportLevelPdf({idx})" '
        f'title="Собрать отчёт этого уровня в PDF: все разделы раскрыты">'
        f'Скачать PDF</button>'
        '</div>'
    )


# --------------------------------------------------------------------------- #
def _hero(a: analyze.Analysis) -> str:
    """Вердикт по ПРОГНОЗУ текущего месяца. Закрытый месяц — строкой ниже:
    это единственная твёрдая цифра, и по ней же считается ранг ТБ."""
    r = a.verdict["rcp"]
    d = a.dates or {}
    st = C.status_of(r["exec"])
    word = {"good": "План выполняется", "warn": "План близок к выполнению",
            "bad": "План не выполняется"}[st]
    # сами цифры закрытого месяца живут в KPI-карточках ниже (по каждой метрике),
    # здесь остаётся только ранг: он один на ТБ и к отдельной метрике не привязан.
    # У банка ранга нет — сравнивать не с кем, и строка про него не пишется вовсе.
    closed_txt = (f'закрытый месяц {C.esc(d.get("closed_label", ""))}' if not r["rank"]
                  else f'ранг ТБ {r["rank"]}/{r["n_tb"]} за закрытый месяц '
                       f'{C.esc(d.get("closed_label", ""))}')
    # процент отвечает «насколько», абсолют — «сколько людей». Без второго числа
    # 91% у банка и 91% у небольшого ГОСБ читаются как одна и та же новость
    fot = a.verdict["fot"]
    inner = (
        f'<div class="eyebrow">Прогноз выполнения на {C.esc(d.get("label", ""))}</div>'
        # Вердикт — только словом: процент выполнения относится к получателям, и в
        # заголовке плашки читался как общий по ней. Он стоит в своей строке
        # показателя, рядом с числами, из которых посчитан.
        f'<div class="verdict">{C.esc(word)}</div>'
        # Обе главные строки плашки — получатели и ФОТ — устроены одинаково:
        # прогноз → план → выполнение → отклонение, и несут класс row2-fot (крупное
        # начертание строки показателя). Класс проставлен в разметке, а не оставлен
        # на ux.js: скрипт вешает его на ПЕРВУЮ строку .row2 плашки, считая её
        # строкой ФОТ (в макете она стояла первой). У нас первая — получатели, и
        # ФОТ, равный ей по смыслу, оставался мелкой подписью.
        f'<div class="row2 row2-fot">Получатели: прогноз '
        f'<b>{C.fmt_num(r["fact"])}</b> '
        f'при плане {C.fmt_num(r["plan"])} · '
        f'<b style="color:{_col(r["exec"])}">{_pct(r["exec"])}</b> · '
        f'{_delta_html(r["fact"] - r["plan"], "чел")}</div>'
        # Бейджа про разрыв до плана здесь нет: gap_rcp — это ровно план минус
        # прогноз, то же число и с тем же знаком уже стоит в строке выше.
        + C.meter(r["exec"])
        + f'<div class="row2 row2-fot">ФОТ: прогноз '
          f'<b>{C.fmt_num(fot["fact"] / 1e6)}</b> '
          f'при плане {C.fmt_num(fot["plan"] / 1e6)} млн ₽ · '
        # процент набран как остальные числа строки и окрашен по статусу — так же, как
        # выполнение показано в карточках показателей ниже
          f'<b style="color:{_col(fot["exec"])}">{_pct(fot["exec"])}</b> · '
          f'{_delta_html((fot["fact"] - fot["plan"]) / 1e6, "млн ₽")}</div>'
        + f'<div class="row2">{closed_txt}</div>'
        + _wow_row(a)
    )
    return C.card(inner, cls="hero")


def _wow_num(delta: float, unit: str = "", digits: int = 0,
             eps: float = 0.5) -> str:
    """Изменение величины к прошлой сборке: знак, цвет, единица.

    Ноль в пределах округления пишем словами: «+0 чел» читалось бы как
    настоящее изменение на ноль, а это отсутствие изменения.
    """
    if abs(delta) < eps:
        return '<b style="color:var(--text-2)">без изменений</b>'
    col = "var(--good)" if delta > 0 else "var(--bad)"
    sign = "+" if delta > 0 else "−"
    tail = f" {C.esc(unit)}" if unit else ""
    return f'<b style="color:{col}">{sign}{C.fmt_num(abs(delta), digits=digits)}{tail}</b>'


def _wow_pp(delta: float | None) -> str:
    """Изменение выполнения плана — в процентных пунктах.

    Именно в пунктах, а не в процентах: 95% → 97% это «+2 п.п.», а не «+2%».
    Проценты от процентов в отчёте про выполнение плана читаются неверно.
    """
    if delta is None or abs(delta) < 0.0005:
        return '<b style="color:var(--text-2)">без изменений</b>'
    col = "var(--good)" if delta > 0 else "var(--bad)"
    sign = "+" if delta > 0 else "−"
    return f'<b style="color:{col}">{sign}{abs(delta) * 100:.1f} п.п.</b>'


def _wow_row(a: analyze.Analysis) -> str:
    """Строка «неделя к неделе» в плашке прогноза.

    Сравнение идёт с прошлой СБОРКОЙ отчёта — в витрине недельного грейна нет
    (см. snapshot.py), поэтому дата базовой сборки подписана прямо в строке: без
    неё непонятно, за какой период показано изменение. Когда базы ещё нет, строка
    не исчезает, а говорит об этом — иначе читатель решит, что изменений нет.
    """
    w = a.wow or {}
    if not w.get("rcp"):
        return ('<div class="row2 wow">Неделя к неделе: сравнивать пока не с чем — '
                'снимок этой сборки сохранён, динамика появится в следующем отчёте.</div>')
    rcp, fot = w["rcp"], w.get("fot") or {}
    plan = (f' · план {_wow_num(rcp["plan"], "чел")}'
            if abs(rcp.get("plan", 0)) >= 0.5 else "")
    return (f'<div class="row2 wow">Неделя к неделе, к сборке от {C.esc(w["full"])}: '
            f'прогноз получателей {_wow_num(rcp["fc"], "чел")}{plan} · '
            f'выполнение {_wow_pp(rcp.get("exec"))} · '
            f'прогноз ФОТ {_wow_num(fot.get("fc", 0) / 1e6, "млн ₽", 1, 0.05)}</div>')


def _wow_unit(a: analyze.Analysis, unit_id: int, cls: str = "g-wow") -> str:
    """То же сравнение по одной единице — для карточки и шапки её разбора."""
    w = a.wow or {}
    u = (w.get("units") or {}).get(unit_id)
    if not u:
        return ""
    return (f'<div class="{cls}">Неделя к неделе (к {C.esc(w.get("label", ""))}): '
            f'прогноз {_wow_num(u["fc"], "чел")} · '
            f'выполнение {_wow_pp(u.get("exec"))}</div>')


def _delta_html(delta: float, unit: str = "") -> str:
    """Отклонение от плана в абсолюте: знак, цвет, единица.

    Показывается рядом с процентом везде, где стоит выполнение плана. Ноль в пределах
    округления пишем как «вровень с планом», иначе рядом с «100%» стояло бы «+0».
    """
    if abs(delta) < 0.5:
        return '<b style="color:var(--text-2)">без отклонения</b>'
    col = "var(--good)" if delta > 0 else "var(--bad)"
    sign = "+" if delta > 0 else "−"
    tail = f" {C.esc(unit)}" if unit else ""
    return f'<b style="color:{col}">{sign}{C.fmt_num(abs(delta))}{tail} к плану</b>'


def _kpis(a: analyze.Analysis) -> str:
    """Две карточки по метрикам: сверху ПРОГНОЗ против плана, снизу — твёрдые факты
    ЗАКРЫТОГО месяца и прирост год к году. Разделены линией, потому что это разные по
    природе числа: прогноз может не сбыться, факт закрытого месяца — уже нет."""
    d = a.dates or {}
    closed = a.closed or {}
    yoy = a.yoy or {}

    def foot(key, scale, unit):
        cl = closed.get(key, {})
        if not cl.get("fact"):
            return ""
        # выполнение закрытого месяца окрашено тем же правилом, что и выполнение
        # прогноза строкой выше: это одна и та же величина за разные месяцы, и
        # чёрный процент рядом с цветным читался как другая по смыслу цифра
        head = (f'{C.esc(d.get("closed_label", ""))} закрыт: '
                f'{C.fmt_num(cl["fact"] / scale, unit)} '
                f'(<b style="color:{_col(cl.get("exec"))}">'
                f'{_pct(cl.get("exec"))}</b> плана, '
                + _delta_html((cl["fact"] - cl.get("plan", 0)) / scale, unit) + ')')
        y = yoy.get(key)
        if not y:
            # год к году не рассчитан — честное «—», а не молчаливый ноль
            tail = 'год к году —'
        else:
            col = "var(--good)" if y["delta"] >= 0 else "var(--bad)"
            sign = "+" if y["delta"] >= 0 else "−"
            tail = (f'год к году <b style="color:{col}">{sign}'
                    f'{C.fmt_num(abs(y["delta"]) / scale, unit)} '
                    f'({sign}{abs(y["pct"]) * 100:.1f}%)</b>')
        return f'<div class="foot">{head}<br>{tail}</div>'

    def kpi(title, v, key, scale=1.0, unit=""):
        return C.card(
            f'<div class="label">{C.esc(title)}</div>'
            f'<div class="value">{C.fmt_num(v["fact"] / scale, unit)}</div>'
            f'<div class="delta">план {C.fmt_num(v["plan"] / scale, unit)} · '
            f'<b style="color:{_col(v["exec"])}">{_pct(v["exec"])}</b> · '
            + _delta_html((v["fact"] - v["plan"]) / scale, unit) + '</div>'
            + C.meter(v["exec"]) + foot(key, scale, unit),
            cls="kpi",
        )
    # ФОТ в БД — рубли, выводим в млн ₽ (÷ 1e6)
    cards = (kpi("Получатели (прогноз), чел", a.verdict["rcp"], "rcp")
             + kpi("Общий ФОТ (прогноз), млн ₽", a.verdict["fot"], "fot", scale=1e6))
    return f'<div class="grid cols-2">{cards}</div>'


def _wf_lines(pf: dict, d: dict, conv: float, conv_diag: dict | None = None,
              conv_is_tb: bool = False) -> str:
    """Строки блока портфеля. Общие для уровня и для оверлея по единице — числа
    и порядок одни и те же, меняется только срез данных.

    `conv` — коэффициент ИМЕННО ЭТОГО уровня (у ГОСБ свой), `conv_is_tb` — что он
    подменён коэффициентом ТБ из-за малого объёма истории.
    """
    # если фактическая конверсия ниже пола, показываем и её: иначе в отчёте стоит
    # ровно «0.20» и не отличить настоящую конверсию от сработавшей границы
    raw = (conv_diag or {}).get("tb_raw")
    if (conv_diag or {}).get("tb_clipped") and raw is not None and conv_is_tb:
        conv_txt = f'коэф. ТБ {raw:.2f} → поднят до пола {conv:.2f}, своей истории мало'
    elif conv_is_tb:
        conv_txt = f'коэф. ТБ {conv:.2f} — своей истории мало'
    else:
        conv_txt = f'коэф. {conv:.2f}'
    # Время у пайплайна меряется в КАЛЕНДАРНЫХ днях от реальной даты — показываем
    # именно дни, а не проценты: «осталось 1 из 31 дн.» читается однозначно, а «3%»
    # можно спутать с долей отыгранных выплат в строке оттока выше.
    dl, dm = int(d.get("days_left", 0)), int(d.get("days_in_month", 0) or 1)
    pipe_hint = (f'заявлено {C.fmt_num(pf.get("pipe_raw", 0))} · '
                 f'пришло {C.fmt_num(pf.get("pipe_fact", 0))} · '
                 f'остаток {C.fmt_num(pf.get("pipe_rest", 0))} × {conv_txt} × '
                 f'осталось {dl} из {dm} дн. → +{C.fmt_num(pf.get("pipe_expect", 0))}')
    out_lbl = C.esc(d.get("out_label", ""))
    rows = [
        (f'Портфель — {C.esc(d.get("closed_label", ""))} закрыт', pf["base"], 0, ""),
        (f'Фактический отток за 3 месяца, который не вернулся',
         pf.get("out_kept", 0), -1,
         # имени витрины в подписи нет: читателю отчёта оно ничего не объясняет,
         # а условие отбора объясняет
         f'{out_lbl} · организации от {bank.OUT_MIN_QTY} чел '
         f'за вычетом вернувшихся' if out_lbl else ""),
        ("Пайплайн на месяц", pf["pipe"], 1, pipe_hint),
    ]
    # подсказка идёт классом g-hint, а НЕ .sub: .sub — это стиль подзаголовка
    # страницы (19px), внутри строки блока он выглядит крупнее самой строки
    return "".join(
        f'<div class="g-seg"><b>{lbl}</b> '
        f'<span style="color:{"var(--bad)" if sign < 0 else "var(--good)"}">'
        f'{"−" if sign < 0 else "+" if sign > 0 else ""}{C.fmt_num(val)}</span>'
        + (f' <span class="g-hint">· {hint}</span>' if hint else "") + '</div>'
        for lbl, val, sign, hint in rows)


def _portfolio_block(a: analyze.Analysis, ai: str | None = None) -> str:
    """Управление портфелем: что есть, что потеряли, что ждём.

    Это НЕ разложение прогноза на слагаемые. Прогноз берётся готовым из витрины
    метрик и с этими тремя числами арифметически не связан — складывать их и
    сверять с прогнозом бессмысленно. Здесь три независимых факта, каждый со своим
    действием: портфель — что защищаем, невозвращённый отток — что уже потеряли,
    пайплайн — что придёт само.
    """
    pf = a.pf or {}
    if not pf:
        return ""
    d = a.dates or {}
    fc = a.fc_stats or {}
    body = _wf_lines(pf, d, fc.get("conv_tb", 1.0), fc.get("conv"), conv_is_tb=False)
    st = C.status_of(pf.get("exec"))
    total = (
        f'<div class="g-do">Прогноз витрины на {C.esc(d.get("label", ""))}: '
        f'<b>{C.fmt_num(pf["forecast"])}</b> при плане {C.fmt_num(pf["plan"])} → '
        + C.badge(f"{_pct(pf.get('exec'))} плана", st) + '</div>'
    )
    upside = ""
    if pf.get("pipe_upside", 0) >= 1:
        upside = (f'<div class="g-act">Если пайплайн отработают на 100%, придёт на '
                  f'<b>+{C.fmt_num(pf["pipe_upside"])} фл</b> больше, чем заложено '
                  f'с поправкой на реализуемость.</div>')
    # Пояснение нужно: без него три строки ниже принимают за слагаемые прогноза и
    # начинают сверять их сумму с ним. Но объяснять методику двумя фразами с тире
    # и тройкой однородных — значит писать не читателю, а в протокол.
    note = ('<p class="sub" style="font-size:14px;margin:-4px 0 12px">'
            'Показатели ниже в прогноз не суммируются. Прогноз поступает из '
            'витрины готовым, а это независимые факты управления портфелем: '
            'текущая база, безвозвратные потери и ожидаемый приход по сделкам.</p>')
    return C.section("Управление портфелем",
                     C.card('<h3>Портфель, потери и приход</h3>' + note + body
                            + total + upside)
                     + _ai(ai),
                     eyebrow="Справочно")


def _trend_block(rows: list, title: str) -> str:
    """Два графика и числа под ними: портфель по месяцам и выполнение плана.

    Графики разные не для разнообразия: на линиях видно, куда идёт портфель, но
    разница с планом в проценты там незаметна — её показывают столбцы отклонения от
    нуля. Один график вместо двух отвечал бы только на половину вопроса.
    """
    if not rows:
        return ('<div class="gd-note">истории по этой единице в витрине метрик нет — '
                'графики не построены</div>')
    return (
        f'<h4>{C.esc(title)}</h4>'
        '<div class="ch-cap">Портфель получателей помесячно, человек: факт, план '
        'и отклонение от плана</div>'
        + C.trend_plan_fact(rows)
        + C.trend_table(rows)
    )


def _trend_section(a: analyze.Analysis) -> str:
    """Раздел «Динамика за 12 месяцев» — по области уровня целиком.

    Стоит сразу после управления портфелем: там сказано, что с портфелем сейчас,
    здесь — как он к этому пришёл. Только ЗАКРЫТЫЕ месяцы: факт текущего набегает
    в течение месяца и на графике выглядел бы обвалом.
    """
    if not a.trend:
        return ""
    first, last = a.trend[0], a.trend[-1]
    grew = last["fact"] - first["fact"]
    n_miss = sum(1 for r in a.trend if r["fact"] < r["plan"])
    lead = (f'<p class="sub" style="font-size:15px;margin:-2px 0 14px">'
            f'Период {C.esc(first["label"])} — {C.esc(last["label"])}, закрытые '
            f'месяцы. {"Прирост" if grew >= 0 else "Снижение"} портфеля за период — '
            f'<b>{C.fmt_num(abs(grew))}</b> чел. План не выполнен в '
            f'<b>{n_miss}</b> из {len(a.trend)} месяцев.</p>')
    return C.section(
        "Динамика за 12 месяцев",
        lead + C.card(_trend_block(a.trend, "")),
        eyebrow="Ретроспектива портфеля",
        desc="Как портфель получателей менялся месяц к месяцу и в каких месяцах "
             "план не выполнялся — чтобы отличить разовое отклонение от "
             "устойчивой тенденции.")


def _matrix(a: analyze.Analysis, ai: str | None = None, idx: int = 0) -> str:
    m = a.matrix
    if m.empty:
        return ""
    # Порядок строк — алфавитный, как и карточки единиц: матрицу читают, отыскивая
    # в ней конкретный ГОСБ. Ранжированный взгляд на те же данные даёт таблица
    # «ТОП по невыполнению» справа.
    gg = a.gosb_gap.assign(_ord=[analyze._ru_key(x) for x in a.gosb_gap["unit_name"]]) \
                   .sort_values("_ord", kind="stable")
    present = set(m.unit_id)
    rows_id_label = [(int(r.unit_id), (r.unit_name or "")[:26])
                     for r in gg.itertuples() if int(r.unit_id) in present]
    segs = [s for s in SEG_ORDER if s in set(m.seg_name)]
    # Красная ячейка = западающий сегмент из карточек единицы: тот же порог в одного
    # получателя (недобор меньше человека — округление, показываем как выполнено).
    cells = {(int(row.unit_id), row.seg_name):
             (row.execution_percent if analyze._failing_seg(row.nedobor) else
              max(float(row.execution_percent or 0), 1.0), row.nedobor)
             for row in m.itertuples()}
    # Ранг есть только у ТБ — это величина витрины, считанная по закрытому месяцу.
    # На уровне ТБ единицы строк — ГОСБ, и колонки ранга там нет.
    ranks = a.ranks if a.level == "sb" else None
    hint = ('<p class="sub" style="font-size:14px;margin:-4px 0 12px">'
            'Клик по ячейке открывает организации этого '
            f'{C.esc(a.unit_label)} и сегмента, у которых за год стало меньше '
            'получателей.'
            + (' Ранг — место ТБ в сети по выполнению плана за закрытый месяц '
               f'{C.esc((a.dates or {}).get("closed_label", ""))}.' if ranks else "")
            + '</p>')
    heat = C.card(
        '<h3>Прогноз выполнения плана по получателям, %</h3>' + hint
        + C.heat_matrix(rows_id_label, segs, cells, unit_head=a.unit_label,
                        ranks=ranks, cell_click="cellOpen"))
    top = [(f'{r.unit_name} · {r.seg_name}',
            C.badge(_pct(r.execution_percent), C.status_of(r.execution_percent)),
            C.fmt_num(r.nedobor), f'{r.share*100:.0f}%') for r in a.top_cells.itertuples()]
    # «ГОСБхСегмент» — и на уровне банка тоже: так в дизайне пользователя
    top_tbl = C.card(f'<h3>ТОП {a.unit_label} по невыполнению</h3>'
                     + C.table(["ГОСБ × сегмент", "Выполн.", "Отклонение, чел", "Доля отклонения"],
                               top, num_cols=[2, 3]))
    return C.section(f"Матрица выполнения {a.unit_label}/сегмент",
                     f'<div class="grid cols-2">{heat}{top_tbl}</div>'
                     + _cell_data(a, idx, cells) + _ai(ai),
                     eyebrow="Диагностика по прогнозу")


def _cell_data(a: analyze.Analysis, idx: int, cells: dict) -> str:
    """Состав ячеек матрицы — данными для скрипта, а не разметкой.

    Ячеек на уровне до шестисот, и разложить каждую отдельным скрытым блоком
    значило бы утроить вес файла ради содержимого, которое открывают у двух-трёх
    ячеек. Поэтому в документ уезжает компактный словарь, а разметку строит
    `cellOpen` в момент клика.

    Ключ — «единица|сегмент» в пределах уровня; сам уровень скрипт узнаёт по
    id блока, внутри которого лежит матрица: в одном файле тринадцать отчётов.
    """
    top = a.cell_top or {}
    if not top:
        return ""
    payload = {}
    for key in cells:
        cell = top.get(key)
        if not cell:
            continue
        uid, seg = key
        payload[f"{uid}|{seg}"] = {
            "n": cell["n"], "fl": round(cell["fl"]),
            "top": round(cell.get("top_fl", 0)),
            "rows": [[r["name"], round(r["yoy"]), round(r["cur"]), round(r["was"]),
                      r["emp"]] for r in cell["rows"]],
        }
    if not payload:
        return ""
    js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return (f'<script>window.__CELLS=window.__CELLS||{{}};'
            f'window.__CELLS["lvl-{idx}"]={js};</script>')


def _gap_cell(v: float) -> str:
    """Ячейка недобора. План выполнен (недобор ≤ 0) — ставим «—», а не «−0»."""
    return "—" if v <= 0.5 else "−" + C.fmt_num(v)


def _seg_badge(s: dict) -> str:
    """Бейдж сегмента: имя + выполнение, цвет по статусу — состояние не кодируется
    одним лишь цветом. При 99.5–99.9% показываем десятую долю, иначе рядом с
    недобором стояло бы «100%»."""
    return C.badge(f'{s["seg"]} {_pct(s["exec"])}', C.status_of(s["exec"]))


def _gosb_table(c: dict, wide: bool = False) -> str:
    """Таблица карточки: строка «Всего» по ГОСБ + строки ВСЕХ сегментов.

    Одни и те же колонки на обоих уровнях — итог и сегменты сравниваются по вертикали.
    Выполняющие сегменты идут ниже западающих и приглушены: видно, за счёт чего ГОСБ
    вытягивает план, но взгляд по-прежнему цепляется за проблемные.
    У строки «Всего» бейджа нет — процент уже стоит крупно в шапке карточки.

    wide=True (в оверлее) добавляет колонку пайплайна. Оттока по сегментам здесь
    нет: фактический отток лежит на грейне (ГОСБ, ИНН) и в разрез витрины по
    сегментам не раскладывается — разносить его пропорционально было бы выдумкой.
    """
    def row(label, d, cls=""):
        pipe = d.get("pipe_np", 0)
        extra = (f'<span>{"+" + C.fmt_num(pipe) if pipe >= 1 else "—"}</span>'
                 if wide else "")
        return (f'<div class="g-row {cls}"><span>{label}</span>'
                f'<span>{C.fmt_num(d["forecast"])}</span>'
                f'<span>{C.fmt_num(d["plan"])}</span>'
                f'<span>{_gap_cell(d["nedobor"])}</span>'
                f'{extra}<span>{d.get("n_need") or "—"}</span></div>')

    cols = ('<span>пайплайн</span>' if wide else "")
    head = (f'<div class="g-row head"><span>сегмент</span><span>прогноз</span>'
            f'<span>план</span><span>отклонение</span>{cols}<span>орг</span></div>')
    tot = {"forecast": c["forecast"], "plan": c["plan"], "nedobor": c["gap"],
           "n_need": c["n_need"],
           "pipe_np": sum(s.get("pipe_np", 0) for s in c["segs"])}
    rows = [row(_seg_badge(s), s, "" if s["failing"] else "ok") for s in c["segs"]]
    if not rows:
        rows.append('<div class="g-row"><span>нет данных по сегментам</span></div>')
    rest = row("прочие", c["rest"], "rest") if c.get("rest") else ""
    cls_w = " wide" if wide else ""
    return (f'<div class="g-tbl{cls_w}">{head}{row("Всего", tot, "total")}'
            f'{"".join(rows)}{rest}</div>')


def _org_rows(rows: list, key: str, tail_n: int, tail_fl: float,
              tail_txt: str, sign: int = -1, why=None,
              cover: float = 0.0, n_all: int = 0, with_emp: bool = False) -> str:
    """Строки именной детализации: организация · вклад · причина и что сделать.

    Названия компаний приходят из БД — обязательно через C.esc. Оттуда же ФИО
    закреплённого сотрудника (`emp`).

    `with_emp` — показывать ли ФИО. Флагом, а не «есть ли поле в строке»: строками
    одного и того же кадра живут два блока (отток и пайплайн), поле несут оба, а
    подпись нужна только оттоку. Кто её показывает, видно по вызовам.

    Хвост не прячем, и покрытие тоже: подпись всегда говорит, сколько организаций из
    общего числа показано и какую долю блока они объясняют. На проме в блоке бывает
    несколько тысяч организаций, и 8 названных могут объяснять лишь пятую часть —
    читатель обязан это видеть, иначе примет часть за целое.
    """
    if not rows:
        return '<div class="gd-note">нет организаций с заметным вкладом</div>'
    # пояснение зависит от блока: причина оттока к пайплайну и к годовому тренду
    # отношения не имеет, поэтому текст задаётся вызывающим
    why_fn = why or (lambda r: " · ".join(x for x in (r.get("note"), r.get("action")) if x))
    out = []
    if cover and n_all > len(rows):
        out.append(f'<div class="gd-note">{len(rows)} из {C.fmt_num(n_all)} орг. — '
                   f'это {cover * 100:.0f}% блока</div>')
    for r in rows:
        mark = "−" if sign < 0 else "+"
        # ФИО закреплённого сотрудника — второй строкой под названием, как «Орг. N»
        # в списке к работе. Отдельной колонкой его не сделать: строка организации и
        # шапка группы размечены ОДНОЙ сеткой в три колонки, четвёртая разъехалась бы
        # в обеих. Пусто на уровне СБ: там единица разбора ТБ, а закрепление живёт
        # на грейне (ГОСБ, организация) — см. analyze._unit_detail.
        emp = str(r.get("emp") or "").strip() if with_emp else ""
        # span, а не div: ячейка сетки — сам <span>, а блочный элемент внутри
        # фразового делает разметку невалидной. Перевод строки даёт CSS
        sub = f'<span class="gd-emp">{C.esc(emp)}</span>' if emp else ""
        out.append(
            f'<div class="gd-row"><span>{C.esc(r["name"])}{sub}</span>'
            f'<span>{mark}{C.fmt_num(abs(r[key]))}</span>'
            f'<span class="gd-why">{C.esc(why_fn(r)) or "—"}</span></div>')
    if tail_n:
        out.append(f'<div class="gd-note">ещё {C.fmt_num(tail_n)} орг. на '
                   f'{C.fmt_num(tail_fl)} чел {C.esc(tail_txt)}</div>')
    return "".join(out)


def _why_out(r: dict) -> str:
    """Пояснение к строке оттока: сколько вернулось, вывод по отработке, что делать.

    Пометка о зоне нужна именно здесь: в списке крупнейших неизбежно окажутся
    организации, с которыми работать нельзя (нет в эталонной базе) или нечем — отток
    уже отработан, а люди не вернулись. Без пометки читатель начнёт распределять то,
    что не его.

    `reason` — вывод аудита, если он был, иначе причина классификации: для возвратных
    организаций она названа по МЕСЯЦАМ УХОДА («задач не заводили», «ни одной по
    оттоку», «отработан»), и именно она объясняет, почему строка здесь.
    """
    back = float(r.get("ret", 0) or 0)
    parts = []
    if back:
        parts.append(f'вернулись {C.fmt_num(back)} из {C.fmt_num(r.get("gone", 0))}')
    parts += [x for x in (r.get("reason"), r.get("action")) if x]
    zone = r.get("zone")
    if zone and zone != "можно работать":
        parts.insert(0, zone)
    return " · ".join(parts) if parts else "причина не зафиксирована"


def _why_pipe(r: dict) -> str:
    """Пояснение к строке пайплайна: сколько из заявленного дошло до прогноза."""
    return f'в прогнозе {C.fmt_num(r["pipe_adj"], "фл")} — с поправкой на реализуемость'


def _why_size(r: dict) -> str:
    """Пояснение к строке годового тренда: когда оттекали, работали ли тогда, причина.

    Про отработку говорим ровно то, что знаем: «задач не заводили» — это факт из
    выборки, «отработка неизвестна» — месяц оттока в окно воронки не попал. Подменять
    второе первым нельзя, это разные утверждения.
    """
    parts = [f'сейчас {C.fmt_num(r["cur"])} чел']
    months = r.get("out_months") or []
    if months:
        shown = ", ".join(months[:3])
        more = f" и ещё {len(months) - 3}" if len(months) > 3 else ""
        parts.append(f"отток: {shown}{more}")
        if r.get("yoy_key") == "unknown":
            parts.append("отработка за те месяцы неизвестна")
        elif r.get("yoy_tasks"):
            # задачи считаются и в самом месяце оттока, и в следующем: витрина
            # закрывает отток позже, чем он случился, и задачу заводят обоими
            parts.append(f'задач в те месяцы и следующие за ними: {r["yoy_tasks"]}')
        else:
            parts.append("задач ни в те месяцы, ни в следующие не заводили")
    else:
        parts.append("заметных оттоков за окно не было")
    parts.append(f'причина: {r["reason"]}' if r.get("reason") else "причина не зафиксирована")
    return " · ".join(parts)


def _out_group(g: dict, open_: bool = False) -> str:
    """Группа оттока: шапка с причиной и итогом, внутри — список организаций.

    Нативный `<details>`: клик и клавиатура работают без JS, а печать раскрывает
    содержимое сама. Шапка размечена теми же тремя колонками, что и строка
    организации, — числа групп и числа организаций стоят в одной вертикали.

    Покрытие внутри списка не подписываем: сколько организаций в группе и сколько
    в них человек, уже сказано в шапке — повторять это строкой ниже незачем.
    """
    work = (f'можно работать: {g["work_n"]} орг (−{C.fmt_num(g["work_fl"])})'
            if g["work_n"] else "работать не с кем — вне зоны влияния")
    return _group_html(g, open_, work, "out", _why_out, with_emp=True)


def _yoy_group(g: dict, open_: bool = False) -> str:
    """Группа годового тренда. Тот же рендер, что у оттока, — меняется только то,
    что стоит в третьей строке шапки: у оттока это зона влияния, здесь — сколько
    организаций группы имеют зафиксированную причину."""
    work = (f'причина зафиксирована у {g["work_n"]} орг (−{C.fmt_num(g["work_fl"])})'
            if g["work_n"] else "причина не зафиксирована ни у одной")
    return _group_html(g, open_, work, "yoy", _why_size, with_emp=True)


def _group_html(g: dict, open_: bool, work: str, key: str, why,
                with_emp: bool = False) -> str:
    """Группа: шапка с причиной и итогом, внутри — список организаций.

    Нативный `<details>`: клик и клавиатура работают без JS, а печать раскрывает
    содержимое сама. Шапка размечена теми же тремя колонками, что и строка
    организации, — числа групп и числа организаций стоят в одной вертикали.

    Покрытие внутри списка не подписываем: сколько организаций в группе и сколько
    в них человек, уже сказано в шапке — повторять это строкой ниже незачем.
    """
    sub = f'{g["sub"]} · {g["share"] * 100:.0f}% блока · {work}'
    return (
        f'<details class="gd-grp"{" open" if open_ else ""} '
        f'style="--share:{g["share"] * 100:.0f}%">'
        f'<summary><span class="gd-gt">{C.esc(g["title"])}'
        f'<i>{g["n"]} орг</i></span>'
        f'<span>−{C.fmt_num(g["fl"])}</span>'
        f'<span class="gd-sub">{C.esc(sub)}</span></summary>'
        f'<div class="gd-rows">'
        + _org_rows(g["rows"], key, g["tail_n"], g["tail_fl"], "— хвост", why=why,
                    with_emp=with_emp)
        + '</div></details>'
    )


def _gd_help(*lines: str, title: str = "Как считаем этот блок") -> str:
    """Подсказка «как считаем» под заголовком блока карточки.

    Нативный `<details>`: закрыта по умолчанию, открывается кликом и с клавиатуры,
    работает без скрипта и раскрывается сама при печати. Читателю карточки объяснение
    нужно один раз, поэтому строкой, а не постоянным текстом на пол-экрана.

    Язык подсказки — про смысл числа, а не про источник: названия таблиц и полей
    управляющему ничего не говорят и только удлиняют текст.
    """
    body = "".join(f"<p>{x}</p>" for x in lines if x)
    return (f'<details class="gd-help"><summary>{C.esc(title)}</summary>'
            f'<div class="gd-help-b">{body}</div></details>')


def _gosb_dialog(c: dict, det: dict, d: dict, uid: str, unit_label: str = "ГОСБ",
                 wow: str = "") -> str:
    """Оверлей «почему прогноз такой» по одному ГОСБ.

    Порядок блоков: портфель → отток по группам → пайплайн → тренд портфеля →
    разбор по сегментам. Отток разложен на группы (см. `_out_group`) и покрыт целиком;
    в остальных блоках имена показываются только материальные, поэтому у них стоит
    подпись о покрытии.
    """
    if not det:
        return ""
    pf = det["pf"]
    # у единицы свой коэффициент реализуемости и своя доля пройденного месяца
    wf_html = _wf_lines(pf, d, det["conv"], det.get("conv_diag"),
                        det.get("conv_is_tb", False))
    yoy = det["yoy_total"]
    # отток разложен по причине: первая (крупнейшая) группа раскрыта, иначе оверлей
    # встречает читателя четырьмя закрытыми строками без единого имени
    groups = det.get("out_groups") or []
    out_html = ("".join(_out_group(g, i == 0) for i, g in enumerate(groups)) if groups
                else '<div class="gd-note">нет организаций с заметным вкладом</div>')
    n_out = det.get("out_n_all", 0)
    # в блоке только просевшие: он отвечает на «почему потеряли», и выросшие
    # организации ответа на этот вопрос не содержат
    yoy_groups = det.get("yoy_groups") or []
    yoy_html = ("".join(_yoy_group(g, i == 0) for i, g in enumerate(yoy_groups))
                if yoy_groups else
                '<div class="gd-note">просевших за год организаций нет</div>')
    yoy_head = (f'<h4>Портфель год к году: '
                f'<span style="color:{"var(--bad)" if yoy < 0 else "var(--good)"}">'
                f'{"−" if yoy < 0 else "+"}{C.fmt_num(abs(yoy))} чел</span>'
                f' · снижение у {C.fmt_num(det.get("yoy_n_all", 0))} орг на '
                f'−{C.fmt_num(abs(det.get("yoy_down_tot", 0)))}</h4>')
    return (
        f'<dialog class="gd" id="gd-{uid}"><div class="gd-sheet">'
        f'<div class="gd-head"><div><h3 style="margin:0">{C.esc(c["gosb_name"])}</h3>'
        # не .gd-note: это главная строка шапки, а цвет подписи делал её нечитаемой
        f'<div class="gd-fc">Прогноз <b>{C.fmt_num(pf["forecast"])}</b> из плана '
        f'{C.fmt_num(pf["plan"])} · <b style="color:{_col(pf.get("exec"))}">'
        f'{_pct(pf.get("exec"))}</b> · {_delta_html(pf["forecast"] - pf["plan"], "чел")}'
        f'</div>{wow}</div>'
        f'<div class="gd-head-actions">'
        f'<button type="button" class="gd-export" onclick="event.stopPropagation();'
        f'exportCardPdf(\'{uid}\')" title="Экспорт карточки в PDF, все разделы развёрнуты">'
        f'Экспорт PDF</button>'
        f'<button type="button" class="gd-close" onclick="gdClose(\'{uid}\')" '
        f'aria-label="Закрыть">×</button></div></div>'

        f'<div class="gd-block"><h4>Портфель, потери и приход</h4>{wf_html}</div>'

        f'<div class="gd-block"><h4>Отток по причинам — всего '
        f'{C.fmt_num(det["out_tot"])} чел, {C.fmt_num(n_out)} орг</h4>'
        + _gd_help(
            f'<b>Что за число.</b> Сотрудники организаций, которые за три закрытых '
            f'месяца ({C.esc((d or {}).get("out_label", ""))}) перестали получать '
            f'зарплату в банке и до конца периода не вернулись. Если часть людей '
            f'вернулась, в потери идёт только разница.',
            '<b>Кто попадает в блок.</b> Организации, потерявшие от трёх человек: '
            'уход одного-двух — это текучка, и разбирать её поимённо смысла нет.',
            '<b>Группы.</b> «Ушли и не вернулись» и «Вернулись частично» вместе '
            'покрывают блок целиком. «Ушли в последнем закрытом месяце» — срез той '
            'же потери по свежести, поэтому организация может быть и там, и там; '
            'в итог блока она при этом входит один раз.',
            '<b>Имена внутри группы.</b> Названы крупнейшие организации — столько, '
            'сколько объясняет основную часть потерь группы. Остальные собраны '
            'в строку «хвост» с их числом и суммой, чтобы итог сходился.')
        + out_html
        + '</div>'

        f'<div class="gd-block"><h4>Пайплайн на месяц: заявлено '
        f'{C.fmt_num(pf["pipe_raw"])}, в прогнозе {C.fmt_num(pf["pipe"])}</h4>'
        + _org_rows(det["top_pipe"], "pipe", det["pipe_tail_n"], det["pipe_tail_fl"],
                    "— хвост", 1, _why_pipe, cover=det.get("pipe_cov", 0.0),
                    n_all=det.get("pipe_n_all", 0))
        + '</div>'

        f'<div class="gd-block">{yoy_head}'
        + _gd_help(
            '<b>Что за число.</b> Численность получателей в каждой организации '
            'сравнивается с тем же месяцем год назад. Разница и есть годовое '
            'изменение по организации.',
            '<b>Почему итог блока меньше.</b> В списках только организации, где '
            'людей стало меньше: блок отвечает на вопрос «где потеряли за год», и '
            'выросшие организации ответа на него не содержат. Число в заголовке — '
            'изменение портфеля целиком, с учётом роста.',
            '<b>Группы.</b> Раскладка идёт по тому, чем объясняется снижение: был '
            'заметный уход людей и по нему велась работа; уход был, но работа по '
            'нему в системе не отражена; месяцы ухода старше периода, за который '
            'мы видим работу по клиентам; численность снижалась постепенно, без '
            'заметных уходов.',
            '<b>Имена внутри группы.</b> Названы крупнейшие организации — столько, '
            'сколько объясняет основную часть снижения группы; остальные собраны '
            'в строку «хвост».')
        + yoy_html + '</div>'

        + f'<div class="gd-block">'
        + _trend_block(det.get("trend") or [], "Динамика за 12 месяцев")
        + '</div>'

        + f'<div class="gd-block"><h4>Разбор по сегментам</h4>{_gosb_table(c, wide=True)}</div>'
        f'</div></dialog>'
    )


def _problem_gosb(a: analyze.Analysis, ai: str | None = None, idx: int = 0) -> str:
    """Карточки единиц уровня: ГОСБ внутри ТБ, ТБ внутри банка.

    `idx` — номер уровня в документе; из него собираются id оверлеев. В одном файле
    лежат все 13 отчётов, и без префикса id оверлея ГОСБ мог бы совпасть с id
    оверлея ТБ, а клик открывал бы чужую карточку.
    """
    if not a.gosb_cards:
        return ""
    cards, dialogs = [], []
    d = a.dates or {}
    unit = a.unit_label
    for c in a.gosb_cards:
        # Цвет карточки — СТРОГО по выполнению прогноза, тем же правилом, что и
        # везде в отчёте (C.status_of: ≥100% зелёный, 95–100% жёлтый, ниже красный).
        # Раньше здесь было своё правило: единица с выполнением 107% и одним
        # невыполненным сегментом красилась жёлтым, а число «107%» рядом с зелёным
        # 107% в матрице и в плашке читалось как ошибка расчёта. Заодно уходил порог
        # 0.9, из-за которого 92% на карточке были жёлтыми, а в матрице — красными.
        # Про сегменты говорит бейдж ниже: он и остаётся жёлтым, когда план вытянут
        # другими сегментами.
        st = C.status_of(c["exec"])
        seg_html = _gosb_table(c)
        # пояснения по западающим сегментам в строку таблицы не влезают — отдельно
        notes = []
        for s in c["segs_bad"]:
            cov = s["coverage"]
            if not s["n_avail"]:
                notes.append(f'в {s["seg"]} собственных организаций нет, нужна компенсация за счёт других сегментов')
            elif cov is not None and cov < 0.999:
                notes.append(f'в {s["seg"]} хватает на {cov*100:.0f}% ({s["n_avail"]} орг)')
        note_html = (f'<div class="g-act">{C.esc(" · ".join(notes[:3]))}</div>'
                     if notes else "")
        act = c["act"]
        if c["healthy"]:
            head_badge = C.badge("план выполняется по всем сегментам", "good")
        elif c["seg_only"]:
            head_badge = C.badge(f'план выполняется, не выполнен в сегментах: '
                                 f'{", ".join(s["seg"] for s in c["segs_bad"][:3])}', "warn")
        else:
            head_badge = C.badge("−" + C.fmt_num(c["gap"]) + " чел до плана", st)
        filler = (f' · компенсация за счёт других сегментов: <b>{c["filler_n"]}</b> орг '
                  f'(+{C.fmt_num(c["filler_fl"])})' if c["filler_n"] else "")
        cover = (c["fl_need"] / c["gap_seg"]) if c["gap_seg"] > 0 else None
        short = (f' · покрывает <b>{cover*100:.0f}%</b> отклонения, '
                 f'собственный потенциал {unit} исчерпан'
                 if cover is not None and cover < 0.999 else "")
        do = (
            f'<div class="g-do">Итого под план: <b>{c["n_need"]}</b> организаций '
            f'(+{C.fmt_num(c["fl_need"])} чел, привлечь {c["n_attract"]} / '
            f'вернуть {c["n_return"]}) · ФОТ <b>~{C.fmt_num(c["fot_need"])}</b> млн ₽'
            f'{filler}{short}</div>'
            f'{note_html}'
            f'<div class="g-act">Активности 3 мес: {act["act_n"]} по {act["worked_orgs"]} орг, '
            f'успех {act["success"]*100:.0f}% · из нужных под план не работали с '
            f'<b>{c["not_worked"]}</b></div>'
        )
        gid = c["gosb_id"]
        uid = f"{idx}-{gid}"           # уникален в пределах документа со всеми уровнями
        det = (a.gosb_detail or {}).get(gid)
        # Подписи «Почему такой прогноз →» на карточке нет: о том, что карточка
        # кликабельна, говорит стрелка в её углу, и она же стоит на карточках без
        # разбора — лишняя строка только удлиняла карточку.
        more = ""
        # на уровне банка карточка ТБ ведёт ещё и в его собственный разбор — это и есть
        # переход СБ → ТБ; onclick останавливаем, чтобы не открылся заодно оверлей
        drill = (f'<div class="g-more g-drill" onclick="event.stopPropagation();'
                 f'lvlGo({c["lvl"]})">Открыть разбор {C.esc(c["gosb_name"])} →</div>'
                 if c.get("lvl") else "")
        # Выполнение прогноза — главное число карточки, поэтому оно стоит крупно,
        # подписано словом и окрашено по статусу. Одного цвета мало: рядом со
        # знаком «%» идёт подпись, а статус продублирован бейджем ниже.
        inner = (
            f'<div class="g-head"><h3 style="margin:0">{C.esc(c["gosb_name"])}</h3>'
            f'<span class="g-ex {st}"><i>прогноз</i>{_pct(c["exec"])}</span></div>'
            + C.meter(c["exec"])
            + f'<div class="g-fc">{C.fmt_num(c["forecast"])} из '
              f'{C.fmt_num(c["plan"])} · {_delta_html(c["forecast"] - c["plan"], "чел")}'
              f'</div>'
            # куда сдвинулся прогноз этой единицы с прошлой сборки: по карточкам
            # видно, где неделя что-то изменила, а где всё стоит на месте
            + _wow_unit(a, gid)
            + f'<div style="margin:2px 0 6px">{head_badge}</div>'
            + f'{seg_html}{do}{more}{drill}'
        )
        # карточка кликабельна целиком; role/tabindex — чтобы работала и с клавиатуры
        attrs = (f' role="button" tabindex="0" onclick="gdOpen(\'{uid}\')" '
                 f'onkeydown="if(event.key===\'Enter\'||event.key===\' \')'
                 f'{{event.preventDefault();gdOpen(\'{uid}\');}}"' if det else "")
        cards.append(f'<div class="card gcard {st}"{attrs}>{inner}</div>')
        if det:
            dialogs.append(_gosb_dialog(c, det, d, uid, unit,
                                        _wow_unit(a, gid, "gd-wow")))
    grid = f'<div class="gcards">{"".join(cards)}</div>{"".join(dialogs)}'
    return C.section(f"Детализация по {unit}", grid + _ai(ai),
                     eyebrow=f"Детализация по {unit} · клик открывает разбор до организаций")


def _cell_dialog() -> str:
    """Оверлей раскрытой ячейки матрицы — ОДИН на весь документ.

    Ячеек в отчёте шестьсот с лишним, и отдельный оверлей на каждую раздул бы
    файл ради содержимого, которое смотрят точечно. Разметку наполняет скрипт из
    словаря уровня (см. `_cell_data`), а заголовок собирает из самой таблицы —
    названия единиц в данных не повторяются.
    """
    return (
        '<dialog class="gd cd" id="cell-dlg"><div class="gd-sheet">'
        '<div class="gd-head"><div>'
        '<h3 style="margin:0" id="cd-title"></h3>'
        '<div class="gd-fc" id="cd-sub"></div></div>'
        '<div class="gd-head-actions">'
        '<button type="button" class="gd-export" onclick="cellXls()" '
        'title="Выгрузить список в Excel">Экспорт в Excel</button>'
        '<button type="button" class="gd-close" onclick="cellClose()" '
        'aria-label="Закрыть">×</button></div></div>'
        '<div class="gd-block"><h4 id="cd-h4">Клиенты со снижением по получателям '
        'за год</h4>'
        + _gd_help(
            '<b>Что за список.</b> Организации этой ячейки — то есть этого '
            'подразделения и этого сегмента, — у которых получателей зарплаты '
            'стало меньше, чем в том же месяце год назад. Снижение считается по '
            'каждой организации отдельно: численность на конец закрытого месяца '
            'минус численность на тот же месяц годом ранее.',
            '<b>Кто в список не попадает.</b> Организации, где за год стало '
            'больше или столько же: ячейку раскрывают, чтобы понять, где потеряли '
            'людей, и выросшие клиенты на этот вопрос не отвечают. Отсекается '
            'также снижение меньше одного человека — это округление витрины.',
            '<b>Сколько показано.</b> Десять крупнейших снижений. Сколько '
            'организаций в ячейке просело всего и на сколько человек — в строке '
            'под заголовком, чтобы часть не принимали за целое.',
            '<b>Как это связано с процентом в ячейке.</b> Никак не выводится одно '
            'из другого: процент — выполнение ПРОГНОЗА на текущий месяц, а список — '
            'ФАКТ за год. Список объясняет, откуда взялось падение базы, на которой '
            'строится прогноз.',
            '<b>Сегмент.</b> Берётся из справочника клиентов и закреплён за самой '
            'организацией, поэтому в строке подразделения она стоит ровно в одной '
            'ячейке.')
        + '<div id="cd-rows"></div></div>'
        '</div></dialog>'
    )


# Раскрытие ячейки матрицы: состав берётся из словаря уровня, разметка строится
# на месте. Оверлей один на документ — см. `_cell_dialog`.
_CELL_JS = """
<script>
window.__cdRows = null;
function cellEsc(s){
  return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
  });
}
function cellNum(n){ return Number(n).toLocaleString('ru-RU'); }
function cellClose(){
  var d = document.getElementById('cell-dlg');
  if(d && d.open) d.close();
}
function cellOpen(td){
  var dlg = document.getElementById('cell-dlg');
  if(!dlg || !td) return;
  var lvl = td.closest('.lvl');
  var key = td.getAttribute('data-cell') || '';
  var cell = ((window.__CELLS || {})[lvl ? lvl.id : ''] || {})[key];
  var seg = key.split('|')[1] || '';
  var tr = td.closest('tr');
  var unit = (tr && tr.cells.length) ? tr.cells[0].textContent.trim() : '';
  var pct = td.textContent.trim();

  document.getElementById('cd-title').textContent = unit + ' · ' + seg;
  var sub = 'Выполнение прогноза ' + pct;
  if(cell){
    sub += ' · за год просели ' + cellNum(cell.n) + ' орг на \\u2212'
        + cellNum(cell.fl) + ' чел';
  }
  document.getElementById('cd-sub').textContent = sub;

  var rows = (cell && cell.rows) || [];
  /* формулировки без согласования с числом: «1 крупнейших» в отчёте правления
     выглядело бы опечаткой, а склонять числительные в скрипте незачем */
  var head = document.getElementById('cd-h4');
  head.textContent = rows.length
    ? ('Клиенты со снижением по получателям за год: ' + rows.length
       + (cell.n > rows.length ? ' из ' + cellNum(cell.n) : ''))
    : 'Клиенты со снижением по получателям за год';

  var host = document.getElementById('cd-rows');
  if(!rows.length){
    host.innerHTML = '<div class="gd-note">В этой ячейке организаций со снижением '
      + 'по получателям за год нет.</div>';
    window.__cdRows = null;
  } else {
    host.innerHTML = rows.map(function(r){
      var emp = r[4] ? '<span class="gd-emp">' + cellEsc(r[4]) + '</span>' : '';
      return '<div class="gd-row"><span>' + cellEsc(r[0]) + emp + '</span>'
        + '<span>\\u2212' + cellNum(Math.abs(r[1])) + '</span>'
        + '<span class="gd-why">сейчас ' + cellNum(r[2]) + ' чел, год назад '
        + cellNum(r[3]) + ' чел</span></div>';
    }).join('');
    if(cell.n > rows.length){
      host.innerHTML += '<div class="gd-note">Эти строки объясняют \\u2212'
        + cellNum(cell.top) + ' чел из \\u2212' + cellNum(cell.fl)
        + ' чел снижения ячейки.</div>';
    }
    window.__cdRows = [['Организация', 'Ответственный', 'Изменение за год, чел',
                        'Сейчас, чел', 'Год назад, чел']].concat(
      rows.map(function(r){ return [r[0], r[4] || '', r[1], r[2], r[3]]; }));
    window.__cdName = unit + ' ' + seg + ' — снижение за год';
  }
  dlg.showModal();
}
(function(){
  var d = document.getElementById('cell-dlg');
  if(d) d.addEventListener('click', function(e){ if(e.target === d) d.close(); });
})();
function cellXls(){
  if(!window.__cdRows || typeof window.xlsxDownload !== 'function') return;
  window.xlsxDownload(window.__cdName || 'Ячейка матрицы', window.__cdRows);
}
</script>
"""


# Открытие/закрытие оверлея. Нативный <dialog>: Esc работает сам, фокус
# возвращается браузером. Клик по подложке закрываем вручную — по умолчанию не закрывает.
_GD_JS = """
<script>
function gdOpen(id){var d=document.getElementById('gd-'+id); if(d) d.showModal();}
function gdClose(id){var d=document.getElementById('gd-'+id); if(d) d.close();}
document.querySelectorAll('dialog.gd').forEach(function(d){
  d.addEventListener('click', function(e){ if(e.target===d) d.close(); });
});

/* Экспорт карточки ТБ/ГОСБ в PDF, развёрнутый вид: содержимое оверлея (и,
   если ux.js уже вынес блок «Портфель, потери и приход» в отдельное
   всплывающее окно gd-{uid}-mgmt — содержимое обоих окон) клонируется в
   #print-root, все <details> внутри клона раскрываются, и печатается только
   этот блок — печать через window.print() выбрана, потому что страница
   самодостаточна и без сети (закрытый контур), внешнюю библиотеку под PDF
   подключить нельзя. */
function exportCardPdf(uid){
  var main = document.getElementById('gd-' + uid);
  var root = document.getElementById('print-root');
  if(!main || !root) return;
  var mainSheet = main.querySelector('.gd-sheet');
  if(!mainSheet) return;
  root.innerHTML = '';

  var mainClone = mainSheet.cloneNode(true);
  mainClone.querySelectorAll('.gd-close, .mgmt-trigger, .xls-bar').forEach(function(el){ el.remove(); });
  var mainHead = mainClone.querySelector(':scope > .gd-head');
  if(mainHead) root.appendChild(mainHead);

  var mgmt = document.getElementById('gd-' + uid + '-mgmt');
  if(mgmt){
    var mgmtSheet = mgmt.querySelector('.gd-sheet');
    if(mgmtSheet){
      var mgmtClone = mgmtSheet.cloneNode(true);
      mgmtClone.querySelectorAll('.gd-close').forEach(function(el){ el.remove(); });
      while(mgmtClone.firstChild) root.appendChild(mgmtClone.firstChild);
    }
  }
  Array.prototype.slice.call(mainClone.children).forEach(function(el){ root.appendChild(el); });

  root.querySelectorAll('details').forEach(function(d){ d.open = true; });
  document.body.classList.add('printing-card');
  window.print();
}
/* Экспорт ЦЕЛОГО УРОВНЯ (банк или ТБ) в PDF, развёрнутый вид.
   Уровень после ux.js выглядит не так, как в исходном HTML: разделы завёрнуты в
   свёрнутые <details>, плашка показателей сворачивается через style.display, а
   «Управление портфелем» вообще вынесено из уровня в отдельное окно mgmt-lvl-N.
   Поэтому клон собирается из ОБОИХ мест и приводится к раскрытому виду. */
function exportLevelPdf(idx){
  var lvl = document.getElementById('lvl-' + idx);
  var root = document.getElementById('print-root');
  if(!lvl || !root) return;
  root.innerHTML = '';
  var clone = lvl.cloneNode(true);
  clone.removeAttribute('hidden');

  var mgmt = document.getElementById('mgmt-lvl-' + idx);
  if(mgmt){
    var sheet = mgmt.querySelector('.gd-sheet');
    if(sheet){
      var sec = document.createElement('section');
      var mc = sheet.cloneNode(true);
      mc.querySelectorAll('.gd-close').forEach(function(el){ el.remove(); });
      while(mc.firstChild) sec.appendChild(mc.firstChild);
      clone.appendChild(sec);
    }
  }
  /* оверлеи карточек в отчёт уровня не кладём: у каждой карточки своя кнопка,
     а вместе они дают сотни страниц вместо отчёта */
  clone.querySelectorAll('dialog').forEach(function(el){ el.remove(); });
  /* интерактивная обвязка на бумаге бессмысленна */
  clone.querySelectorAll('.lvl-pdf, .lvl-up, .forecast-toggle, .mgmt-trigger, '
    + '.gd-export, .gd-close, .gosb-finder, .ts-dropdown, .xls-bar').forEach(function(el){
    el.remove();
  });
  clone.querySelectorAll('details').forEach(function(d){ d.open = true; });
  /* плашка показателей могла быть свёрнута кнопкой — возвращаем */
  clone.querySelectorAll('[style]').forEach(function(el){
    if(el.style && el.style.display === 'none') el.style.display = '';
  });
  root.appendChild(clone);
  document.body.classList.add('printing-card');
  window.print();
}
window.addEventListener('afterprint', function(){
  document.body.classList.remove('printing-card');
  var root = document.getElementById('print-root');
  if(root) root.innerHTML = '';
});
</script>
"""

# Выгрузка таблиц в Excel.
#
# Файл .xlsx собирается ПРЯМО В БРАУЗЕРЕ: отчёт уезжает в закрытый контур одним
# документом без сети, подключить SheetJS или любую другую библиотеку нельзя.
# Внутри .xlsx — обычный zip из пяти маленьких XML, и пишется он без сжатия
# (метод store): Excel такой архив открывает штатно, а deflate потребовал бы
# реализовать сжатие руками ради файла в пару десятков килобайт.
#
# CSV сознательно не выбран: русские заголовки и разделитель зависят от локали
# Windows, и «экспорт в Excel» у половины читателей открывался бы одной колонкой
# с кракозябрами.
_XLS_JS = """
<script>
(function(){
  var CRC = (function(){
    var t = new Uint32Array(256), c, n, k;
    for(n = 0; n < 256; n++){
      c = n;
      for(k = 0; k < 8; k++){ c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1); }
      t[n] = c >>> 0;
    }
    return t;
  })();
  function crc32(buf){
    var c = 0xFFFFFFFF;
    for(var i = 0; i < buf.length; i++){ c = CRC[(c ^ buf[i]) & 0xFF] ^ (c >>> 8); }
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  var enc = new TextEncoder();

  /* zip без сжатия: локальные заголовки, центральный каталог, хвост */
  function zip(files){
    var parts = [], central = [], offset = 0, cdSize = 0;
    files.forEach(function(f){
      var name = enc.encode(f.name), data = f.data;
      var crc = crc32(data), size = data.length;
      var lh = new Uint8Array(30 + name.length), dv = new DataView(lh.buffer);
      dv.setUint32(0, 0x04034b50, true);
      dv.setUint16(4, 20, true);
      dv.setUint16(6, 0x0800, true);   /* имена в UTF-8 */
      dv.setUint32(14, crc, true);
      dv.setUint32(18, size, true);
      dv.setUint32(22, size, true);
      dv.setUint16(26, name.length, true);
      lh.set(name, 30);
      parts.push(lh, data);

      var ch = new Uint8Array(46 + name.length), cv = new DataView(ch.buffer);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);
      cv.setUint16(8, 0x0800, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, size, true);
      cv.setUint32(24, size, true);
      cv.setUint16(28, name.length, true);
      cv.setUint32(42, offset, true);
      ch.set(name, 46);
      central.push(ch);
      cdSize += ch.length;
      offset += lh.length + size;
    });
    var end = new Uint8Array(22), ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(8, files.length, true);
    ev.setUint16(10, files.length, true);
    ev.setUint32(12, cdSize, true);
    ev.setUint32(16, offset, true);
    return new Blob(parts.concat(central, [end]),
                    {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
  }

  function xesc(s){
    return String(s == null ? '' : s)
      .replace(/[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f]/g, '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function colName(i){
    var s = '';
    do { s = String.fromCharCode(65 + (i % 26)) + s; i = Math.floor(i / 26) - 1; }
    while(i >= 0);
    return s;
  }
  /* «12 345» и «−1 234» в выгрузке должны быть ЧИСЛАМИ, иначе в Excel по ним
     не построить ни сумму, ни сортировку. Проценты остаются текстом: «107%» —
     это подпись, а не доля, и превращать её в 1.07 значило бы менять смысл. */
  function cellVal(v){
    if(typeof v === 'number') return isFinite(v) ? v : '';
    var t = String(v == null ? '' : v)
      .replace(/\\u2212/g, '-').replace(/[\\s\\u00a0\\u2009]/g, '');
    if(/^-?\\d+(?:[.,]\\d+)?$/.test(t)) return Number(t.replace(',', '.'));
    return String(v == null ? '' : v);
  }
  function sheetXml(rows){
    var out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
      + '<sheetData>'];
    rows.forEach(function(row, ri){
      out.push('<row r="' + (ri + 1) + '">');
      row.forEach(function(v, ci){
        var ref = colName(ci) + (ri + 1), val = cellVal(v);
        if(typeof val === 'number'){
          out.push('<c r="' + ref + '"><v>' + val + '</v></c>');
        } else if(val !== ''){
          out.push('<c r="' + ref + '" t="inlineStr"><is><t xml:space="preserve">'
            + xesc(val) + '</t></is></c>');
        }
      });
      out.push('</row>');
    });
    out.push('</sheetData></worksheet>');
    return out.join('');
  }
  function sheetName(title){
    var s = String(title || 'Лист1').replace(/[\\[\\]:*?\\/\\\\]/g, ' ').trim();
    return s.slice(0, 28) || 'Лист1';
  }
  function fileName(title){
    var s = String(title || 'Выгрузка').replace(/[\\\\/:*?"<>|]/g, '-')
      .replace(/\\s+/g, ' ').trim();
    return s.slice(0, 90) + '.xlsx';
  }

  /* Публичная точка: массив массивов -> .xlsx в загрузки браузера */
  window.xlsxDownload = function(title, rows){
    if(!rows || !rows.length) return;
    var files = [
      {name: '[Content_Types].xml', data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        + '</Types>')},
      {name: '_rels/.rels', data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        + '</Relationships>')},
      {name: 'xl/workbook.xml', data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        + 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        + '<sheets><sheet name="' + xesc(sheetName(title)) + '" sheetId="1" r:id="rId1"/></sheets>'
        + '</workbook>')},
      {name: 'xl/_rels/workbook.xml.rels', data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        + '</Relationships>')},
      {name: 'xl/worksheets/sheet1.xml', data: enc.encode(sheetXml(rows))}
    ];
    var blob = zip(files);
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = fileName(title);
    document.body.appendChild(a);
    a.click();
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
  };

  /* Текст ячейки таблицы: подписи второй строкой (ответственный, «Орг. N»)
     склеиваем через « · », иначе в выгрузке они слипались бы с названием. */
  function cellText(td){
    var c = td.cloneNode(true);
    /* фильтры в шапке таблицы — это управление, а не заголовок: в выгрузке от
       них остался бы список всех вариантов вместо названия колонки */
    Array.prototype.forEach.call(c.querySelectorAll('select,input,button'),
      function(el){ el.remove(); });
    Array.prototype.forEach.call(c.querySelectorAll('div,p,br'), function(el){
      el.parentNode.insertBefore(document.createTextNode(' \\u00b7 '), el);
    });
    return (c.textContent || '').replace(/\\s+/g, ' ')
      .replace(/(\\s*\\u00b7\\s*)+/g, ' \\u00b7 ')
      .replace(/^\\s*\\u00b7\\s*|\\s*\\u00b7\\s*$/g, '').trim();
  }
  function tableRows(t){
    var out = [];
    Array.prototype.forEach.call(t.rows, function(tr){
      if(!tr.cells.length) return;
      out.push(Array.prototype.map.call(tr.cells, cellText));
    });
    return out;
  }
  function tableTitle(t){
    var box = t.closest('.card, .gd-sheet, .section-body, section');
    var h = box ? box.querySelector('h2, h3, h4') : null;
    var name = h ? h.textContent.trim() : 'Таблица';
    var lvl = t.closest('.lvl');
    var who = lvl ? lvl.querySelector('.lvl-name') : null;
    return (who ? who.textContent.trim() + ' — ' : '') + name;
  }
  function exportTable(t){
    /* у интерактивного списка организаций своя выгрузка: в файл уходят ВСЕ
       отобранные фильтром строки, а не видимая страница из пятнадцати */
    var hook = (window.__xlsRows || {})[t.id];
    var rows = hook ? hook() : tableRows(t);
    window.xlsxDownload(tableTitle(t), rows);
  }
  function addButtons(){
    Array.prototype.forEach.call(document.querySelectorAll('table'), function(t){
      if(t.dataset.xlsReady === '1') return;
      t.dataset.xlsReady = '1';
      /* кнопку ставим вплотную к таблице: если таблица завёрнута в контейнер
         прокрутки (и он больше ничего не держит) — перед контейнером, иначе
         перед самой таблицей. Иначе кнопка уезжала бы выше заголовка карточки */
      var host = t, p = t.parentElement;
      if(p && p.children.length === 1
         && (p.classList.contains('tbl-scroll')
             || (p.style && p.style.overflowX === 'auto'))){
        host = p;
      }
      if(!host.parentNode) return;
      var bar = document.createElement('div');
      bar.className = 'xls-bar';
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'xls-btn';
      btn.textContent = 'Экспорт в Excel';
      btn.title = 'Выгрузить таблицу в файл .xlsx';
      btn.addEventListener('click', function(e){
        e.preventDefault();
        e.stopPropagation();
        exportTable(t);
      });
      bar.appendChild(btn);
      /* таблица может назвать своё место для кнопки: у списка организаций это
         строка с целью и поиском — иначе выгрузка занимала бы отдельный ряд */
      var into = t.getAttribute('data-xls-into');
      var slot = into ? document.getElementById(into) : null;
      if(slot){ slot.appendChild(bar); }
      else { host.parentNode.insertBefore(bar, host); }
    });
  }
  addButtons();
  /* уровни переключаются, карточки открываются — таблицы в них те же, но
     разметку двигает скрипт дизайна, поэтому проверяем ещё раз после кликов */
  document.addEventListener('click', function(){ setTimeout(addButtons, 0); });
  window.addEventListener('hashchange', function(){ setTimeout(addButtons, 0); });
})();
</script>
"""


# Переключение уровней. Адрес страницы пишем в hash: тогда работает кнопка «назад»
# браузера и на конкретный уровень можно дать ссылку. Открытый оверлей перед
# переходом закрываем — иначе модалка одного уровня останется поверх другого.
_LVL_JS = """
<script>
function lvlGo(i){
  document.querySelectorAll('dialog.gd[open]').forEach(function(d){ d.close(); });
  document.querySelectorAll('.lvl').forEach(function(el, k){ el.hidden = (k !== i); });
  document.querySelectorAll('.lvl-tab').forEach(function(b){
    b.classList.toggle('on', Number(b.dataset.lvl) === i); });
  if (location.hash !== '#lvl-' + i) location.hash = '#lvl-' + i;
  window.scrollTo({top: 0, behavior: 'instant'});
}
function lvlFromHash(){
  var m = /^#lvl-(\\d+)$/.exec(location.hash || '');
  var i = m ? Number(m[1]) : 0;
  if (!document.getElementById('lvl-' + i)) i = 0;
  document.querySelectorAll('.lvl').forEach(function(el, k){ el.hidden = (k !== i); });
  document.querySelectorAll('.lvl-tab').forEach(function(b){
    b.classList.toggle('on', Number(b.dataset.lvl) === i); });
}
window.addEventListener('hashchange', lvlFromHash);
lvlFromHash();
</script>
"""


# Переключатель списков в разделе «Отток». Панели прячет скрипт, а не разметка:
# в HTML открыты обе, поэтому при вырезанном JS раздел остаётся полным.
_OF_JS = """
<script>
function ofTab(btn, i){
  var w = btn.closest('.of-wrap');
  if(!w) return;
  w.querySelectorAll(':scope > .of-tabs > .of-tab').forEach(function(b, k){
    b.classList.toggle('on', k === i);
    b.setAttribute('aria-selected', k === i ? 'true' : 'false');
  });
  w.querySelectorAll(':scope > .of-pane').forEach(function(p, k){ p.hidden = (k !== i); });
}
(function(){
  document.querySelectorAll('.of-wrap').forEach(function(w){
    if(w.querySelectorAll(':scope > .of-tabs > .of-tab').length < 2) return;
    w.classList.add('tabbed');
    w.querySelectorAll(':scope > .of-pane').forEach(function(p, k){ p.hidden = (k !== 0); });
  });
})();
</script>
"""


def _orgs(a: analyze.Analysis, ai: str | None = None, idx: int = 0) -> str:
    """Список к отработке: по умолчанию — ровно те, кем закрывается план.

    Отбор считается в Python внутри западающих сегментов каждого ГОСБ, а строке
    проставляется need_k — минимальная цель, при которой организация нужна. Поэтому
    фильтр «Цель» в HTML просто сравнивает need_k с коэффициентом и работает поверх
    остальных фильтров.

    В ФАЙЛ едут не все кандидаты: на проме их около 70 тыс., и хвост организаций по
    одному-два человека давал 20 МБ HTML, не давая ничего для работы. Порог
    `explorer_min_fl` отсекает строки мельче него — ВСЕ, включая нужные под план.

    Порог живёт ТОЛЬКО здесь, в представлении: ни отбор, ни sim, ни карточки ГОСБ от
    него не зависят, и это принципиально. Разрыв сегмента считается по прогнозу, а не
    по составу кандидатов; убери мелкие организации из отбора — сегменту искусственно
    «не хватит» своих, и включится добор из другого сегмента там, где своих хватало.

    Поэтому заголовок «N организаций закрывают план» считается по полному набору и
    заведомо больше числа показанных строк. Молчать об этом нельзя — расхождение
    подписывается явно.
    """
    min_fl = float(a.explorer_min_fl or 0)
    rows, n_all, n_hidden_plan = [], 0, 0
    for r in a.to_work.itertuples():
        n_all += 1
        if min_fl > 0 and float(r.impact_fl) < min_fl:
            if float(getattr(r, "need_k", 0.0)) > 0:
                n_hidden_plan += 1
            continue
        ins = a.insights.get((int(r.new_gosb_id), int(r.inn)), {})
        reason = ins.get("reason") or r.reason
        if bool(getattr(r, "filler", False)):
            reason = "компенсация за счёт другого сегмента · " + reason
        rows.append({
            "inn": int(r.inn), "company": (getattr(r, "company_name", "") or "")[:48],
            "lever": r.lever,
            "gosb": (r.gosb_name or "")[:28], "seg": r.seg_name or "—",
            # закреплённый за парой (ГОСБ, организация) сотрудник; прочерк означает,
            # что действующего закрепления в витрине нет — см. bank._merge_manager.
            # isinstance, а не `or ""`: у пары без закрепления в колонке может
            # оказаться NaN, а он проходит проверку на истинность и роняет strip()
            "emp": (r.emp_fio.strip() if isinstance(getattr(r, "emp_fio", None), str)
                    else "") or "—",
            "fl": round(float(r.impact_fl)), "fot": round(float(r.impact_fot_mln), 1),
            "reason": reason, "action": ins.get("action", ""),
            "needk": float(getattr(r, "need_k", 0.0)),
        })
    # фильтр по ГОСБ — в алфавитном порядке, как и карточки со строками матрицы
    gosb_opts = sorted({row["gosb"] for row in rows}, key=analyze._ru_key)
    seg_opts = [s for s in SEG_ORDER if s in {row["seg"] for row in rows}]
    cut = min_fl > 0 and len(rows) < n_all
    if cut:
        progress.done(f"{a.tb_short}: порог списка {min_fl:.0f} чел — в файл не попали "
                      f"{n_all - len(rows)} кандидатов из {n_all}, из них нужных под "
                      f"план {n_hidden_plan}. На отбор, n_need и покрытие порог не "
                      f"влияет — он только про видимость строк")
    all_label = (f"Все с эффектом от {C.fmt_num(min_fl)} чел" if cut
                 else "Все организации")
    # свой id на каждый уровень: в одном документе живут списки всех ТБ, и общий
    # префикс заставил бы скрипт одного уровня править таблицу другого
    explorer = C.orgs_explorer(f"work{idx}", rows, gosb_opts, seg_options=seg_opts,
                               all_label=all_label)
    sim = a.sim
    # именно segs_bad: в segs теперь лежат ВСЕ сегменты ГОСБ, включая выполняющие
    bad_segs = sorted({s["seg"] for c in a.gosb_cards for s in c["segs_bad"]},
                      key=lambda x: SEG_ORDER.index(x) if x in SEG_ORDER else 99)
    n_ret = sum(1 for r in rows if r["lever"] == "Вернуть")
    # Подпись под заголовком — ОДНА фраза: что за список и сколько в нём строк.
    # Раньше здесь висел абзац из пяти оговорок через точку с запятой — правила
    # отбора, порог видимости, скрытые кандидаты, смысл рычага «Вернуть», — и
    # читать его никто не начинал. Всё это никуда не делось, но переехало в
    # свёрнутую подсказку «Как собран список»: объяснение нужно один раз.
    cand = (f'В таблице {C.fmt_num(len(rows))} из {C.fmt_num(n_all)} кандидатов'
            if cut else f'В таблице {C.fmt_num(len(rows))} '
                        f'{_plural(len(rows), "организация", "организации", "организаций")}')
    # хвост не прячем молча: в подсказке сказано, сколько кандидатов есть всего,
    # по какому правилу часть из них не попала в файл и сколько среди скрытых
    # нужных под план — иначе заголовок «N закрывают план» расходился бы с таблицей
    help_html = _gd_help(
        f'<b>Как отобраны.</b> Внутри каждого сегмента с невыполнением плана '
        f'({C.esc(", ".join(bad_segs)) or "—"}) организации берутся по убыванию '
        f'эффекта, пока отклонение сегмента не закрыто. Переключатель «Цель» '
        f'вверху меняет задачу отбора: выполнить план или перевыполнить его.',
        (f'<b>Почему строк меньше, чем кандидатов.</b> Организации с эффектом '
         f'мельче {C.fmt_num(min_fl)} чел в файл не попадают — их тысячи, и отчёт '
         f'стал бы неподъёмным. На сам отбор порог не влияет: заголовок '
         f'«{sim["k"]} организаций закрывают план» и числа в карточках '
         f'{C.esc(a.unit_label)} считаются по полному набору. Нужных под план '
         f'среди скрытых — {C.fmt_num(n_hidden_plan)}.' if cut else ""),
        (f'<b>Рычаг «Вернуть» — {n_ret} '
         f'{_plural(n_ret, "организация", "организации", "организаций")}.</b> Люди ушли и не '
         f'вернулись, а отток в месяц ухода не отработан. Остальные строки — '
         f'«Привлечь»: организации, которых в портфеле ещё нет.' if n_ret else ""),
        '<b>Фильтры.</b> Рычаг, ГОСБ и сегмент выбираются в шапке таблицы, поиск '
        'идёт по названию, номеру организации, сотруднику и причине.',
        title="Как собран список")
    head = (f'<h3>С кем работать — {sim["k"]} организаций закрывают план</h3>'
            f'<p class="sub" style="font-size:14px;margin:-4px 0 10px">'
            f'Организации, за счёт которых закрывается отклонение от плана. '
            f'{cand}.</p>{help_html}')
    return C.section("Потенциал организаций", C.card(head + explorer) + _ai(ai),
                     eyebrow="Потенциал организаций")


def _outflow_section(a: analyze.Analysis, ai: str | None = None) -> str:
    """Раздел «Отток»: два списка по результату отработки.

    Остальной отчёт смотрит вперёд — кого брать в работу, чтобы закрыть план. Этот
    раздел смотрит назад: люди уже ушли, и вопрос в том, чем закончилась работа по
    ним. Отсюда и деление на два списка:

      «обещали вернуться, но не вернулись» — договорённость зафиксирована в задаче,
      а возврата нет: есть предмет разговора с клиентом и с исполнителем;
      «отработали, но без результата» — работа велась, обещаний не давали, не
      вернулся никто: вопрос не к исполнению, а к самому подходу.

    Порядок колонок повторяет вопрос: кто ушёл → сколько → когда → чем закончилось.
    """
    o = a.outflow or {}
    if not o or not o.get("rows"):
        return ""
    unit = a.unit_label
    ret_pct = (o["tot_ret"] / o["tot_gone"] * 100) if o["tot_gone"] else 0
    head = (
        f'<h3>Крупнейшие потери портфеля и чем закончилась работа по ним</h3>'
        f'<p class="sub" style="font-size:15px;margin:-4px 0 10px">'
        f'За {C.esc((a.dates or {}).get("out_label", "три закрытых месяца"))} отток '
        f'составил <b>{C.fmt_num(o["tot_gone"])}</b> чел по '
        f'{C.fmt_num(o["n_all"])} организациям, возврат — '
        f'{C.fmt_num(o["tot_ret"])} ({ret_pct:.0f}%). Безвозвратные потери: '
        f'<b>{C.fmt_num(o["tot_kept"])}</b> чел.</p>'
        + _gd_help(
            '<b>Что за списки.</b> Оба — про один и тот же отток за три закрытых '
            'месяца, но отвечают на разные вопросы. «Обещали вернуться» — там, где '
            'договорённость о возврате была зафиксирована, а возврата нет. '
            '«Отработали без результата» — там, где по клиенту велась работа в месяцы '
            'ухода, обещаний не давали и не вернулся никто. Сколько задач было и '
            'сколько из них именно по оттоку, видно в строке таблицы.',
            '<b>Откуда известно про обещание.</b> Из задачи по оттоку: в её '
            'чек-листе есть пункты «Ожидаемый возврат получателей» и «Ожидаемый '
            'месяц возврата», а если чек-лист не заполнен — из прямой формулировки '
            'в комментарии сотрудника («сотрудники вернутся в августе»). Ответы '
            '«нет» и «пункт не актуален» обещанием не считаются.',
            '<b>Когда обещание считается невыполненным.</b> Названо число — '
            'вернулось меньше обещанного. Названо без числа — не вернулся никто. '
            'Возврат берётся из витрины возвратов, а не со слов.',
            '<b>Почему клиент может не попасть ни в один список.</b> Часть людей '
            'вернулась — результат есть, пусть и неполный; либо месяц ухода старше '
            'окна, за которое мы видим работу по клиентам, и сказать о ней нечего.',
            '<b>Причина ухода.</b> Берётся из пункта «Причина оттока» в чек-листе '
            'задачи — то есть так, как её назвал сотрудник, — а если чек-лист не '
            'заполнен, распознаётся по формулировке в комментарии. Свод по причинам '
            'считается по всем организациям уровня, поэтому под ним стоит покрытие: '
            'у скольких организаций причина вообще зафиксирована.',
            title="Как собраны эти списки")
    )
    # Три плашки одного переключателя: сначала общая картина («из-за чего уходят»),
    # затем два разреза по результату работы. Свод стоит первым и открыт по
    # умолчанию: он отвечает на вопрос раздела целиком, а списки — точечно.
    groups = []
    summary = _outflow_reasons(o)
    if summary:
        groups.append({"title": "Общий свод по причинам оттока",
                       "sub": _reasons_tab_sub(o), "html": summary})
    for rows_src, title, lead, kind in (
        (o.get("top_promised") or [], "Обещали вернуться, но не вернулись",
         "Договорённость о возврате зафиксирована в задаче по оттоку, а люди "
         "не вернулись. Предмет разговора — и с клиентом, и с исполнителем.", "promise"),
        (o.get("top_worked") or [], "Отработали, но без результата",
         "Работа в месяцы ухода велась, возврат никто не обещал, и не вернулся "
         "никто. Вопрос к подходу, а не к исполнению договорённости.", "worked"),
    ):
        if not rows_src:
            continue
        n, kept = len(rows_src), sum(r["kept"] for r in rows_src)
        groups.append({"title": title,
                       "sub": (f'{n} {_plural(n, "клиент", "клиента", "клиентов")} · '
                               f'потери {C.fmt_num(kept)} чел'),
                       "html": _outflow_group(rows_src, unit, title, lead, kind,
                                              (a.dates or {}).get("label", ""))})
    return C.section(
        "Отток", C.card(head + _outflow_tabs(groups)) + _ai(ai),
        eyebrow="Безвозвратные потери портфеля",
        desc="Кого потеряли за три закрытых месяца и чем закончилась работа по "
             "ним: отдельно невыполненные обещания вернуть получателей, отдельно "
             "отработка, которая результата не дала.")


def _outflow_tabs(groups: list) -> str:
    """Плашки раздела «Отток» — переключателем, а не одним свитком.

    Столбиком вторая таблица оказывалась за экраном первой на пятнадцать строк, и
    до неё просто не долистывали. Переключатель держит все заголовки с их итогами
    на виду: видно, из чего состоит раздел, ещё до чтения таблиц.

    Скрывает лишние панели САМ СКРИПТ (см. _OF_JS), в разметке открыты все. Без JS
    страница вернётся к прежнему виду — таблицы подряд, — а не потеряет две трети
    раздела. По той же причине печать показывает все панели: см. ux-fix.css.
    """
    if not groups:
        return ""
    panes = "".join(f'<div class="of-pane" role="tabpanel">{g["html"]}</div>'
                    for g in groups)
    if len(groups) < 2:
        return f'<div class="of-wrap">{panes}</div>'
    tabs = "".join(
        f'<button type="button" class="of-tab{" on" if i == 0 else ""}" role="tab" '
        f'aria-selected="{"true" if i == 0 else "false"}" onclick="ofTab(this,{i})">'
        f'<span class="of-tab-t">{C.esc(g["title"])}</span>'
        f'<span class="of-tab-n">{C.esc(g["sub"])}</span></button>'
        for i, g in enumerate(groups))
    lead = ('<p class="sub" style="font-size:14px;margin:0 0 10px">'
            'Раздел собран из трёх плашек — выберите нужную.</p>'
            if len(groups) > 2 else
            '<p class="sub" style="font-size:14px;margin:0 0 10px">'
            'Потери разнесены по двум спискам — выберите нужный.</p>')
    return (f'<div class="of-wrap">{lead}'
            f'<div class="of-tabs" role="tablist">{tabs}</div>{panes}</div>')


def _outflow_reasons(o: dict) -> str:
    """Свод по причинам ухода: из-за чего именно потеряли портфель.

    Первая плашка раздела: отвечает на первый вопрос к нему — «из-за чего уходят»,
    — и считается по ВСЕМ организациям уровня, а не по пятнадцати показанным в
    списках. Рядом с итогом всегда стоит покрытие: причина известна не у всех, и
    без этой оговорки долю «смены банка» примут за долю от всех потерь.

    Колонки групп («обещали вернуться», «отработали без результата») делают свод
    разрезом самих списков: видно не только, из-за чего уходят, но и чем при этой
    причине заканчивается работа.
    """
    rows_src = o.get("reasons") or []
    if not rows_src:
        return ""
    known_kept = float(o.get("reason_known_kept") or 0)
    cov = (known_kept / o["tot_kept"] * 100) if o.get("tot_kept") else 0
    rows = []
    for g in rows_src:
        rows.append([
            C.esc(g["reason"]),
            C.fmt_num(g["n"]),
            f'<b style="color:var(--bad)">−{C.fmt_num(g["kept"])}</b>',
            f'{g["share"] * 100:.0f}%',
            (f'{g["n_promise"]} орг · −{C.fmt_num(g["kept_promise"])}'
             if g["n_promise"] else "—"),
            (f'{g["n_worked"]} орг · −{C.fmt_num(g["kept_worked"])}'
             if g["n_worked"] else "—"),
        ])
    note = (f'<p class="g-act" style="margin:0 0 10px">'
            f'Причина ухода зафиксирована в задачах у '
            f'<b>{C.fmt_num(o.get("reason_known_n", 0))}</b> из '
            f'{C.fmt_num(o.get("n_all", 0))} организаций — это <b>{cov:.0f}%</b> '
            f'безвозвратных потерь уровня. Доли в таблице считаются от этой части, '
            f'а не от всех потерь.</p>')
    return ('<h4 style="margin-top:22px">Из-за чего уходят — свод по причинам</h4>'
            + note
            + C.table(["Причина ухода", "организаций", "потери, чел", "доля потерь",
                       "обещали вернуться", "отработали без результата"],
                      rows, num_cols=[1, 2, 3]))


def _reasons_tab_sub(o: dict) -> str:
    """Подпись плашки свода: сколько причин и какую долю потерь они объясняют."""
    rows = o.get("reasons") or []
    cov = (float(o.get("reason_known_kept") or 0) / o["tot_kept"] * 100
           if o.get("tot_kept") else 0)
    n = len([g for g in rows if not g.get("tail")])
    return (f'{n} {_plural(n, "причина", "причины", "причин")} · объясняют '
            f'{cov:.0f}% потерь')


def _promise_cell(r: dict, cur_label: str) -> str:
    """Что именно обещали по этому клиенту и сдвинулся ли срок.

    Число обещанных получателей стоит первым: с ним разговор предметный. Срок
    помечается как прошедший, если названный месяц уже закрыт, — это и есть повод
    вернуться к клиенту сейчас, а не «когда-нибудь».
    """
    p = r.get("promise") or {}
    parts = []
    qty = p.get("qty")
    if qty:
        parts.append(f'обещали вернуть <b>{C.fmt_num(qty)}</b> чел')
    else:
        parts.append("обещали вернуть получателей")
    month = str(p.get("month") or "")
    if month:
        past = _month_passed(month, cur_label)
        col = "var(--bad)" if past else "var(--text-2)"
        parts.append(f'<span style="color:{col}">срок {C.esc(month)}'
                     f'{" — прошёл" if past else ""}</span>')
    if r.get("ret"):
        parts.append(f'вернулись {C.fmt_num(r["ret"])}')
    src = str(p.get("src") or "")
    if src:
        parts.append(f'<span style="color:var(--text-2)">{C.esc(src)}</span>')
    return " · ".join(parts)


def _month_passed(month: str, cur_label: str) -> bool:
    """Месяц «MM.YYYY» уже закончился относительно прогнозного месяца отчёта."""
    def key(m):
        try:
            mm, yy = str(m).split(".")
            return (int(yy), int(mm))
        except (ValueError, AttributeError):
            return None
    a, b = key(month), key(cur_label)
    return bool(a and b and a < b)


def _outflow_group(rows_src: list, unit: str, title: str, lead: str,
                   kind: str = "worked", cur_label: str = "") -> str:
    """Одна группа раздела «Отток»: заголовок, пояснение и таблица клиентов.

    Последняя колонка зависит от группы и отвечает на её собственный вопрос: в
    группе обещаний — что именно обещали и когда, в группе отработки — сколько
    было задач и чем они закончились. Одна общая колонка на оба списка была бы
    наполовину пустой в каждом.

    Вывода по комментариям в таблице нет: он занимал половину ширины строки текстом
    разной длины, из-за чего числа — а таблица про них — расползались по вертикали.
    Разбор комментариев остался там, где его читают целиком: в тексте под разделом
    и в карточке ГОСБ у каждой организации.
    """
    if not rows_src:
        return ""
    has_emp = any(r["emp"] for r in rows_src)
    is_promise = kind == "promise"
    has_work = is_promise or any(r["tasks"] for r in rows_src)
    tot = sum(r["kept"] for r in rows_src)
    rows = []
    for r in rows_src:
        when = ", ".join(r["months"][:3]) + (f" и ещё {len(r['months']) - 3}"
                                             if len(r["months"]) > 3 else "")
        work = ""
        if is_promise:
            work = _promise_cell(r, cur_label)
        elif has_work:
            if r["worked"]:
                work = (f'<span style="color:var(--good)">задача закрыта</span> · '
                        f'всего {r["tasks"]}, по оттоку {r["out_tasks"]}')
            else:
                work = (f'<span style="color:var(--warn)">в работе, результат '
                        f'не достигнут</span> · всего {r["tasks"]}')
        # причина ухода — второй строкой в той же колонке: рядом с тем, чем
        # закончилась работа, она и читается. Отдельной колонкой таблица из шести
        # столбцов стала бы семистолбцовой и поехала бы по ширине
        if r.get("out_reason"):
            work += (f'<div class="gd-emp">причина: {C.esc(r["out_reason"])}</div>'
                     if work else
                     f'<span class="gd-emp">причина: {C.esc(r["out_reason"])}</span>')
        # закрепление живёт на грейне (ГОСБ, организация): на уровне банка его
        # в строке нет, и писать «закрепления нет» было бы неправдой — там просто
        # другой разрез. Отсутствие закрепления называем только там, где оно видно
        if r["emp"]:
            who = f' · {C.esc(r["emp"])}'
        elif has_emp:
            who = ' · <span style="color:var(--warn)">закрепления нет</span>'
        else:
            who = ""
        row = [
            f'{C.esc(r["name"])}<div class="gd-emp">{C.esc(r["unit"])}{who}</div>',
            C.fmt_num(r["gone"]),
            C.fmt_num(r["ret"]) if r["ret"] else "—",
            f'<b style="color:var(--bad)">−{C.fmt_num(r["kept"])}</b>',
            C.esc(when) or "—",
        ]
        if has_work:
            row.append(work)
        rows.append(row)
    cols = [f"Организация · {unit}" + (" · ответственный" if has_emp else ""),
            "отток", "возврат", "потери", "период оттока"]
    if has_work:
        cols.append("что обещали" if is_promise else "отработка")
    return (
        f'<h4 style="margin-top:22px">{C.esc(title)} — {len(rows)} клиентов на '
        f'{C.fmt_num(tot)} чел</h4>'
        f'<p class="g-act" style="margin:0 0 10px">{C.esc(lead)}</p>'
        + C.table(cols, rows, num_cols=[1, 2, 3])
    )


def _log_llm_stats(a: analyze.Analysis) -> None:
    s = a.llm_stats or {}
    if not s:
        return
    capped = s.get("capped", 0)
    tail = (f" · не влезло в бюджет ({s.get('max_calls')} выз.) → правила: {capped}"
            if capped else "")
    progress.done(
        f"Аудит отработки: пул {s.get('pool',0)} пар (эффект ≥ {s.get('min_impact',0):g}) · "
        f"чек-лист {s.get('checklist',0)} · ключевые слова {s.get('keyword',0)} · "
        f"без текста {s.get('no_text',0)} · LLM {s.get('llm',0)} "
        f"(батчей {s.get('batches',0)} по {s.get('batch')}) · фолбэк {s.get('fallback',0)}{tail}"
    )
    progress.done(
        f"Из них не требуют действий сейчас: влиять нечем {s.get('no_influence',0)} · "
        f"назван будущий срок {s.get('deadline',0)}"
    )


def _ai(text: str | None) -> str:
    """Вывод LLM карточкой в конце своего раздела.

    Раньше все выводы жили одним блоком «Что делать — резюме» в конце страницы, и
    читателю приходилось возвращаться к цифрам. Теперь вывод стоит там, где стоят
    данные, к которым он относится.
    """
    if not text or not str(text).strip():
        return ""
    return C.card('<div class="ai-head">Вывод</div>'
                  + C.narrative_html(str(text)), cls="ai")


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Склонение существительного при числе: 21 организация, 22 организации,
    25 организаций. Отчёт читает правление — «21 организаций» в нём быть не должно."""
    a, b = abs(int(n)) % 100, abs(int(n)) % 10
    if 10 < a < 20:
        return many
    if b == 1:
        return one
    if 1 < b < 5:
        return few
    return many


def _col(exec_pct):
    return {"good": "var(--good)", "warn": "var(--warn)", "bad": "var(--bad)"}[C.status_of(exec_pct)]


def _pct(exec_pct) -> str:
    """Выполнение плана строкой — вместе с `_col` и `C.status_of`.

    В приграничных долях процента печатаем десятую. Округление до целого
    перебрасывает число через границу цвета: 99.6% превращались в «100%» рядом с
    жёлтым, 94.7% — в «95%» рядом с красным, и цвет выглядел ошибкой расчёта.
    Так же давно устроены ячейки матрицы и бейджи сегментов.
    """
    p = (exec_pct or 0) * 100
    if (99.5 <= p < 100) or (94.5 <= p < 95):
        # округление отбрасываем вниз: 99.96% округлились бы в «100.0%» и снова
        # встали бы рядом с жёлтым, ради чего вся эта ветка и написана
        return f"{math.floor(p * 10) / 10:.1f}%"
    return f"{p:.0f}%"
