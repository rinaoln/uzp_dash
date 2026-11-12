"""Снимок чисел отчёта — под сравнение «неделя к неделе».

ПОЧЕМУ СНИМОК, А НЕ ЗАПРОС. Витрина метрик хранит только МЕСЯЦЫ: period_type
принимает значения m / q / qtd / y / ytd, недельного грейна в ней нет, и строки
за «неделю назад» не существует. Прогноз при этом пересчитывается постоянно, и
разница между тем, что витрина показывала неделю назад и показывает сейчас, —
это разница между ДВУМЯ СБОРКАМИ отчёта. Поэтому каждая сборка кладёт рядом с
HTML свой снимок, а следующая читает самый подходящий из предыдущих.

Снимок маленький (план, прогноз и выполнение по уровню и по каждой единице) и
лежит в output/snapshots. Отчёт от него не зависит: нет снимков — просто нет
строки сравнения, всё остальное собирается как обычно.

Сравниваются только сборки на ОДИН И ТОТ ЖЕ прогнозный месяц. Прогноз на август
и прогноз на июль — разные величины, и вычитать их друг из друга нельзя.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ... import progress

DIR_NAME = "snapshots"
# Сколько дней назад ищем базовую сборку. Отчёт собирают чаще раза в неделю,
# поэтому берём самый свежий снимок, которому уже есть столько дней, — иначе
# «неделя к неделе» считалась бы к утренней сборке того же дня.
BASE_MIN_AGE_DAYS = 5
# Старые снимки не копим: сравнение смотрит на неделю назад, а не на историю.
KEEP_LAST = 30


def _dir(output_dir) -> Path:
    return Path(output_dir) / DIR_NAME


def save(output_dir, ref_cur: str, levels: list) -> Path | None:
    """Записать снимок текущей сборки. `levels` — список объектов Analysis."""
    data = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "ref_cur": str(ref_cur),
        # ЧТО ИМЕННО ЛЕЖИТ В СНИМКЕ. Сравнение неделя к неделе идёт по ПРОГНОЗУ:
        # и в этой сборке, и в базовой берётся prediction_amt витрины на один и
        # тот же месяц. Факт закрытого месяца в снимок не попадает намеренно — он
        # за неделю не меняется, и дельта по нему всегда была бы нулевой.
        "source": "uzp_dwh_metrics.prediction_amt",
        "levels": {str(a.tb_id): _level_snap(a) for a in levels},
    }
    d = _dir(output_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"tb_health_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        # снимок — служебный файл, из-за него отчёт падать не должен
        progress.done(f"Снимок сборки не сохранён ({e}) — сравнение неделя "
                      f"к неделе в следующем отчёте не построится")
        return None
    _rotate(d)
    progress.done(f"Снимок сборки сохранён: {path.name} — от него следующий отчёт "
                  f"посчитает динамику неделя к неделе")
    return path


def _level_snap(a) -> dict:
    v = a.verdict or {}
    return {
        "name": a.tb_short,
        "rcp": _mk(v.get("rcp", {})),
        "fot": _mk(v.get("fot", {})),
        "units": {str(c["gosb_id"]): {"fc": float(c["forecast"]),
                                      "plan": float(c["plan"]),
                                      "exec": float(c["exec"] or 0)}
                  for c in (a.gosb_cards or [])},
    }


def _mk(v: dict) -> dict:
    """Числа уровня для снимка.

    `fact` в вердикте уровня — это ПРОГНОЗ витрины на отчётный месяц (см.
    analyze._portfolio: verdict["rcp"]["fact"] = prediction_amt), поэтому в
    снимке оно и называется `fc`. Ключ `src` пишется рядом со значением, чтобы
    происхождение числа было видно в самом файле снимка.
    """
    return {"fc": float(v.get("fact") or 0), "plan": float(v.get("plan") or 0),
            "exec": float(v.get("exec") or 0), "src": "prediction_amt"}


def _rotate(d: Path) -> None:
    files = sorted(d.glob("tb_health_*.json"))
    for f in files[:-KEEP_LAST]:
        try:
            f.unlink()
        except OSError:
            pass


def load_base(output_dir, ref_cur: str) -> dict | None:
    """Базовая сборка для сравнения: самая свежая из достаточно старых.

    Правило отбора: тот же прогнозный месяц, ДРУГОЙ календарный день и возраст
    не меньше BASE_MIN_AGE_DAYS. Если снимков нужного возраста нет, берём самый
    свежий из оставшихся: сравнить с позавчерашней сборкой и честно подписать её
    дату полезнее, чем не показать динамику вовсе.

    Сборки ТОГО ЖЕ ДНЯ базой не считаются намеренно. Отчёт часто пересобирают
    подряд — после правки текста, после перезапуска, — и сравнение с собственной
    утренней сборкой давало бы строку «без изменений» на каждой карточке: она
    выглядит поломкой, хотя говорит лишь о том, что данные за час не поменялись.
    """
    d = _dir(output_dir)
    if not d.exists():
        return None
    snaps = []
    for f in sorted(d.glob("tb_health_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(data.get("ref_cur")) != str(ref_cur):
            continue
        try:
            built = datetime.fromisoformat(str(data.get("built_at")))
        except ValueError:
            continue
        data["_built"] = built
        snaps.append(data)
    now = datetime.now()
    snaps = [s for s in snaps if s["_built"].date() < now.date()]
    if not snaps:
        return None
    aged = [s for s in snaps if (now - s["_built"]).total_seconds()
            >= BASE_MIN_AGE_DAYS * 86400]
    base = aged[-1] if aged else snaps[-1]
    days = max(0, int((now - base["_built"]).total_seconds() // 86400))
    progress.done(f"Динамика неделя к неделе считается к сборке от "
                  f"{base['_built']:%d.%m.%Y %H:%M} ({days} дн. назад)")
    return base


def compare(base: dict | None, a) -> dict:
    """Что изменилось в этом уровне со времён базовой сборки.

    Возвращает пустой словарь, если сравнивать не с чем: тогда отчёт просто не
    рисует строку динамики, а в плашке прогноза стоит пояснение, почему её нет.
    """
    if not base:
        return {}
    lvl = (base.get("levels") or {}).get(str(a.tb_id))
    if not lvl:
        return {}
    built = base["_built"] if isinstance(base.get("_built"), datetime) else None
    out = {
        "label": f"{built:%d.%m}" if built else "",
        # абсолютные числа базовой сборки — чтобы дельту можно было проверить,
        # не открывая снимок: они уходят в подсказку строки динамики
        "was": {"rcp": float((lvl.get("rcp") or {}).get("fc") or 0),
                "fot": float((lvl.get("fot") or {}).get("fc") or 0)},
        "full": f"{built:%d.%m.%Y}" if built else "",
        "days": (max(0, int((datetime.now() - built).total_seconds() // 86400))
                 if built else None),
        "rcp": _delta(lvl.get("rcp"), (a.verdict or {}).get("rcp")),
        "fot": _delta(lvl.get("fot"), (a.verdict or {}).get("fot")),
        "units": {},
    }
    prev_units = lvl.get("units") or {}
    for c in (a.gosb_cards or []):
        was = prev_units.get(str(c["gosb_id"]))
        if not was:
            continue
        out["units"][c["gosb_id"]] = {
            "was": float(was.get("fc") or 0),
            "fc": float(c["forecast"]) - float(was.get("fc") or 0),
            "plan": float(c["plan"]) - float(was.get("plan") or 0),
            "exec": float(c["exec"] or 0) - float(was.get("exec") or 0),
        }
    return out


def _delta(was: dict | None, now: dict | None) -> dict | None:
    if not was or not now:
        return None
    return {"fc": float(now.get("fact") or 0) - float(was.get("fc") or 0),
            "plan": float(now.get("plan") or 0) - float(was.get("plan") or 0),
            "exec": float(now.get("exec") or 0) - float(was.get("exec") or 0)}
