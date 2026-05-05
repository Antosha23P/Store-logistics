# -*- coding: utf-8 -*-
"""
Совместимость на уровне машины: П и М никогда не вместе (П+М и П+М+Н запрещены).
Дробление отгрузки с магазина; диапазон 90–120; жадная укладка + слияние рейсов.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# Порядок вывода в отчёте (режим по факту загрузки; П+М+Н запрещён — см. can_have_on_truck)
MODE_ORDER = ["PN", "MN", "P", "M", "N"]


def can_have_on_truck(P: int, M: int, N: int) -> bool:
    """Продукты и моющие никогда не едут в одной машине, даже с напитками (П+М+Н нельзя)."""
    return not (P > 0 and M > 0)


def dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def truck_totals(truck: Dict[int, Tuple[int, int, int]]) -> Tuple[int, int, int]:
    P = M = N = 0
    for dp, dm, dn in truck.values():
        P += dp
        M += dm
        N += dn
    return P, M, N


def truck_total_units(truck: Dict[int, Tuple[int, int, int]]) -> int:
    return sum(dp + dm + dn for dp, dm, dn in truck.values())


def describe_truck_mode(P: int, M: int, N: int) -> str:
    if P == M == N == 0:
        return "—"
    if P > 0 and M > 0:
        return "ERR"  # не должно случаться при корректной укладке
    if P > 0 and N > 0 and M == 0:
        return "PN"
    if M > 0 and N > 0 and P == 0:
        return "MN"
    if P > 0 and M == N == 0:
        return "P"
    if M > 0 and P == N == 0:
        return "M"
    if N > 0 and P == M == 0:
        return "N"
    return "MIX"


MODE_LABELS_RU = {
    "P": "только продукты (П)",
    "M": "только моющие (М)",
    "N": "только напитки (Н)",
    "PN": "продукты + напитки (П+Н)",
    "MN": "моющие + напитки (М+Н)",
    "ERR": "ошибка: П и М на одной машине",
    "MIX": "смешанная загрузка",
    "—": "пусто",
}


@dataclass
class TruckPlan:
    mode: str
    load_units: int
    load_p: int
    load_m: int
    load_n: int
    loads_by_store: Dict[int, Tuple[int, int, int]]
    visit_order: List[int]
    distance: float
    detail_lines: List[str] = field(default_factory=list)


@dataclass
class FullPlan:
    trucks: List[TruckPlan]
    total_trucks: int
    total_distance: float
    sum_p: int
    sum_m: int
    sum_n: int
    store_splits: Dict[int, List[str]]
    warnings: List[str]
    day: int | None = None
    min_trucks_possible: int = 0  # ceil(всего ед. / cap_max) при лимите cap_max


def _rem_copy(demands: Dict[int, Tuple[int, int, int]]) -> Dict[int, List[int]]:
    return {int(s): [int(p), int(m), int(n)] for s, (p, m, n) in demands.items() if p + m + n > 0}


def _rem_total(rem: Dict[int, List[int]]) -> int:
    return sum(sum(v) for v in rem.values())


def _rem_del_empty(rem: Dict[int, List[int]]) -> None:
    dead = [s for s in rem if sum(rem[s]) == 0]
    for s in dead:
        del rem[s]


def _rem_categories_count(rem: Dict[int, List[int]], s: int) -> int:
    r = rem.get(s, [0, 0, 0])
    return sum(1 for x in r if x > 0)


def _absorb_from_stores_already_on_truck(
    truck: Dict[int, List[int]],
    rem: Dict[int, List[int]],
    cap_limit: int,
    idx_order: Tuple[int, ...],
) -> Tuple[int, int, int]:
    """
    Добор с уже открытых на этой машине магазинов.
    idx_order — порядок категорий (0=П,1=М,2=Н): для П+Н используйте (0,2), для М+Н (1,2), напитки в конце.
    """
    Pt, Mt, Nt = truck_totals({k: tuple(v) for k, v in truck.items()})
    progressed = True
    while progressed and Pt + Mt + Nt < cap_limit and _rem_total(rem) > 0:
        progressed = False
        for s in list(truck.keys()):
            if s not in rem or sum(truck[s]) == 0:
                continue
            space = cap_limit - Pt - Mt - Nt
            if space <= 0:
                break
            for idx in idx_order:
                if rem[s][idx] <= 0:
                    continue
                max_amt = min(rem[s][idx], space)
                if max_amt <= 0:
                    continue
                for amt in range(max_amt, 0, -1):
                    nP, nM, nN = Pt, Mt, Nt
                    if idx == 0:
                        nP += amt
                    elif idx == 1:
                        nM += amt
                    else:
                        nN += amt
                    if can_have_on_truck(nP, nM, nN):
                        Pt, Mt, Nt = nP, nM, nN
                        rem[s][idx] -= amt
                        truck[s][idx] += amt
                        progressed = True
                        space = cap_limit - Pt - Mt - Nt
                        break
            _rem_del_empty(rem)
    return Pt, Mt, Nt


def fill_one_truck(
    rem: Dict[int, List[int]],
    cap_limit: int,
    fleet_hint: str = "ANY",
) -> Dict[int, Tuple[int, int, int]]:
    """
    Набирает машину до cap_limit (не выше), соблюдая П/М раздельно.
    fleet_hint: PN — сначала П, потом Н; MN — сначала М, потом Н; N напитки последним добивают объём.
    ANY — прежняя логика (для остатков и fallback).
    """
    if fleet_hint == "PN":
        idx_order = (0, 2)
    elif fleet_hint == "MN":
        idx_order = (1, 2)
    elif fleet_hint == "P":
        idx_order = (0,)
    elif fleet_hint == "M":
        idx_order = (1,)
    elif fleet_hint == "N":
        idx_order = (2,)
    else:
        idx_order = (0, 1, 2)

    def idx_allowed(idx: int) -> bool:
        if fleet_hint == "PN":
            return idx in (0, 2)
        if fleet_hint == "MN":
            return idx in (1, 2)
        if fleet_hint == "P":
            return idx == 0
        if fleet_hint == "M":
            return idx == 1
        if fleet_hint == "N":
            return idx == 2
        return True

    absorb_order = idx_order if fleet_hint != "ANY" else (2, 0, 1)

    truck: Dict[int, List[int]] = defaultdict(lambda: [0, 0, 0])
    Pt = Mt = Nt = 0

    while Pt + Mt + Nt < cap_limit and _rem_total(rem) > 0:
        Pt, Mt, Nt = _absorb_from_stores_already_on_truck(
            truck, rem, cap_limit, absorb_order
        )
        if Pt + Mt + Nt >= cap_limit:
            break

        best: Tuple[int, int, int, int, int] | None = None
        scan_order = idx_order if fleet_hint != "ANY" else (2, 0, 1)
        for s in rem:
            cats = _rem_categories_count(rem, s)
            tie = sum(rem[s])
            pri_base = 0 if cats >= 2 else 1
            for idx in scan_order:
                if not idx_allowed(idx):
                    continue
                if rem[s][idx] <= 0:
                    continue
                max_amt = min(rem[s][idx], cap_limit - Pt - Mt - Nt)
                if max_amt <= 0:
                    continue
                for amt in range(max_amt, 0, -1):
                    nP, nM, nN = Pt, Mt, Nt
                    if idx == 0:
                        nP += amt
                    elif idx == 1:
                        nM += amt
                    else:
                        nN += amt
                    if can_have_on_truck(nP, nM, nN):
                        pri = pri_base
                        cand = (pri, amt, s, idx, tie)
                        if best is None:
                            best = cand
                        elif cand[1] > best[1]:
                            best = cand
                        elif cand[1] == best[1]:
                            if cand[0] < best[0]:
                                best = cand
                            elif cand[0] == best[0] and cand[4] > best[4]:
                                best = cand
                        break
        if best is None:
            break
        _, amt, s, idx, _ = best
        if idx == 0:
            Pt += amt
        elif idx == 1:
            Mt += amt
        else:
            Nt += amt
        rem[s][idx] -= amt
        truck[s][idx] += amt
        _rem_del_empty(rem)

        Pt, Mt, Nt = _absorb_from_stores_already_on_truck(
            truck, rem, cap_limit, absorb_order
        )

    out = {s: (v[0], v[1], v[2]) for s, v in truck.items() if sum(v) > 0}
    return out


def _next_fleet_hint(
    rem: Dict[int, List[int]],
    turn: int,
) -> str:
    """Чередуем М+Н и П+Н, пока есть оба вида спроса; напитки не расходуем «вперёд» отдельно."""
    tot_p = sum(rem[s][0] for s in rem)
    tot_m = sum(rem[s][1] for s in rem)
    tot_n = sum(rem[s][2] for s in rem)
    if tot_p == 0 and tot_m == 0:
        return "N" if tot_n > 0 else "P"
    if tot_m == 0:
        return "PN" if tot_n > 0 else "P"
    if tot_p == 0:
        return "MN" if tot_n > 0 else "M"
    if turn % 2 == 0:
        return "MN" if tot_m >= tot_p else "PN"
    return "PN" if tot_m >= tot_p else "MN"


def _balanced_cap_for_next_truck(rem: Dict[int, List[int]], cap_max: int) -> int:
    """Целевой верх на этот рейс: как можно ровнее уложить в ceil(остаток/cap_max) машин (часто 6 при 715)."""
    rem_t = _rem_total(rem)
    if rem_t <= 0:
        return cap_max
    k = max(1, (rem_t + cap_max - 1) // cap_max)
    return min(cap_max, (rem_t + k - 1) // k)


def _combine_trucks(
    a: Dict[int, Tuple[int, int, int]], b: Dict[int, Tuple[int, int, int]]
) -> Dict[int, Tuple[int, int, int]]:
    m: Dict[int, List[int]] = defaultdict(lambda: [0, 0, 0])
    for src in (a, b):
        for s, (dp, dm, dn) in src.items():
            m[s][0] += dp
            m[s][1] += dm
            m[s][2] += dn
    return {s: (v[0], v[1], v[2]) for s, v in m.items() if sum(v) > 0}


def _valid_truck(truck: Dict[int, Tuple[int, int, int]]) -> bool:
    return can_have_on_truck(*truck_totals(truck))


def merge_trucks_to_minimize(trucks: List[Dict[int, Tuple[int, int, int]]], cap_max: int):
    """Слияние пар машин, если суммарно ≤ cap_max и совместимость сохраняется."""
    trucks = [t for t in trucks if truck_total_units(t) > 0]
    changed = True
    while changed:
        changed = False
        for i in range(len(trucks)):
            for j in range(i + 1, len(trucks)):
                c = _combine_trucks(trucks[i], trucks[j])
                tot = truck_total_units(c)
                if tot <= cap_max and _valid_truck(c):
                    trucks[i] = c
                    trucks.pop(j)
                    changed = True
                    break
            if changed:
                break
    return trucks


def _truck_mode_of(t: Dict[int, Tuple[int, int, int]]) -> str:
    return describe_truck_mode(*truck_totals(t))


def _mut_truck(t: Dict[int, Tuple[int, int, int]]) -> Dict[int, List[int]]:
    return {s: [v[0], v[1], v[2]] for s, v in t.items() if sum(v) > 0}


def _freeze_truck(m: Dict[int, List[int]]) -> Dict[int, Tuple[int, int, int]]:
    return {s: (v[0], v[1], v[2]) for s, v in m.items() if sum(v) > 0}


def _ttot_m(m: Dict[int, List[int]]) -> int:
    return sum(sum(v) for v in m.values())


def _transfer_units_dl(
    donor: Dict[int, List[int]],
    recv: Dict[int, List[int]],
    target: int,
    allowed_idx: Tuple[int, ...],
) -> int:
    """Переносит до target единиц только по указанным индексам (0=П,1=М,2=Н)."""
    moved = 0
    while moved < target:
        took = False
        for s in sorted(donor.keys()):
            for idx in allowed_idx:
                if donor[s][idx] <= 0:
                    continue
                donor[s][idx] -= 1
                recv.setdefault(s, [0, 0, 0])
                recv[s][idx] += 1
                moved += 1
                took = True
                if moved >= target:
                    return moved
                break
            if took:
                break
        if not took:
            return moved
    return moved


def _shift_within_fleet(
    trucks_mut: List[Dict[int, List[int]]],
    cap_min: int,
    cap_max: int,
    allowed_idx: Tuple[int, ...],
) -> None:
    """Донорские машины (> cap_min) отдают груз недогруженным (< cap_min), не нарушая cap_max."""
    changed = True
    while changed:
        changed = False
        for i in range(len(trucks_mut)):
            ti = _ttot_m(trucks_mut[i])
            if ti >= cap_min or ti == 0:
                continue
            need = cap_min - ti
            for j in range(len(trucks_mut)):
                if i == j:
                    continue
                tj = _ttot_m(trucks_mut[j])
                if tj <= cap_min:
                    continue
                room = cap_max - ti
                can_give = tj - cap_min
                mv = min(need, can_give, room)
                if mv <= 0:
                    continue
                n = _transfer_units_dl(trucks_mut[j], trucks_mut[i], mv, allowed_idx)
                if n > 0:
                    changed = True
                    break
            if changed:
                break


def _pool_repack_one_fleet(
    fleet: List[Dict[int, Tuple[int, int, int]]],
    mode: str,
    cap_min: int,
    cap_max: int,
) -> List[Dict[int, Tuple[int, int, int]]]:
    """Объединяет все рейсы одного режима и заново набирает машины + сдвиг под cap_min."""
    if not fleet:
        return []
    merged: Dict[int, List[int]] = defaultdict(lambda: [0, 0, 0])
    for t in fleet:
        for s, (dp, dm, dn) in t.items():
            merged[s][0] += dp
            merged[s][1] += dm
            merged[s][2] += dn
    rem: Dict[int, List[int]] = {}
    for s, v in merged.items():
        if sum(v) == 0:
            continue
        p, m, n = v[0], v[1], v[2]
        if mode == "PN":
            rem[s] = [p, 0, n]
        elif mode == "MN":
            rem[s] = [0, m, n]
        elif mode == "P":
            rem[s] = [p, 0, 0]
        elif mode == "M":
            rem[s] = [0, m, 0]
        elif mode == "N":
            rem[s] = [0, 0, n]
        else:
            rem[s] = [p, m, n]
    _rem_del_empty(rem)
    if not rem:
        return []
    out: List[Dict[int, Tuple[int, int, int]]] = []
    guard = 0
    hint = mode if mode in ("PN", "MN", "P", "M", "N") else "ANY"
    while _rem_total(rem) > 0 and guard < 5000:
        guard += 1
        cap_lim = _balanced_cap_for_next_truck(rem, cap_max)
        t = fill_one_truck(rem, cap_lim, hint)
        if not t:
            break
        out.append(t)
    allowed = {"PN": (0, 2), "MN": (1, 2), "P": (0,), "M": (1,), "N": (2,)}.get(mode, (0, 1, 2))
    muts = [_mut_truck(x) for x in out]
    muts = [m for m in muts if _ttot_m(m) > 0]
    _shift_within_fleet(muts, cap_min, cap_max, allowed)
    return [_freeze_truck(m) for m in muts if _ttot_m(m) > 0]


def redistribute_by_fleet(
    trucks: List[Dict[int, Tuple[int, int, int]]],
    cap_min: int,
    cap_max: int,
) -> List[Dict[int, Tuple[int, int, int]]]:
    """Пересбор по флотам PN / MN / P / M / N: общий пул → новые машины → доводка до 90+ за счёт других машин флота."""
    groups: Dict[str, List[Dict[int, Tuple[int, int, int]]]] = defaultdict(list)
    loose: List[Dict[int, Tuple[int, int, int]]] = []
    for t in trucks:
        m = _truck_mode_of(t)
        if m in ("ERR", "MIX", "—"):
            loose.append(t)
        else:
            groups[m].append(t)
    out = loose[:]
    for mode in MODE_ORDER:
        if mode not in groups:
            continue
        out.extend(_pool_repack_one_fleet(groups[mode], mode, cap_min, cap_max))
    return [t for t in out if truck_total_units(t) > 0]


def _absorb_cross_fleet_pure(
    trucks: List[Dict[int, Tuple[int, int, int]]],
    cap_max: int,
) -> List[Dict[int, Tuple[int, int, int]]]:
    """
    Перенос «чистых» остатков на смешанные рейсы при наличии места:
    Н → машина только П (становится П+Н); Н → П+Н; М → М+Н; П → П+Н.
    Все товары с магазина довозятся, часть может уехать вторым рейсом (уже заложено).
    """
    muts = [_mut_truck(t) for t in trucks if truck_total_units(t) > 0]

    def lbl(m: Dict[int, List[int]]) -> str:
        return describe_truck_mode(*truck_totals(_freeze_truck(m)))

    def try_move(
        from_modes: Tuple[str, ...],
        to_modes: Tuple[str, ...],
        idx_take: int,
    ) -> bool:
        did = False
        progress = True
        while progress:
            progress = False
            for ri, recv in enumerate(muts):
                if _ttot_m(recv) == 0 or lbl(recv) not in to_modes:
                    continue
                if _ttot_m(recv) >= cap_max:
                    continue
                room = cap_max - _ttot_m(recv)
                if room <= 0:
                    continue
                for di, donor in enumerate(muts):
                    if di == ri or _ttot_m(donor) == 0 or lbl(donor) not in from_modes:
                        continue
                    n = _transfer_units_dl(donor, recv, room, (idx_take,))
                    if n > 0:
                        did = True
                        progress = True
                        break
                if progress:
                    break
        return did

    def try_n_from_pn_to_mn() -> bool:
        """Перенос Н с П+Н на М+Н: освобождает объём под П (остаток «только П» залезает на П+Н)."""
        if not any(lbl(m) == "P" and _ttot_m(m) > 0 for m in muts):
            return False
        did = False
        progress = True
        while progress:
            progress = False
            for ri, recv in enumerate(muts):
                if lbl(recv) != "MN" or _ttot_m(recv) >= cap_max:
                    continue
                room = cap_max - _ttot_m(recv)
                if room <= 0:
                    continue
                for di, donor in enumerate(muts):
                    if di == ri or lbl(donor) != "PN":
                        continue
                    n = _transfer_units_dl(donor, recv, room, (2,))
                    if n > 0:
                        did = True
                        progress = True
                        break
                if progress:
                    break
        return did

    for _ in range(500):
        moved = False
        moved |= try_n_from_pn_to_mn()
        moved |= try_move(("N",), ("P",), 2)
        moved |= try_move(("N",), ("PN",), 2)
        moved |= try_move(("P",), ("PN",), 0)
        moved |= try_move(("M",), ("MN",), 1)
        moved |= try_move(("N",), ("MN",), 2)
        if not moved:
            break

    return [_freeze_truck(m) for m in muts if _ttot_m(m) > 0]


def pack_trucks(
    demands: Dict[int, Tuple[int, int, int]],
    cap_min: int,
    cap_max: int,
) -> List[Dict[int, Tuple[int, int, int]]]:
    rem = _rem_copy(demands)
    trucks: List[Dict[int, Tuple[int, int, int]]] = []
    turn = 0
    guard = 0
    while _rem_total(rem) > 0:
        guard += 1
        if guard > 5000:
            break
        cap_lim = _balanced_cap_for_next_truck(rem, cap_max)
        hint = _next_fleet_hint(rem, turn)
        t = fill_one_truck(rem, cap_lim, hint)
        if not t:
            for h in ("PN", "MN", "P", "M", "N", "ANY"):
                t = fill_one_truck(rem, min(cap_lim, cap_max), h)
                if t:
                    break
        if not t:
            t = fill_one_truck(rem, cap_max, "ANY")
        if not t:
            break
        trucks.append(t)
        turn += 1
    trucks = merge_trucks_to_minimize(trucks, cap_max)
    trucks = redistribute_by_fleet(trucks, cap_min, cap_max)
    trucks = _absorb_cross_fleet_pure(trucks, cap_max)
    trucks = redistribute_by_fleet(trucks, cap_min, cap_max)
    return [t for t in trucks if truck_total_units(t) > 0]


def build_warnings(
    trucks: List[Dict[int, Tuple[int, int, int]]],
    cap_min: int,
    cap_max: int,
    total_day_units: int,
) -> List[str]:
    w: List[str] = []
    for i, t in enumerate(trucks, 1):
        u = truck_total_units(t)
        if u > cap_max:
            w.append(f"Машина {i}: загрузка {u} > максимума {cap_max} (ошибка алгоритма).")
        if total_day_units >= cap_min and u < cap_min:
            w.append(
                f"Машина {i}: загрузка {u} < минимума {cap_min} "
                f"(допустимо, если весь день мало товара или жёсткие ограничения П/М по разным машинам)."
            )
    return w


def build_store_splits_from_trucks(
    trucks: List[Dict[int, Tuple[int, int, int]]],
) -> Dict[int, List[str]]:
    per: Dict[int, List[str]] = defaultdict(list)
    for ti, t in enumerate(trucks, 1):
        for s, (dp, dm, dn) in sorted(t.items(), key=lambda x: x[0]):
            if dp + dm + dn == 0:
                continue
            per[s].append(f"рейс {ti}: П={dp} М={dm} Н={dn}")
    return dict(per)


def route_length(
    order: List[int],
    coords: Dict[int, Tuple[int, int]],
    warehouse: Tuple[int, int],
) -> float:
    if not order:
        return 0.0
    wh = (float(warehouse[0]), float(warehouse[1]))
    pts = [wh] + [(float(coords[i][0]), float(coords[i][1])) for i in order] + [wh]
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def nn_tsp(
    stores: List[int],
    coords: Dict[int, Tuple[int, int]],
    warehouse: Tuple[int, int],
) -> List[int]:
    if len(stores) <= 1:
        return list(stores)
    wh = (float(warehouse[0]), float(warehouse[1]))
    remaining = set(stores)
    cur = wh
    path: List[int] = []
    while remaining:
        nxt = min(
            remaining,
            key=lambda s: dist(cur, (float(coords[s][0]), float(coords[s][1]))),
        )
        path.append(nxt)
        remaining.remove(nxt)
        cur = (float(coords[nxt][0]), float(coords[nxt][1]))
    return path


def two_opt(
    path: List[int],
    coords: Dict[int, Tuple[int, int]],
    warehouse: Tuple[int, int],
    max_passes: int = 50,
) -> List[int]:
    if len(path) < 4:
        return path
    best = path[:]
    best_len = route_length(best, coords, warehouse)
    improved = True
    passes = 0
    while improved and passes < max_passes:
        improved = False
        passes += 1
        for i in range(len(best) - 1):
            for j in range(i + 2, len(best)):
                if j == len(best) - 1 and i == 0:
                    continue
                new = best[: i + 1] + best[i + 1 : j + 1][::-1] + best[j + 1 :]
                ln = route_length(new, coords, warehouse)
                if ln + 1e-9 < best_len:
                    best, best_len = new, ln
                    improved = True
                    break
            if improved:
                break
    return best


def optimize_route(
    stores: List[int],
    coords: Dict[int, Tuple[int, int]],
    warehouse: Tuple[int, int],
) -> Tuple[List[int], float]:
    uniq = list(dict.fromkeys(stores))
    if not uniq:
        return [], 0.0
    p = nn_tsp(uniq, coords, warehouse)
    p = two_opt(p, coords, warehouse)
    return p, route_length(p, coords, warehouse)


def compute_plan(
    demands: Dict[int, Tuple[int, int, int]],
    coords: Dict[int, Tuple[int, int]],
    warehouse: Tuple[int, int],
    cap_min: int = 90,
    cap_max: int = 120,
    day: int | None = None,
) -> FullPlan:
    demands = {int(k): (int(v[0]), int(v[1]), int(v[2])) for k, v in demands.items()}
    coords = {int(k): (int(v[0]), int(v[1])) for k, v in coords.items()}
    sum_p = sum(p for p, _, _ in demands.values())
    sum_m = sum(m for _, m, _ in demands.values())
    sum_n = sum(n for _, _, n in demands.values())
    total_u = sum_p + sum_m + sum_n
    min_trucks_possible = (total_u + cap_max - 1) // cap_max if cap_max > 0 else 0

    raw_trucks = pack_trucks(demands, cap_min, cap_max)
    plans: List[TruckPlan] = []
    total_dist = 0.0
    mode_rank = {m: i for i, m in enumerate(MODE_ORDER)}

    for t in raw_trucks:
        P, M, N = truck_totals(t)
        mode = describe_truck_mode(P, M, N)
        stores_visit = sorted(t.keys())
        order, ln = optimize_route(stores_visit, coords, warehouse)
        total_dist += ln
        detail = [
            f"№{s}: П={t[s][0]} М={t[s][1]} Н={t[s][2]} "
            f"({t[s][0]+t[s][1]+t[s][2]} ед.)"
            for s in stores_visit
        ]
        plans.append(
            TruckPlan(
                mode=mode,
                load_units=P + M + N,
                load_p=P,
                load_m=M,
                load_n=N,
                loads_by_store=dict(t),
                visit_order=order,
                distance=ln,
                detail_lines=detail,
            )
        )

    plans.sort(key=lambda tr: (mode_rank.get(tr.mode, 99), -tr.load_units, tr.mode))

    warnings = build_warnings(raw_trucks, cap_min, cap_max, total_u)
    store_splits = build_store_splits_from_trucks(raw_trucks)

    return FullPlan(
        trucks=plans,
        total_trucks=len(plans),
        total_distance=total_dist,
        sum_p=sum_p,
        sum_m=sum_m,
        sum_n=sum_n,
        store_splits=store_splits,
        warnings=warnings,
        day=day,
        min_trucks_possible=min_trucks_possible,
    )


def compute_plans_all_days(
    demands_by_day: Dict[int, Dict[int, Tuple[int, int, int]]],
    coords: Dict[int, Tuple[int, int]],
    warehouse: Tuple[int, int],
    cap_min: int,
    cap_max: int,
) -> Dict[int, FullPlan]:
    out: Dict[int, FullPlan] = {}
    for d in sorted(demands_by_day.keys()):
        out[d] = compute_plan(
            demands_by_day[d], coords, warehouse, cap_min, cap_max, day=d
        )
    return out


# --- разбор ввода ---


def _norm_col(s: str) -> str:
    t = str(s).strip().lower().replace("ё", "е")
    t = re.sub(r"\s+", "", t)
    return t


def _parse_cell(v) -> int:
    if v is None:
        return 0
    if isinstance(v, float) and math.isnan(v):
        return 0
    s = str(v).strip().replace(",", ".")
    if s in ("", "-", "—", "nan", "none"):
        return 0
    return int(float(s))


def _pick(row: Dict, aliases: Tuple[str, ...]) -> int:
    key_map = {_norm_col(k): v for k, v in row.items()}
    for a in aliases:
        nk = _norm_col(a)
        if nk in key_map:
            return _parse_cell(key_map[nk])
    raise KeyError(aliases[0])


def normalize_demands_coords(
    rows: List[Dict],
) -> Tuple[Dict[int, Tuple[int, int, int]], Dict[int, Tuple[int, int]]]:
    """Один день: все строки попадают в день 1, если колонки «день» нет."""
    by_day, coords = normalize_demands_coords_multi(rows)
    if not by_day:
        return {}, coords
    if len(by_day) == 1:
        return next(iter(by_day.values())), coords
    raise ValueError(
        "В таблице несколько разных дней. Укажите один день или включите расчёт «все дни» в приложении."
    )


def normalize_demands_coords_multi(
    rows: List[Dict],
) -> Tuple[Dict[int, Dict[int, Tuple[int, int, int]]], Dict[int, Tuple[int, int]]]:
    """
    Несколько дней: колонка day (или день). Координаты — по последней встреченной строке магазина.
    """
    id_aliases = ("id", "№", "номер", "магазин", "shop", "nomer")
    demands_by_day: Dict[int, Dict[int, Tuple[int, int, int]]] = defaultdict(dict)
    coords: Dict[int, Tuple[int, int]] = {}

    for row in rows:
        if not row or not any(
            str(v).strip() not in ("", "nan", "None") for v in row.values() if v is not None
        ):
            continue
        try:
            sid = int(_pick(row, id_aliases))
        except KeyError:
            continue
        if sid <= 0:
            continue

        def opt(aliases: Tuple[str, ...]) -> int:
            try:
                return _pick(row, aliases)
            except KeyError:
                return 0

        try:
            day = opt(("day", "день", "d", "date"))
        except Exception:
            day = 0
        if day <= 0:
            day = 1

        p = opt(("p", "п", "продукты", "products"))
        m = opt(("m", "м", "моющие", "detergents"))
        n = opt(("n", "н", "напитки", "drinks"))
        x = opt(("x", "х"))
        y = opt(("y", "у"))

        if (p, m, n) != (0, 0, 0):
            cur = demands_by_day[day].get(sid)
            if cur is None:
                demands_by_day[day][sid] = (p, m, n)
            else:
                demands_by_day[day][sid] = (cur[0] + p, cur[1] + m, cur[2] + n)
        if x != 0 or y != 0:
            coords[sid] = (x, y)

    return dict(demands_by_day), coords


def parse_text_table(text: str) -> List[Dict[str, int]]:
    """
    7 чисел: day id x y p m n
    6 чисел: id x y p m n
    4 числа: id p m n
    """
    rows: List[Dict[str, int]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"[,;|\t]+", " ", line)
        parts = line.split()
        nums: List[int] = []
        for p in parts:
            if p in ("-", "—"):
                nums.append(0)
                continue
            if re.fullmatch(r"-?\d+", p):
                nums.append(int(p))
        if len(nums) >= 7:
            rows.append(
                {
                    "day": nums[0],
                    "id": nums[1],
                    "x": nums[2],
                    "y": nums[3],
                    "p": nums[4],
                    "m": nums[5],
                    "n": nums[6],
                }
            )
        elif len(nums) >= 6:
            rows.append(
                {
                    "id": nums[0],
                    "x": nums[1],
                    "y": nums[2],
                    "p": nums[3],
                    "m": nums[4],
                    "n": nums[5],
                }
            )
        elif len(nums) >= 4:
            rows.append(
                {
                    "id": nums[0],
                    "x": 0,
                    "y": 0,
                    "p": nums[1],
                    "m": nums[2],
                    "n": nums[3],
                }
            )
    return rows


def dataframe_to_rows(df) -> List[Dict]:
    return df.to_dict(orient="records")
