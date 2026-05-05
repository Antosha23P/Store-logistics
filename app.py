# -*- coding: utf-8 -*-
"""
Логистика магазинов: таблица / CSV / фото → разнесение по машинам и маршруты.

Запуск: python -m streamlit run app.py
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from logistics_engine import (
    MODE_LABELS_RU,
    MODE_ORDER,
    compute_plan,
    dataframe_to_rows,
    normalize_demands_coords_multi,
    parse_text_table,
)
from photo_ocr import ocr_available, ocr_hint, run_ocr_on_image

_SAMPLE_ROWS: List[Tuple[int, int, int, int, int, int, int]] = [
    (1, 1, 19, 9, 2, 0, 16),
    (1, 2, 25, 6, 12, 4, 0),
    (1, 3, 28, 4, 10, 5, 5),
    (1, 4, 27, 2, 4, 6, 8),
    (1, 5, 20, 5, 25, 5, 15),
    (1, 6, 18, 2, 20, 5, 11),
    (1, 7, 16, 7, 13, 8, 7),
    (1, 8, 13, 3, 5, 2, 5),
    (1, 9, 9, 2, 20, 5, 6),
    (1, 10, 11, 7, 0, 0, 11),
    (1, 11, 4, 4, 0, 0, 0),
    (1, 12, 6, 7, 10, 6, 5),
    (1, 13, 2, 8, 8, 5, 14),
    (1, 14, 12, 9, 0, 0, 0),
    (1, 15, 4, 11, 20, 9, 16),
    (1, 16, 8, 12, 0, 0, 0),
    (1, 17, 2, 14, 22, 16, 16),
    (1, 18, 8, 15, 0, 0, 0),
    (1, 19, 13, 12, 18, 0, 7),
    (1, 20, 12, 15, 15, 0, 8),
    (1, 21, 15, 14, 20, 10, 25),
    (1, 22, 16, 17, 6, 10, 3),
    (1, 23, 18, 12, 12, 0, 5),
    (1, 24, 20, 16, 5, 0, 8),
    (1, 25, 23, 17, 7, 5, 10),
    (1, 26, 23, 14, 0, 0, 0),
    (1, 27, 27, 17, 23, 0, 21),
    (1, 28, 20, 15, 20, 20, 25),
    (1, 29, 24, 10, 16, 4, 0),
    (1, 30, 28, 8, 22, 0, 8),
]


def sample_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"day": r[0], "id": r[1], "x": r[2], "y": r[3], "p": r[4], "m": r[5], "n": r[6]}
            for r in _SAMPLE_ROWS
        ]
    )


def rules_expander():
    with st.expander("Правила совместимости и вместимости", expanded=False):
        st.markdown(
            """
- **Машина:** суммарно от **минимума** до **максимума** единиц (по умолчанию **90–120**).  
  Нижняя граница — целевой ориентир: если из‑за П/М без Н остаются мелкие остатки, рейс может быть меньше 90 (будет предупреждение).
- **На одной машине допустимо:** только **П**; только **М**; только **Н**; **П+Н**; **М+Н**.  
  **Нельзя** везти **П+М** и **П+М+Н** (продукты и моющие всегда разными машинами), в том числе из разных магазинов.
