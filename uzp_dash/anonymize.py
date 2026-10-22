"""Псевдонимы для запросов к LLM.

Зачем: корпоративный шлюз (GigaChat) блокирует запрос и отправляет обращение в
blacklist, если в промпте встречаются чувствительные названия — например «ГО по
Донецкой Народной Республике». Поэтому названия ГОСБ и компаний в LLM не уходят
вовсе: вместо них подставляются нейтральные токены («ГОСБ-01»), а реальные имена
возвращаются в ответ модели уже на нашей стороне.
"""
from __future__ import annotations

import re

# Разделители, которыми модель может «переписать» токен: «ГОСБ-01», «ГОСБ 1», «ГОСБ—01»
_SEP = r"[\s\-–—_]*"

# Чем заменить токен, которому не нашлось имени. Ключи — те же `kind`, что уходят
# в `alias()`; вид, которого здесь нет, получит формулировку по умолчанию.
UNKNOWN = {
    "ГОСБ": "другие подразделения",
    "ТБ": "другие территориальные банки",
    "Организация": "другие организации",
}


def _kind_re(kind: str) -> str:
    """Шаблон вида токена с поправкой на склонение.

    Модель склоняет не только предложение, но и сам токен: «начинать с
    Организации-01» вместо «Организация-01». Точное совпадение такой токен не
    ловит — имя не возвращалось, и псевдоним уезжал в отчёт правления.
    Аббревиатуры («ГОСБ», «ТБ») не склоняются, поэтому их шаблон не меняется.
    """
    if kind[-1:].lower() in "аяеёиоуыэюй":
        return re.escape(kind[:-1]) + r"[а-яё]{0,3}"
    return re.escape(kind)


class Aliases:
    """Двусторонний словарь «настоящее имя ↔ токен» на один вызов LLM."""

    def __init__(self) -> None:
        self._by_name: dict[tuple[str, str], str] = {}
        self._by_token: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self.leaked = 0          # сколько выдуманных токенов пришлось обезличить

    def alias(self, kind: str, name) -> str:
        """Токен для имени. Повторный вызов даёт тот же токен."""
        name = (str(name) if name is not None else "").strip()
        if not name:
            return name
        key = (kind, name)
        if key not in self._by_name:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            token = f"{kind}-{self._counters[kind]:02d}"
            self._by_name[key] = token
            self._by_token[token] = name
        return self._by_name[key]

    def _expand_lists(self, text: str) -> str:
        """Развернуть перечисления вида «ГОСБ-01, 02 и 03» в полные токены.

        Модель часто сокращает повтор префикса; без этого шага восстановилось бы
        только первое имя, а остальные остались бы голыми числами.
        """
        for kind, last in self._counters.items():
            pattern = re.compile(
                rf"({_kind_re(kind)}{_SEP}0*(\d+))((?:\s*(?:,|и)\s*0*\d{{1,2}}(?!\d))+)",
                re.IGNORECASE)

            def repl(m, kind=kind, last=last):
                head, tail = m.group(1), m.group(3)
                out = head
                for part in re.finditer(r"(\s*(?:,|и)\s*)0*(\d{1,2})(?!\d)", tail):
                    sep, num = part.group(1), int(part.group(2))
                    if not 1 <= num <= last:      # это не номер ГОСБ — оставляем как есть
                        return m.group(0)
                    out += f"{sep}{kind}-{num:02d}"
                return out

            text = pattern.sub(repl, text)
        return text

    def restore(self, text: str) -> str:
        """Вернуть настоящие имена в текст ответа LLM.

        Последним шагом — страховка от НЕИЗВЕСТНЫХ токенов. Модель называет и то,
        чего в промпте не было: пишет «ГОСБ-04 и ГОСБ-05», хотя псевдонимов завели
        три. Вернуть такое имя неоткуда, а оставить токен в отчёте нельзя — читатель
        видит «ГОСБ-05» и не понимает, о ком речь. Заменяем нейтральной формулировкой
        и считаем, сколько раз это понадобилось (`leaked`): если счётчик не ноль,
        промпту не хватило псевдонимов, и это видно в прогрессе.
        """
        self.leaked = 0
        if not text:
            return text
        if self._by_token:
            text = self._expand_lists(text)
            # от длинных номеров к коротким: иначе «ГОСБ-1» съест начало «ГОСБ-12»
            for token in sorted(self._by_token, key=len, reverse=True):
                kind, _, num = token.rpartition("-")
                pattern = re.compile(rf"{_kind_re(kind)}{_SEP}0*{int(num)}(?!\d)",
                                     re.IGNORECASE)
                name = self._by_token[token]
                text = pattern.sub(lambda _m, n=name: n, text)  # без спецсимволов замены
        return self._drop_unknown(text)

    def _drop_unknown(self, text: str) -> str:
        """Убрать токены, которых нет в словаре, — модель их выдумала."""
        for kind in self._kinds():
            repl = UNKNOWN.get(kind, f"другие {kind}")
            pattern = re.compile(rf"{_kind_re(kind)}{_SEP}0*\d+(?!\d)",
                                 re.IGNORECASE)
            text, n = pattern.subn(repl, text)
            self.leaked += n
            if n > 1:
                # «ГОСБ-04 и ГОСБ-05» превратилось бы в «другие подразделения и
                # другие подразделения» — схлопываем повтор в одну формулировку
                dup = re.compile(rf"{re.escape(repl)}(?:\s*(?:,|и)\s*{re.escape(repl)})+")
                text = dup.sub(repl, text)
        return text

    def _kinds(self) -> set:
        """Все виды токенов: и заведённые, и те, что модель могла выдумать сама."""
        return set(self._counters) | set(UNKNOWN)

    def __len__(self) -> int:
        return len(self._by_token)


