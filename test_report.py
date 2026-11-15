"""Тесты: python -m unittest -v"""
import unittest
from datetime import date
from pathlib import Path

from report import (Contract, Payment, Work, build_report, fmt_money, load,
                    parse_money)

DATA = Path(__file__).parent / "data"


def by_work(reports):
    return {l.work.work_id: l for r in reports for l in r.lines}


def by_contract(reports):
    return {r.contract.contract_id: r for r in reports}


class Money(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_money("300000.00"), 30_000_000)
        self.assertEqual(parse_money("0.1"), 10)
        with self.assertRaises(ValueError):
            parse_money("1.005")

    def test_fmt(self):
        self.assertEqual(fmt_money(30_000_000), "300 000,00")
        self.assertEqual(fmt_money(-501), "-5,01")


class RealData(unittest.TestCase):
    def report(self, d):
        return build_report(*load(DATA), d)

    def test_on_2026_11_10(self):
        reports, warnings = self.report(date(2026, 11, 10))
        c, w = by_contract(reports), by_work(reports)
        self.assertEqual(warnings, [])
        d1, d2 = c["D-1"], c["D-2"]
        self.assertEqual((d1.due, d1.paid, d1.remaining, d1.queued, d1.refunded),
                         (50_000_000, 31_500_000, 18_500_000, 15_000_000, 5_000_000))
        self.assertEqual((d2.due, d2.paid, d2.remaining, d2.queued),
                         (9_000_000, 7_500_000, 1_500_000, 0))
        self.assertEqual(w["W-13"].state, "cancelled")
        self.assertEqual(w["W-23"].state, "planned")  # принята только 11.11
        self.assertEqual(w["W-14"].paid, 10_000_000)
        self.assertEqual(w["W-15"].paid, 1_500_000)
        self.assertEqual(w["W-23"].remaining, 1_500_000)

    def test_sum_of_works_equals_contract_totals(self):
        for d in (date(2026, 9, 1), date(2026, 9, 5), date(2026, 11, 10), date(2026, 12, 31)):
            for r in self.report(d)[0]:
                self.assertEqual(sum(l.paid for l in r.lines) + r.unallocated, r.paid, (d, r.contract.contract_id))

    def test_refund_counts_until_refunded(self):
        c = by_contract(self.report(date(2026, 9, 5))[0])
        self.assertEqual(c["D-1"].paid, 35_000_000)  # P-1 + P-2 (ещё не возвращён)

    def test_overpayment_after_p8(self):
        c = by_contract(self.report(date(2026, 11, 15))[0])
        self.assertEqual(c["D-2"].unallocated, 500_000)


class Edge(unittest.TestCase):
    def setUp(self):
        self.c = [Contract("C", "X", 20_000, date(2026, 1, 1))]
        self.w = [Work("W1", "C", "a", 10_000, "done", date(2026, 2, 1), None),
                  Work("W2", "C", "b", 10_000, "cancelled", None, date(2026, 2, 1))]

    def pay(self, pid, amount, work="", status="paid"):
        return Payment(pid, "C", work, "", amount, status, date(2026, 1, 5), date(2026, 1, 5), None)

    def test_linked_to_cancelled_goes_to_pool(self):
        reports, _ = build_report(self.c, self.w, [self.pay("P", 5_000, "W2")], date(2026, 3, 1))
        self.assertEqual(by_work(reports)["W1"].paid, 5_000)

    def test_linked_excess_goes_to_pool_then_overpayment(self):
        reports, _ = build_report(self.c, self.w, [self.pay("P", 15_000, "W1")], date(2026, 3, 1))
        self.assertEqual(by_work(reports)["W1"].paid, 10_000)
        self.assertEqual(reports[0].unallocated, 5_000)

    def test_contract_signed_after_date_skipped(self):
        reports, _ = build_report(self.c, self.w, [], date(2025, 12, 31))
        self.assertEqual(reports, [])


if __name__ == "__main__":
    unittest.main()
