#!/usr/bin/env python3
"""Отчёт по оплате работ подрядчиков на заданную дату.

Запуск:
    python report.py --date 2026-11-10
    python report.py --date 10.11.2026 --data data --csv report.csv

Только стандартная библиотека Python 3.9+. Деньги хранятся в копейках (int),
разбор сумм через Decimal, float не используется.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------- деньги и даты


def parse_money(text: str) -> int:
    """'300000.00' -> 30000000 копеек. Больше двух знаков после точки - ошибка."""
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError(f"некорректная сумма: {text!r}")
    kop = value * 100
    if kop != kop.to_integral_value():
        raise ValueError(f"сумма точнее копейки: {text!r}")
    return int(kop)


def fmt_money(kop: int) -> str:
    """30000000 -> '300 000,00'"""
    sign = "-" if kop < 0 else ""
    rub, k = divmod(abs(kop), 100)
    return f"{sign}{rub:,}".replace(",", " ") + f",{k:02d}"


def parse_date(text: Optional[str]) -> Optional[date]:
    text = (text or "").strip()
    if not text:
        return None
    for f in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, f).date()
        except ValueError:
            pass
    raise ValueError(f"некорректная дата: {text!r}")


def fmt_date(d: Optional[date]) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def natural_key(s: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------- модель


@dataclass
class Contract:
    contract_id: str
    contractor: str
    amount: int
    signed_at: Optional[date]


@dataclass
class Work:
    work_id: str
    contract_id: str
    title: str
    price: int
    status: str
    completed_at: Optional[date]
    cancelled_at: Optional[date]


@dataclass
class Payment:
    payment_id: str
    contract_id: str
    work_id: str
    purpose: str
    amount: int
    status: str
    created_at: Optional[date]
    paid_at: Optional[date]
    refunded_at: Optional[date]


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load(data_dir: Path):
    contracts = [
        Contract(r["contract_id"].strip(), r["contractor"].strip(),
                 parse_money(r["amount"]), parse_date(r["signed_at"]))
        for r in read_csv(data_dir / "contracts.csv")
    ]
    works = [
        Work(r["work_id"].strip(), r["contract_id"].strip(), r["title"].strip(),
             parse_money(r["price"]), r["status"].strip(),
             parse_date(r["completed_at"]), parse_date(r["cancelled_at"]))
        for r in read_csv(data_dir / "works.csv")
    ]
    payments = [
        Payment(r["payment_id"].strip(), r["contract_id"].strip(),
                (r["work_id"] or "").strip(), r["purpose"].strip(),
                parse_money(r["amount"]), r["status"].strip(),
                parse_date(r["created_at"]), parse_date(r["paid_at"]),
                parse_date(r["refunded_at"]))
        for r in read_csv(data_dir / "payments.csv")
    ]
    return contracts, works, payments


# ---------------------------------------------------------------- состояние на дату

DONE, PLANNED, CANCELLED = "done", "planned", "cancelled"
STATE_RU = {DONE: "принята", PLANNED: "не принята", CANCELLED: "отменена"}

# Состояние платежа на дату
P_PAID, P_QUEUED, P_REFUNDED, P_NONE = "paid", "queued", "refunded", "none"


def work_state(w: Work, on: date, warnings: list[str]) -> str:
    """Статус в выгрузке - на момент выгрузки; восстанавливаем его на дату `on`."""
    if w.status == CANCELLED:
        if w.cancelled_at is None:
            warnings.append(f"{w.work_id}: отменена без даты отмены - считаю отменённой на любую дату")
            return CANCELLED
        return CANCELLED if w.cancelled_at <= on else PLANNED
    if w.status == DONE:
        if w.completed_at is None:
            warnings.append(f"{w.work_id}: принята без даты приёмки - считаю принятой на любую дату")
            return DONE
        return DONE if w.completed_at <= on else PLANNED
    if w.status != PLANNED:
        warnings.append(f"{w.work_id}: неизвестный статус {w.status!r} - считаю запланированной")
    return PLANNED


def payment_state(p: Payment, on: date, warnings: list[str]) -> str:
    """Состояние платежа на дату `on`."""
    if p.created_at and p.created_at > on:
        return P_NONE  # ещё не заведён в учёт
    if p.status == "queued":
        return P_QUEUED
    if p.status in ("paid", "refunded"):
        if p.paid_at is None:
            warnings.append(f"{p.payment_id}: статус {p.status}, но нет даты оплаты - считаю в очереди")
            return P_QUEUED
        if p.paid_at > on:
            return P_QUEUED  # на дату уже заведён, но деньги ещё не ушли
        if p.status == "refunded":
            if p.refunded_at is None:
                warnings.append(f"{p.payment_id}: возврат без даты - считаю возвращённым на любую дату")
                return P_REFUNDED
            if p.refunded_at <= on:
                return P_REFUNDED
        return P_PAID
    warnings.append(f"{p.payment_id}: неизвестный статус {p.status!r} - платёж не учитываю")
    return P_NONE


# ---------------------------------------------------------------- расчёт


@dataclass
class WorkLine:
    work: Work
    state: str
    paid: int = 0
    sources: list[str] = field(default_factory=list)  # 'P-1: 100 000,00'

    @property
    def due(self) -> int:
        return 0 if self.state == CANCELLED else self.work.price

    @property
    def remaining(self) -> int:
        return self.due - self.paid


@dataclass
class ContractReport:
    contract: Contract
    lines: list[WorkLine]
    paid: int = 0              # фактически выплачено на дату (за вычетом возвратов)
    refunded: int = 0          # было выплачено и возвращено до даты (справочно)
    queued: int = 0            # в очереди на дату
    unallocated: int = 0       # выплачено сверх цены всех действующих работ
    notes: list[str] = field(default_factory=list)

    @property
    def due(self) -> int:
        return sum(l.due for l in self.lines)

    @property
    def cancelled_sum(self) -> int:
        return sum(l.work.price for l in self.lines if l.state == CANCELLED)

    @property
    def remaining(self) -> int:
        return self.due - self.paid


def _add(line: WorkLine, pid: str, amount: int):
    line.paid += amount
    line.sources.append(f"{pid}: {fmt_money(amount)}")


def build_report(contracts, works, payments, on: date):
    warnings: list[str] = []
    by_contract = {c.contract_id: c for c in contracts}
    works_by_id = {w.work_id: w for w in works}

    for w in works:
        if w.contract_id not in by_contract:
            warnings.append(f"{w.work_id}: договор {w.contract_id} не найден - работа пропущена")
    for p in payments:
        if p.contract_id not in by_contract:
            warnings.append(f"{p.payment_id}: договор {p.contract_id} не найден - платёж пропущен")

    reports = []
    for c in sorted(contracts, key=lambda c: natural_key(c.contract_id)):
        if c.signed_at and c.signed_at > on:
            continue  # договор ещё не подписан на дату
        lines = [WorkLine(w, work_state(w, on, warnings))
                 for w in sorted((w for w in works if w.contract_id == c.contract_id),
                                 key=lambda w: natural_key(w.work_id))]
        line_by_id = {l.work.work_id: l for l in lines}
        rep = ContractReport(c, lines)

        if sum(w.work.price for w in lines) != c.amount:
            rep.notes.append(
                f"сумма договора {fmt_money(c.amount)} не равна сумме цен работ "
                f"{fmt_money(sum(w.work.price for w in lines))}")

        # 1) отбор платежей, действующих на дату
        effective: list[Payment] = []
        for p in sorted((p for p in payments if p.contract_id == c.contract_id),
                        key=lambda p: (p.paid_at or date.max, natural_key(p.payment_id))):
            st = payment_state(p, on, warnings)
            if st == P_PAID:
                effective.append(p)
                rep.paid += p.amount
            elif st == P_QUEUED:
                rep.queued += p.amount
            elif st == P_REFUNDED:
                rep.refunded += p.amount

        # 2) привязанные платежи - на свою работу (в пределах её цены)
        pool: list[tuple[str, int]] = []  # (payment_id, остаток суммы)
        for p in effective:
            if p.work_id:
                line = line_by_id.get(p.work_id)
                if line is None:
                    other = works_by_id.get(p.work_id)
                    where = f"работа из договора {other.contract_id}" if other else "работа не найдена"
                    warnings.append(f"{p.payment_id}: привязан к {p.work_id} ({where}) - считаю платежом по договору")
                    pool.append((p.payment_id, p.amount))
                    continue
                if line.state == CANCELLED:
                    rep.notes.append(f"{p.payment_id} привязан к отменённой {p.work_id} - распределён как платёж по договору")
                    pool.append((p.payment_id, p.amount))
                    continue
                take = min(p.amount, max(line.remaining, 0))
                if take:
                    _add(line, p.payment_id, take)
                if p.amount > take:
                    rep.notes.append(f"{p.payment_id} больше остатка по {p.work_id} - излишек распределён как платёж по договору")
                    pool.append((p.payment_id, p.amount - take))
            else:
                pool.append((p.payment_id, p.amount))

        # 3) непривязанные - на работы по порядку: сначала принятые (по дате приёмки),
        #    затем не принятые (по номеру работы)
        order = sorted((l for l in lines if l.state == DONE),
                       key=lambda l: (l.work.completed_at or date.min, natural_key(l.work.work_id)))
        order += [l for l in lines if l.state == PLANNED]
        for pid, amount in pool:
            for line in order:
                if amount == 0:
                    break
                take = min(amount, max(line.remaining, 0))
                if take:
                    _add(line, pid, take)
                    amount -= take
            rep.unallocated += amount

        if rep.unallocated:
            rep.notes.append(f"ПЕРЕПЛАТА: выплачено на {fmt_money(rep.unallocated)} больше цены действующих работ")
        if rep.paid + rep.queued > rep.due and not rep.unallocated:
            rep.notes.append(
                f"с учётом очереди будет выплачено {fmt_money(rep.paid + rep.queued)} "
                f"при цене действующих работ {fmt_money(rep.due)} - переплата {fmt_money(rep.paid + rep.queued - rep.due)}")
        reports.append(rep)
    return reports, warnings


# ---------------------------------------------------------------- вывод

WORK_HEADERS = ["Договор", "Подрядчик", "Работа", "Название", "Статус на дату", "Цена",
                "Выплачено", "Осталось оплатить", "Из каких платежей"]
CONTRACT_HEADERS = ["Договор", "Подрядчик", "Сумма договора", "Отменено работ на",
                    "К оплате за действующие работы", "Выплачено", "Осталось оплатить",
                    "В очереди на оплату", "Останется после очереди", "Переплата (с учётом очереди)",
                    "Возвращено подрядчиком (справочно)"]


def work_rows(reports):
    for r in reports:
        for l in r.lines:
            state = STATE_RU[l.state]
            if l.state == DONE:
                state += f" {fmt_date(l.work.completed_at)}"
            elif l.state == CANCELLED:
                state += f" {fmt_date(l.work.cancelled_at)}"
            yield [r.contract.contract_id, r.contract.contractor, l.work.work_id, l.work.title,
                   state, fmt_money(l.work.price), fmt_money(l.paid), fmt_money(l.remaining),
                   "; ".join(l.sources)]


def contract_rows(reports):
    for r in reports:
        yield [r.contract.contract_id, r.contract.contractor, fmt_money(r.contract.amount),
               fmt_money(r.cancelled_sum), fmt_money(r.due), fmt_money(r.paid),
               fmt_money(max(r.remaining, 0)), fmt_money(r.queued),
               fmt_money(max(r.remaining - r.queued, 0)),
               fmt_money(max(r.paid + r.queued - r.due, 0)), fmt_money(r.refunded)]


def table(headers, rows) -> str:
    rows = [list(map(str, r)) for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    num = {i for i, h in enumerate(headers) if i and rows and all(re.fullmatch(r"-?[\d ]+,\d\d", r[i]) for r in rows)}

    def fmt(r):
        return " | ".join(c.rjust(widths[i]) if i in num else c.ljust(widths[i]) for i, c in enumerate(r))
    sep = "-+-".join("-" * w for w in widths)
    return "\n".join([fmt(headers), sep, *map(fmt, rows)])


def print_text(reports, warnings, on: date, out=sys.stdout):
    print(f"ОТЧЁТ ПО ОПЛАТЕ РАБОТ НА {fmt_date(on)}\n", file=out)
    print("Работы:", file=out)
    print(table(WORK_HEADERS, work_rows(reports)), file=out)
    print("\nИтоги по договорам:", file=out)
    print(table(CONTRACT_HEADERS, contract_rows(reports)), file=out)
    notes = [f"{r.contract.contract_id}: {n}" for r in reports for n in r.notes]
    if notes or warnings:
        print("\nОбратить внимание:", file=out)
        for n in notes + warnings:
            print(f"  - {n}", file=out)


def write_csv(path: Path, reports, warnings, on: date):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([f"Отчёт по оплате работ на {fmt_date(on)}"])
        w.writerow([])
        w.writerow(WORK_HEADERS)
        w.writerows(work_rows(reports))
        w.writerow([])
        w.writerow(CONTRACT_HEADERS)
        w.writerows(contract_rows(reports))
        notes = [f"{r.contract.contract_id}: {n}" for r in reports for n in r.notes] + warnings
        if notes:
            w.writerow([])
            w.writerow(["Обратить внимание"])
            w.writerows([[n] for n in notes])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Отчёт по оплате работ подрядчиков на дату")
    ap.add_argument("--date", required=True, help="дата отчёта: 2026-11-10 или 10.11.2026")
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"), help="папка с CSV (по умолчанию ./data)")
    ap.add_argument("--csv", help="дополнительно сохранить отчёт в CSV (для Excel, разделитель ';')")
    args = ap.parse_args(argv)

    on = parse_date(args.date)
    reports, warnings = build_report(*load(Path(args.data)), on)
    print_text(reports, warnings, on)
    if args.csv:
        write_csv(Path(args.csv), reports, warnings, on)
        print(f"\nCSV сохранён: {args.csv}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
