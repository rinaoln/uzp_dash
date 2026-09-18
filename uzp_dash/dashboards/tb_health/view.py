"""Сборка apple-страницы дэша tb_health.

Отчёт двухуровневый и живёт в одном файле: вкладка СБ (единица разбора — ТБ) и по
вкладке на каждый ТБ (единица — ГОСБ). Структура уровня одна и та же: вердикт области,
из чего сложился прогноз, матрица «единица × сегмент», карточки единиц с оверлеем
разбора и — только на уровне ТБ — интерактивный список организаций к работе.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ...registry import Context, dashboard
from ...render import components as C
from ...render import page
from ... import progress
from . import analyze, bank, prompts, segments

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
TITLE = "Прогноз портфеля, причины невыполнения"

# Легенда под шапкой — из дизайна пользователя дословно
_LEGEND = (
    '<div class="legend">\n'
    '<span class="lg-item"><span class="lg-dot good"></span><b>план выполняется</b></span>\n'
    '<span class="lg-item"><span class="lg-dot warn"></span><b>план выполняется, но есть '
    'слабый сегмент</b></span>\n'
    '<span class="lg-item"><span class="lg-dot bad"></span><b>план не выполняется</b></span>\n'
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

    levels_tb = [(int(r.tb_id), str(r.tb_short_name), str(r.tb_full_name))
                 for r in b.tbs.itertuples()]
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
    return page(
        title=TITLE,
        subtitle=f"Прогноз на {C.esc(d.get('label', sb.ref_date))}",
        body=(_BOOT_JS + _LEGEND + _tabs([a for a, _ in levels]) + bodies
              + _GD_JS + _LVL_JS + _OF_JS),
        # ux-fix.css — наши правки поверх дизайна (сам ux.css остаётся копией макета)
        css=_asset("ux.css") + _asset("ux-fix.css") + _asset("help.css"),
        # скрипт дизайна — ПОСЛЕ .wrap: он переносит её содержимое в новую раскладку и
        # оборачивает gdOpen, поэтому идёт после всех остальных скриптов
        tail=(f'<script>{_asset("ux.js.txt")}</script>\n'
              # раскладка готова — снимаем маску, поставленную _BOOT_JS
              f'{_REVEAL_JS}\n'
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
        + _matrix(a, story.get("matrix"))
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
    word = {"good": "План выполняется", "warn": "План под угрозой",
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
        f'<div class="verdict">{C.esc(word)} · '
        f'<span class="big">{(r["exec"] or 0)*100:.0f}%</span></div>'
        # Обе главные строки плашки — получатели и ФОТ — несут класс row2-fot: это
        # крупное начертание строки показателя. Класс проставлен в разметке, а не
        # оставлен на ux.js: скрипт вешает его на ПЕРВУЮ строку .row2 плашки, считая
        # её строкой ФОТ (в макете она стояла первой). У нас первая — получатели, и
        # ФОТ, равный ей по смыслу, оставался мелкой подписью.
        f'<div class="row2 row2-fot">Получатели: прогноз '
        f'<b>{C.fmt_num(r["fact"])}</b> '
        f'при плане {C.fmt_num(r["plan"])} · '
        f'{_delta_html(r["fact"] - r["plan"], "чел")}</div>'
        # при выполненном плане разрыва нет, и «−0 получателей до плана» рядом со
        # строкой «+N к плану» читается как ошибка отчёта
        + ('<div>' + C.badge("−" + C.fmt_num(a.gap_rcp) + " получателей до плана", st)
           + '</div>' if a.gap_rcp >= 0.5 else
           '<div>' + C.badge("план закрыт по получателям", st) + '</div>')
        + C.meter(r["exec"])
        + f'<div class="row2 row2-fot">ФОТ: прогноз '
          f'<b>{C.fmt_num(fot["fact"] / 1e6)}</b> '
          f'при плане {C.fmt_num(fot["plan"] / 1e6)} млн ₽ · '
        # процент набран как остальные числа строки и окрашен по статусу — так же, как
        # выполнение показано в карточках показателей ниже
          f'<b style="color:{_col(fot["exec"])}">{(fot["exec"] or 0)*100:.0f}%</b> · '
          f'{_delta_html((fot["fact"] - fot["plan"]) / 1e6, "млн ₽")}</div>'
        + f'<div class="row2">{closed_txt}</div>'
    )
    return C.card(inner, cls="hero")


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
        head = (f'{C.esc(d.get("closed_label", ""))} закрыт: '
                f'{C.fmt_num(cl["fact"] / scale, unit)} '
                f'({(cl.get("exec") or 0) * 100:.0f}% плана, '
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
            f'<b style="color:{_col(v["exec"])}">{(v["exec"] or 0)*100:.0f}%</b> · '
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
         f'{out_lbl} · uzp_dwh_fact_outflow от {bank.OUT_MIN_QTY} чел '
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
    ex = pf.get("exec") or 0
    st = C.status_of(pf.get("exec"))
    total = (
        f'<div class="g-do">Прогноз витрины на {C.esc(d.get("label", ""))}: '
        f'<b>{C.fmt_num(pf["forecast"])}</b> при плане {C.fmt_num(pf["plan"])} → '
        + C.badge(f"{ex * 100:.0f}% плана", st) + '</div>'
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


def _matrix(a: analyze.Analysis, ai: str | None = None) -> str:
    m = a.matrix
    if m.empty:
        return ""
    gg = a.gosb_gap.sort_values("nedobor", ascending=False)
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
    heat = C.card(
        '<h3>Прогноз выполнения плана по получателям, %</h3>'
        + C.heat_matrix(rows_id_label, segs, cells))
    top = [(f'{r.unit_name} · {r.seg_name}',
            C.badge(f'{r.execution_percent*100:.0f}%', C.status_of(r.execution_percent)),
            C.fmt_num(r.nedobor), f'{r.share*100:.0f}%') for r in a.top_cells.itertuples()]
    # «ГОСБхСегмент» — и на уровне банка тоже: так в дизайне пользователя
    top_tbl = C.card(f'<h3>ТОП {a.unit_label} по невыполнению</h3>'
                     + C.table(["ГОСБ × сегмент", "Выполн.", "Отклонение, чел", "Доля отклонения"],
                               top, num_cols=[2, 3]))
    return C.section(f"Матрица выполнения {a.unit_label}/сегмент",
                     f'<div class="grid cols-2">{heat}{top_tbl}</div>' + _ai(ai),
                     eyebrow="Диагностика по прогнозу")


def _gap_cell(v: float) -> str:
    """Ячейка недобора. План выполнен (недобор ≤ 0) — ставим «—», а не «−0»."""
    return "—" if v <= 0.5 else "−" + C.fmt_num(v)


def _seg_badge(s: dict) -> str:
    """Бейдж сегмента: имя + выполнение, цвет по статусу — состояние не кодируется
    одним лишь цветом. При 99.5–99.9% показываем десятую долю, иначе рядом с
    недобором стояло бы «100%»."""
    fmt = "%s %.1f%%" if s["exec"] >= 0.995 else "%s %.0f%%"
    return C.badge(fmt % (s["seg"], s["exec"] * 100), C.status_of(s["exec"]))


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


def _gd_help(*lines: str) -> str:
    """Подсказка «как считаем» под заголовком блока карточки.

    Нативный `<details>`: закрыта по умолчанию, открывается кликом и с клавиатуры,
    работает без скрипта и раскрывается сама при печати. Читателю карточки объяснение
    нужно один раз, поэтому строкой, а не постоянным текстом на пол-экрана.

    Язык подсказки — про смысл числа, а не про источник: названия таблиц и полей
    управляющему ничего не говорят и только удлиняют текст.
    """
    body = "".join(f"<p>{x}</p>" for x in lines)
    return ('<details class="gd-help"><summary>Как считаем этот блок</summary>'
            f'<div class="gd-help-b">{body}</div></details>')


def _gosb_dialog(c: dict, det: dict, d: dict, uid: str, unit_label: str = "ГОСБ") -> str:
    """Оверлей «почему прогноз такой» по одному ГОСБ.

    Порядок блоков: портфель → отток по группам → пайплайн → тренд портфеля →
    разбор по сегментам. Отток разложен на группы (см. `_out_group`) и покрыт целиком;
    в остальных блоках имена показываются только материальные, поэтому у них стоит
    подпись о покрытии.
    """
    if not det:
        return ""
    pf = det["pf"]
    ex = pf.get("exec") or 0
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
        f'{ex*100:.0f}%</b> · {_delta_html(pf["forecast"] - pf["plan"], "чел")}'
        f'</div></div>'
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
        st = ("good" if c["healthy"] else
              "warn" if c["seg_only"] else
              "bad" if c["exec"] < 0.9 else "warn")
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
        more = ('<div class="g-more">Почему такой прогноз →</div>' if det else "")
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
            f'<span class="g-ex {st}"><i>прогноз</i>{c["exec"]*100:.0f}%</span></div>'
            + C.meter(c["exec"])
            + f'<div class="g-fc">{C.fmt_num(c["forecast"])} из '
              f'{C.fmt_num(c["plan"])} · {_delta_html(c["forecast"] - c["plan"], "чел")}'
              f'</div>'
            + f'<div style="margin:2px 0 6px">{head_badge}</div>'
            + f'{seg_html}{do}{more}{drill}'
        )
        # карточка кликабельна целиком; role/tabindex — чтобы работала и с клавиатуры
        attrs = (f' role="button" tabindex="0" onclick="gdOpen(\'{uid}\')" '
                 f'onkeydown="if(event.key===\'Enter\'||event.key===\' \')'
                 f'{{event.preventDefault();gdOpen(\'{uid}\');}}"' if det else "")
        cards.append(f'<div class="card gcard {st}"{attrs}>{inner}</div>')
        if det:
            dialogs.append(_gosb_dialog(c, det, d, uid, unit))
    grid = f'<div class="gcards">{"".join(cards)}</div>{"".join(dialogs)}'
    return C.section(f"Детализация по {unit}", grid + _ai(ai),
                     eyebrow=f"Детализация по {unit} · клик открывает разбор до организаций")


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
  mainClone.querySelectorAll('.gd-close, .mgmt-trigger').forEach(function(el){ el.remove(); });
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
    + '.gd-export, .gd-close, .gosb-finder, .ts-dropdown').forEach(function(el){
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
    gosb_opts = sorted({row["gosb"] for row in rows})
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
    hold = (f' · из них «Вернуть» — <b>{n_ret}</b>: люди ушли и не вернулись, а отток '
            f'в месяц ухода не отработан' if n_ret else "")
    # хвост не прячем молча: сказано, сколько кандидатов есть всего, по какому правилу
    # часть из них в файл не попала и сколько среди скрытых нужных под план — иначе
    # заголовок «N закрывают план» выглядел бы расходящимся с таблицей без объяснения
    plan_hidden = (f' · из них нужных под план — {C.fmt_num(n_hidden_plan)}: '
                   f'на отбор и на карточки ГОСБ порог не влияет' if n_hidden_plan else "")
    cand = (f'в списке {C.fmt_num(len(rows))} из {C.fmt_num(n_all)} кандидатов — '
            f'остальные мельче {C.fmt_num(min_fl)} чел{plan_hidden}' if cut
            else f'всего кандидатов {C.fmt_num(len(rows))}')
    head = (f'<h3>С кем работать — {sim["k"]} организаций закрывают план</h3>'
            f'<p class="sub" style="font-size:14px;margin:-4px 0 14px">'
            f'отбор идёт внутри сегментов с невыполнением плана каждого ГОСБ '
            f'({C.esc(", ".join(bad_segs)) or "—"}), по величине эффекта, пока отклонение '
            f'сегмента не закрыто · переключатель «Цель» задаёт перевыполнение · '
            f'{cand}{hold}</p>')
    return C.section("Потенциал организаций", C.card(head + explorer) + _ai(ai),
                     eyebrow="Потенциал организаций")


def _outflow_section(a: analyze.Analysis, ai: str | None = None) -> str:
    """Раздел «Отток»: где потеряли больше всего и что там делали.

    Остальной отчёт смотрит вперёд — кого брать в работу, чтобы закрыть план. Этот
    раздел смотрит назад: люди уже ушли, и вопрос в том, была ли по ним работа. Одно
    без другого не читается — по половине крупнейших потерь задач не заводили вовсе,
    и видно это только рядом с именами.

    Порядок колонок повторяет вопрос: кто ушёл → сколько → когда → кто вёл →
    что делали.
    """
    o = a.outflow or {}
    if not o or not o.get("rows"):
        return ""
    unit = a.unit_label
    ret_pct = (o["tot_ret"] / o["tot_gone"] * 100) if o["tot_gone"] else 0
    head = (
        f'<h3>Крупнейшие потери портфеля и их отработка</h3>'
        f'<p class="sub" style="font-size:15px;margin:-4px 0 14px">'
        f'За {C.esc((a.dates or {}).get("out_label", "три закрытых месяца"))} отток '
        f'составил <b>{C.fmt_num(o["tot_gone"])}</b> чел по '
        f'{C.fmt_num(o["n_all"])} организациям, возврат — '
        f'{C.fmt_num(o["tot_ret"])} ({ret_pct:.0f}%). Безвозвратные потери: '
        f'<b>{C.fmt_num(o["tot_kept"])}</b> чел.</p>'
    )
    groups = []
    for rows_src, title, lead in (
        (o.get("top_worked") or [], "Отработка проведена, возврат не состоялся",
         "Задачи по этим клиентам заводились, люди в портфель не вернулись. "
         "Разбор нужен по существу работы, а не по факту её наличия."),
        (o.get("top_silent") or [], "Отток на контроль",
         "Крупнейшие потери, по которым отработка в воронке не отражена. "
         "Требуют решения по дальнейшим действиям."),
    ):
        if not rows_src:
            continue
        groups.append({"title": title,
                       "n": len(rows_src),
                       "kept": sum(r["kept"] for r in rows_src),
                       "html": _outflow_group(rows_src, unit, title, lead)})
    return C.section(
        "Отток", C.card(head + _outflow_tabs(groups)) + _ai(ai),
        eyebrow="Безвозвратные потери портфеля",
        desc="Кого потеряли за три закрытых месяца и что по этим клиентам "
             "делали — чтобы отделить случаи, где работа велась и не дала "
             "результата, от тех, где нужно принимать решение.")


def _outflow_tabs(groups: list) -> str:
    """Две группы оттока — переключателем, а не одним свитком.

    Списком в столбик вторая группа оказывалась за экраном первой таблицы на
    пятнадцать строк, и до неё просто не долистывали. Переключатель держит оба
    заголовка с их итогами на виду: видно, что списка два, ещё до чтения таблицы.

    Скрывает лишнюю панель САМ СКРИПТ (см. _OF_JS), в разметке открыты обе. Без JS
    страница вернётся к прежнему виду — два списка подряд, — а не потеряет половину
    раздела. По той же причине печать показывает обе панели: см. ux-fix.css.
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
        f'<span class="of-tab-n">{g["n"]} клиентов · потери '
        f'{C.fmt_num(g["kept"])} чел</span></button>'
        for i, g in enumerate(groups))
    lead = ('<p class="sub" style="font-size:14px;margin:0 0 10px">'
            'Потери разнесены по двум спискам — выберите нужный.</p>')
    return (f'<div class="of-wrap">{lead}'
            f'<div class="of-tabs" role="tablist">{tabs}</div>{panes}</div>')


