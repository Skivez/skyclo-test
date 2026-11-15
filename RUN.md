# Как запустить

Нужен Python 3.9+, сторонние пакеты не нужны.

```bash
python report.py --date 2026-11-10
```

Параметры:
- `--date` - дата отчёта, `2026-11-10` или `10.11.2026` (обязательно);
- `--data` - папка с CSV, по умолчанию `data` рядом со скриптом;
- `--csv FILE` - дополнительно сохранить отчёт в CSV для Excel.

```bash
python report.py --date 10.11.2026 --csv report_2026-11-10.csv
```

Тесты:

```bash
python -m unittest -v
```

## Файлы

- `report.py` - программа;
- `test_report.py` - тесты;
- `DECISIONS.md` - принятые решения и допущения;
- `NOTE_FOR_ACCOUNTANT.md` - записка бухгалтеру;
- `report_2026-11-10.csv` - готовый отчёт на 10.11.2026.