- **Один магазин** может обслужить **несколько машин**; с одной точки можно взять **не всё сразу** (например, 20 П и 10 Н из 15 Н) — **оставшиеся единицы обязаны уехать другим рейсом** (алгоритм это учитывает).
- **Тактика укладки:** чередуются рейсы **М+Н** и **П+Н** (по остаткам); внутри рейса сначала набираются **П или М**, затем **напитки** добивают объём. Целевой размер рейса считается **поровну** по числу оставшихся машин (≈715/6≈119), чтобы уместиться в минимум рейсов. После этого — перераспределение по флотам и перенос **Н с П+Н на М+Н** при «висящем» только-П (освобождается место под продукты).
- **Несколько дней:** колонка **day** (или **день**). Для каждого дня — свой расчёт; координаты магазинов общие.
- **Минимум машин** не может быть меньше **⌈всего единиц / максимум на машину⌉** — это показывается в отчёте.
            """
        )


def validate_coords(demands, coords) -> List[str]:
    msgs: List[str] = []
    if not demands:
        msgs.append("Нет строк со спросом.")
        return msgs
    for sid, (p, m, n) in sorted(demands.items(), key=lambda x: int(x[0])):
        if p or m or n:
            x, y = coords.get(int(sid), (0, 0))
            if x == 0 and y == 0:
                msgs.append(f"Магазин {sid}: есть спрос, но координаты (0, 0).")
    return msgs


def render_plan(plan, title_prefix: str = ""):
    if title_prefix:
        st.header(title_prefix)
    st.subheader("Итог")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Рейсов", plan.total_trucks)
    c2.metric("Нижняя оценка рейсов", plan.min_trucks_possible)
    c3.metric("Суммарный пробег", f"{plan.total_distance:.1f}")
    c4.metric("Всего единиц", plan.sum_p + plan.sum_m + plan.sum_n)
    c5.metric("П / М / Н", f"{plan.sum_p} / {plan.sum_m} / {plan.sum_n}")
    if plan.total_trucks > plan.min_trucks_possible:
        st.info(
            f"Рейсов больше, чем ⌈ед./макс⌉ = {plan.min_trucks_possible}, "
            f"из‑за запрета **П+М** и **П+М+Н** (продукты и моющие только разными машинами)."
        )
    elif plan.total_trucks == plan.min_trucks_possible:
        st.success("Число рейсов совпадает с теоретическим минимумом при заданном максимуме загрузки.")

    for w in plan.warnings:
        st.warning(w)

    with st.expander("Дробление по магазинам (несколько заездов)", expanded=True):
        if not plan.store_splits:
            st.write("Нет отгрузок.")
        else:
            for sid, lines in sorted(plan.store_splits.items(), key=lambda x: x[0]):
                st.write(f"**№{sid}:** " + " · ".join(lines))

    mode_idx = {m: i for i, m in enumerate(MODE_ORDER)}
    trucks_sorted = sorted(plan.trucks, key=lambda t: (mode_idx.get(t.mode, 99), -t.load_units, t.mode))

    for i, tr in enumerate(trucks_sorted, 1):
        label = MODE_LABELS_RU.get(tr.mode, tr.mode)
        seq = " → ".join(str(s) for s in tr.visit_order) if tr.visit_order else "—"
        st.markdown(f"### Машина {i}: {label}")
        st.caption(
            f"Всего **{tr.load_units}** ед. (П={tr.load_p}, М={tr.load_m}, Н={tr.load_n}) · "
            f"Пробег **{tr.distance:.2f}**"
        )
        st.write(f"**Маршрут:** склад → {seq} → склад")
        for line in tr.detail_lines:
            st.text(line)


def main():
    st.set_page_config(page_title="Логистика магазинов", layout="wide")
    st.title("Разнесение товаров по машинам и маршруты")
    rules_expander()

    if "grid_df" not in st.session_state:
        st.session_state.grid_df = sample_dataframe()
    if "ocr_user" not in st.session_state:
        st.session_state.ocr_user = ""

    with st.sidebar:
        st.header("Склад и лимиты")
        wx = st.number_input("Склад X", value=6, step=1)
        wy = st.number_input("Склад Y", value=14, step=1)
        cap_min = st.number_input("Мин. единиц в машине (ориентир)", min_value=1, max_value=200, value=90, step=1)
        cap_max = st.number_input("Макс. единиц в машине", min_value=30, max_value=300, value=120, step=1)
        if cap_min > cap_max:
            st.error("Минимум не может быть больше максимума.")
        st.caption(ocr_hint())

    tab_table, tab_csv, tab_text, tab_photo = st.tabs(
        ["Таблица", "CSV / Excel", "Текст со скрина", "Фото"]
    )

    with tab_table:
        if st.button("Заполнить примером (30 магазинов, день 1)"):
            st.session_state.grid_df = sample_dataframe()
            st.rerun()
        st.caption(
            "Колонки: **day** — день; **id** — магазин; **x, y**; **p, m, n**. "
            "Для одного дня можно везде поставить **1** или скрыть столбец и оставить только один день в данных."
        )
        st.session_state.grid_df = st.data_editor(
            st.session_state.grid_df,
            num_rows="dynamic",
            use_container_width=True,
            key="editor_main",
        )

    with tab_csv:
        up = st.file_uploader("Файл .csv или .xlsx", type=["csv", "xlsx"])
        if up is not None:
            try:
                raw = up.read()
                if up.name.lower().endswith(".csv"):
                    new_df = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
                else:
                    new_df = pd.read_excel(io.BytesIO(raw))
                if st.button("Применить файл к таблице"):
                    st.session_state.grid_df = new_df
                    st.success("Таблица обновлена.")
                    st.rerun()
                st.dataframe(new_df, use_container_width=True)
            except Exception as e:
                st.error(f"Не удалось прочитать файл: {e}")

    with tab_text:
        st.caption(
            "7 чисел: `день id x y п м н` · 6 чисел: `id x y п м н` · 4 числа: `id п м н` (без координат)."
        )
        paste = st.text_area("Текст", height=200, placeholder="1 1 19 9 2 0 16")
        if st.button("Разобрать и записать в таблицу"):
            rows = parse_text_table(paste)
            if not rows:
                st.warning("Не найдено подходящих строк.")
            else:
                st.session_state.grid_df = pd.DataFrame(rows)
                st.success(f"Записано строк: {len(rows)}.")
                st.rerun()

    with tab_photo:
        st.caption("После OCR проверьте цифры. Удобно править во вкладке «Текст» или «Таблица».")
        img = st.file_uploader("Изображение", type=["png", "jpg", "jpeg", "webp"], key="up_photo")
        if img is not None and st.button("Распознать фото"):
            if not ocr_available():
                st.error("Установите: pip install easyocr")
            else:
                with st.spinner("Распознавание…"):
                    try:
                        st.session_state.ocr_user = run_ocr_on_image(img.getvalue())
                        st.success("Готово.")
                    except Exception as e:
                        st.error(str(e))
        st.text_area("Текст с фото", key="ocr_user", height=220)
        if st.button("Разобрать OCR в таблицу"):
            text = str(st.session_state.get("ocr_user", ""))
            rows = parse_text_table(text)
            if not rows:
                st.warning("Не удалось разобрать строки. Вставьте числа вручную.")
            else:
                st.session_state.grid_df = pd.DataFrame(rows)
                st.success(f"В таблицу: {len(rows)} строк.")
                st.rerun()

    st.divider()
    run = st.button("Рассчитать", type="primary")

    if run:
        if cap_min > cap_max:
            return
        active_df = st.session_state.grid_df
        if active_df is None or active_df.empty:
            st.error("Нет данных в таблице.")
            return
        rows = dataframe_to_rows(active_df)
        by_day, coords = normalize_demands_coords_multi(rows)
        if not by_day:
            st.error("Нет строк с номером магазина и спросом.")
            return

        plans_list: List[Tuple[int, Any]] = []
        for d in sorted(by_day.keys()):
            dem = by_day[d]
            if not dem:
                continue
            for msg in validate_coords(dem, coords):
                st.warning(f"День {d}: {msg}")
            pl = compute_plan(
                dem,
                coords,
                (int(wx), int(wy)),
                cap_min=int(cap_min),
                cap_max=int(cap_max),
                day=d,
            )
            plans_list.append((d, pl))

        if not plans_list:
            st.error("Нет дней с ненулевым спросом.")
            return

        all_lines: List[str] = ["day;mode;load;route;details"]
        for d, plan in plans_list:
            prefix = f"День {d}" if len(plans_list) > 1 else ""
            render_plan(plan, title_prefix=prefix)
            for tr in plan.trucks:
                seq = "-".join(str(s) for s in tr.visit_order)
                det = " | ".join(tr.detail_lines)
                all_lines.append(f"{d};{tr.mode};{tr.load_units};{seq};{det}")
            if len(plans_list) > 1:
                st.divider()

        st.download_button(
            "Скачать сводку CSV (все дни из расчёта)",
            "\n".join(all_lines),
            file_name="routes_summary.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