def _outflow_group(rows_src: list, unit: str, title: str, lead: str) -> str:
    """Одна группа раздела «Отток»: заголовок, пояснение и таблица клиентов.

    Колонка отработки показывается только там, где есть что показать. У группы без
    задач она была бы колонкой из одинаковых прочерков и говорила бы ровно то, что
    в этом отчёте проговаривать не нужно: состав группы и так задан её заголовком.

    Вывода по комментариям в таблице нет: он занимал половину ширины строки текстом
    разной длины, из-за чего числа — а таблица про них — расползались по вертикали.
    Разбор комментариев остался там, где его читают целиком: в тексте под разделом
    и в карточке ГОСБ у каждой организации.
    """
    if not rows_src:
        return ""
    has_emp = any(r["emp"] for r in rows_src)
    has_work = any(r["tasks"] for r in rows_src)
    tot = sum(r["kept"] for r in rows_src)
    rows = []
    for r in rows_src:
        when = ", ".join(r["months"][:3]) + (f" и ещё {len(r['months']) - 3}"
                                             if len(r["months"]) > 3 else "")
        work = ""
        if has_work:
            if r["worked"]:
                work = (f'<span style="color:var(--good)">задача закрыта</span> · '
                        f'всего {r["tasks"]}, по оттоку {r["out_tasks"]}')
            else:
                work = (f'<span style="color:var(--warn)">в работе, результат '
                        f'не достигнут</span> · всего {r["tasks"]}')
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
        cols.append("отработка")
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


def _col(exec_pct):
    return {"good": "var(--good)", "warn": "var(--warn)", "bad": "var(--bad)"}[C.status_of(exec_pct)]