# --------------------------------------------------------------------------- #
# ИМЯ ЧЕЛОВЕКА ВМЕСТО НАЗВАНИЯ ОРГАНИЗАЦИИ.
#
# Индивидуальный предприниматель и глава КФХ названы в справочнике собственным
# именем: «Прохорова Арина Сергеевна». Это персональные данные, и в отчёте,
# который уходит по почте и печатается, им не место — тем более что для работы
# они ничего не добавляют: организация находится по номеру.
#
# Поэтому такое название заменяется на номер организации. Правило работает в
# ОДНОМ месте — там, где имена приходят из справочника, — и потому действует
# сразу везде: в списках, в карточках, в выгрузках и в псевдонимах для LLM.
#
# Признак намеренно строгий. «АО «Селезнев-Александрова»» — компания, названная
# по фамилии владельца, а не человек, и прятать её незачем. Поэтому имя
# считается личным только там, где есть отчество, инициалы или прямое указание
# на предпринимателя.
_PATRONYMIC = r"(?:ович|евич|иевич|ьич|ич|овна|евна|иевна|ична|инична|оглы|кызы|улы)"
_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ]?[а-яё]+)?"
_PERSON = (
    # Фамилия Имя Отчество — отчество опознаётся по окончанию
    re.compile(rf"\b{_WORD}\s+{_WORD}\s+[А-ЯЁ][а-яё]+{_PATRONYMIC}\b"),
    # Фамилия И.О. / Фамилия И. О. / И.О. Фамилия
    re.compile(rf"\b{_WORD}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.(?!\w)"),
    re.compile(rf"\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s*{_WORD}\b"),
)
# Прямое указание на предпринимателя: там дальше всегда имя человека
_ENTREPRENEUR = re.compile(
    r"(?:^|[^А-ЯЁа-яё])(?:ИП|И\s?/\s?П|ИНДИВИДУАЛЬНЫЙ\s+ПРЕДПРИНИМАТЕЛЬ|"
    r"ГЛАВА\s+К(?:Ф)?Х|КФХ)(?:[^А-ЯЁа-яё]|$)", re.I)


def is_person_name(name) -> bool:
    """Название организации — это имя человека?"""
    s = str(name or "").strip()
    if not s:
        return False
    if _ENTREPRENEUR.search(s):
        return True
    return any(rx.search(s) for rx in _PERSON)


def org_label(name, inn) -> str:
    """Как называть организацию в отчёте: имя — или номер вместо имени человека."""
    s = str(name or "").strip()
    if not s or is_person_name(s):
        return f"Орг. {inn}" if inn not in (None, "") else ""
    return s


def mask_people(df, name_col: str = "company_name", inn_col: str = "inn"):
    """Заменить имена людей на номер организации во всей колонке кадра.

    Возвращает (кадр, сколько заменено) — число печатается в лог сборки: молча
    прятать часть названий нельзя, иначе однажды окажется, что правило съело
    обычные компании.
    """
    if df is None or getattr(df, "empty", True) or name_col not in df:
        return df, 0
    names = df[name_col].astype(str)
    inns = df[inn_col] if inn_col in df else [""] * len(df)
    masked, n = [], 0
    for nm, inn in zip(names, inns):
        lbl = org_label(nm, inn)
        if lbl != nm.strip():
            n += 1
        masked.append(lbl)
    df = df.copy()
    df[name_col] = masked
    return df, n
