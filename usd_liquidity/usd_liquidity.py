#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FRED + USD.CNY 隐含利率综合数据表更新脚本（支持每日 1.xlsx 增量合并与去重）

目标：
1. 将每日新增的 1.xlsx 合并到 imply.xlsx 中，去重并覆盖保存 imply.xlsx。
2. 基于更新后的 imply.xlsx 与 FRED 利率（SOFR、IORB、30天AA非金融CP贴现率）
   整合为标准宽表，更新 FRED rates.csv 并同步刷新 usd_liquidity.html。

用法：
    python usd_liquidity_update.py
    python usd_liquidity_update.py --daily 1.xlsx --implied imply.xlsx
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

STANDARD_SHEET = "日频合并数据"
DATE_COL = "日期"
SOFR_COL = "SOFR（%）"
IORB_COL = "IOER/IORB（%）"
CP_COL = "30天AA非金融CP贴现率（%）"
IMP1D_COL = "1D隐含利率（%）"
IMP3M_COL = "3M隐含利率（%）"
STANDARD_COLUMNS = [DATE_COL, SOFR_COL, IORB_COL, CP_COL, IMP1D_COL, IMP3M_COL]
RATE_COLUMNS = STANDARD_COLUMNS[1:]
FRED_SERIES = {"SOFR": SOFR_COL, "IORB": IORB_COL, "RIFSPPNAAD30NB": CP_COL}
TENOR_COLUMNS = {"1D": IMP1D_COL, "3M": IMP3M_COL}

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_PATH = BASE_DIR / "FRED rates.csv"
DEFAULT_IMPLIED_PATH = BASE_DIR / "imply.xlsx"
DEFAULT_DAILY_PATH = BASE_DIR / "1.xlsx"
DEFAULT_HTML_PATH = BASE_DIR / "usd_liquidity.html"
OLD_DIR = BASE_DIR / "old data"


def normalize_header(value: object) -> str:
    text = str(value or "").strip().lower()
    for ch in [" ", "\t", "\n", "_", "-", "/", "（", "）", "(", ")", "%", "％"]:
        text = text.replace(ch, "")
    return text


def parse_date(value: object) -> pd.Timestamp | pd.NaT:
    if value is None or pd.isna(value):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if isinstance(value, datetime):
        return pd.Timestamp(value.date())
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        try:
            return pd.Timestamp(value).normalize()
        except Exception:
            return pd.NaT
    if isinstance(value, (int, float)) and 20000 <= float(value) <= 80000:
        return pd.Timestamp("1899-12-30") + pd.to_timedelta(float(value), unit="D")
    text = str(value).strip()
    if not text or text.upper() in {"ND", "N/A", "NA", "NULL", "NONE", "--", "—"}:
        return pd.NaT
    text = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed) or parsed.year < 1900:
        return pd.NaT
    return parsed.normalize()


def parse_rate(value: object) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.upper() in {"ND", "N/A", "NA", "NULL", "NONE", "--", "—", "."}:
        return float("nan")
    text = text.replace("%", "").replace("％", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return float("nan")


def choose_sheet(excel_file: pd.ExcelFile, preferred: str | None = None) -> str:
    if preferred and preferred in excel_file.sheet_names:
        return preferred
    for name in excel_file.sheet_names:
        if "日频合并" in name:
            return name
    for name in excel_file.sheet_names:
        if "综合" in name:
            return name
    return excel_file.sheet_names[0]


def read_csv_with_fallback(path: Path) -> pd.DataFrame:
    for encoding in ["utf-8-sig", "utf-8", "gb18030", "gbk"]:
        try:
            return pd.read_csv(path, dtype=object, encoding=encoding, sep=None, engine="python")
        except Exception:
            continue
    return pd.read_csv(path, dtype=object, sep=None, engine="python")


def read_raw_table(path: Path, preferred_sheet: str | None = None) -> tuple[pd.DataFrame, str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv_with_fallback(path), "CSV"
    if suffix not in {".xlsx", ".xlsm", ".xls"}:
        raise ValueError(f"不支持的文件格式：{path.suffix}，请使用 .xlsx/.xls/.csv")
    excel = pd.ExcelFile(path)
    sheet = choose_sheet(excel, preferred_sheet)
    return pd.read_excel(excel, sheet_name=sheet, dtype=object), sheet


def first_non_empty_column(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    if not columns:
        return pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")
    return df[columns].apply(
        lambda row: next((value for value in row if not pd.isna(value)), float("nan")),
        axis=1,
    )


def column_groups(df: pd.DataFrame) -> dict[str, list[object]]:
    normalized = {col: normalize_header(col) for col in df.columns}
    return {
        "date": [c for c, n in normalized.items() if n in {"日期", "date", "time", "交易日", "交易日期", "数据日期"}],
        "sofr": [c for c, n in normalized.items() if "sofr" in n],
        "iorb": [c for c, n in normalized.items() if "iorb" in n or "ioer" in n],
        "cp": [c for c, n in normalized.items() if "dcpf1m" in n or "rifsppnaad30nb" in n or "cp贴现率" in n or "商业票据贴现率" in n or ("30天" in n and "cp" in n)],
        "tenor": [c for c, n in normalized.items() if n in {"期限", "tenor"}],
        "implied": [c for c, n in normalized.items() if "隐含利率" in n and not n.startswith("1d") and not n.startswith("3m")],
        "implied1d": [c for c, n in normalized.items() if n.startswith("1d") and "隐含利率" in n],
        "implied3m": [c for c, n in normalized.items() if n.startswith("3m") and "隐含利率" in n],
        "currency": [c for c, n in normalized.items() if n == "货币对"],
        "cny_rate": [c for c, n in normalized.items() if n == "人民币利率"],
        "spot": [c for c, n in normalized.items() if n == "即期汇率"],
        "swap": [c for c, n in normalized.items() if n == "远掉期点"],
    }


def merge_and_deduplicate_implied(base_implied_path: Path, daily_path: Path) -> pd.DataFrame:
    """读取 imply.xlsx 与每日新增的 1.xlsx，合并、去重并写回 imply.xlsx。"""
    print(f"正在加载基础隐含利率表: {base_implied_path.name}")
    base_raw, _ = read_raw_table(base_implied_path)
    
    dfs = [base_raw]
    if daily_path.exists():
        print(f"检测到每日新增数据表: {daily_path.name}，正在进行合并与去重……")
        daily_raw, _ = read_raw_table(daily_path)
        dfs.append(daily_raw)
    else:
        print(f"未检测到新增数据表 {daily_path.name}，仅使用现有 imply.xlsx。")

    combined_raw = pd.concat(dfs, ignore_index=True)
    combined_raw = combined_raw.dropna(axis=0, how="all").dropna(axis=1, how="all")

    before_len = len(combined_raw)
    combined_raw = combined_raw.drop_duplicates(keep="last")
    print(f"去重完成：合并前 {before_len} 行，去重后 {len(combined_raw)} 行。")

    temp_path = base_implied_path.with_name(base_implied_path.stem + ".tmp.xlsx")
    combined_raw.to_excel(temp_path, index=False)
    os.replace(temp_path, base_implied_path)
    print(f"imply.xlsx 已成功更新并保存：{base_implied_path}")

    return combined_raw


def normalize_wide_table(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    groups = column_groups(df)
    if not groups["date"]:
        raise ValueError(f"{source_name}：未找到日期列")

    out = pd.DataFrame(index=df.index)
    out[DATE_COL] = first_non_empty_column(df, groups["date"]).map(parse_date)
    out[SOFR_COL] = first_non_empty_column(df, groups["sofr"]).map(parse_rate)
    out[IORB_COL] = first_non_empty_column(df, groups["iorb"]).map(parse_rate)
    out[CP_COL] = first_non_empty_column(df, groups["cp"]).map(parse_rate)
    out[IMP1D_COL] = first_non_empty_column(df, groups["implied1d"]).map(parse_rate)
    out[IMP3M_COL] = first_non_empty_column(df, groups["implied3m"]).map(parse_rate)
    out = out.dropna(subset=[DATE_COL])
    out = out.loc[out[RATE_COLUMNS].notna().any(axis=1), STANDARD_COLUMNS]
    out = out.sort_values(DATE_COL).drop_duplicates(DATE_COL, keep="last")
    return out.reset_index(drop=True)


def apply_implied_filters(df: pd.DataFrame) -> pd.DataFrame:
    groups = column_groups(df)
    result = df.copy()
    filter_rules = [
        ("currency", "USD.CNY"),
        ("cny_rate", "Shibor"),
        ("spot", "即期询价报价均值"),
        ("swap", "掉期点"),
    ]
    for key, expected in filter_rules:
        if groups[key]:
            col = groups[key][0]
            result = result[result[col].astype(str).str.strip().eq(expected)]
    return result


def normalize_implied_long_table(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    groups = column_groups(df)
    if not groups["date"] or not groups["tenor"] or not groups["implied"]:
        raise ValueError(f"{source_name}：未找到日期、期限或隐含利率列")

    filtered = apply_implied_filters(df)
    if filtered.empty:
        raise ValueError(f"{source_name}：按 USD.CNY / Shibor / 即期询价报价均值 / 掉期点 筛选后无数据")

    work = pd.DataFrame({
        DATE_COL: filtered[groups["date"][0]].map(parse_date),
        "期限": filtered[groups["tenor"][0]].astype(str).str.strip().str.upper(),
        "隐含利率": filtered[groups["implied"][0]].map(parse_rate),
    })
    work = work.dropna(subset=[DATE_COL, "隐含利率"])
    work = work[work["期限"].isin(TENOR_COLUMNS.keys())]
    if work.empty:
        raise ValueError(f"{source_name}：未找到 1D 或 3M 隐含利率")

    work = work.sort_values([DATE_COL, "期限"]).drop_duplicates([DATE_COL, "期限"], keep="last")
    pivot = work.pivot(index=DATE_COL, columns="期限", values="隐含利率").reset_index()
    out = pd.DataFrame({
        DATE_COL: pivot[DATE_COL],
        IMP1D_COL: pivot.get("1D", pd.Series(index=pivot.index, dtype="float64")),
        IMP3M_COL: pivot.get("3M", pd.Series(index=pivot.index, dtype="float64")),
    })
    for col in [SOFR_COL, IORB_COL, CP_COL]:
        out[col] = float("nan")
    return out[STANDARD_COLUMNS]


def load_any_table(path: Path, preferred_sheet: str | None = None) -> tuple[pd.DataFrame, str, str]:
    raw, sheet = read_raw_table(path, preferred_sheet)
    groups = column_groups(raw)
    if groups["tenor"] and groups["implied"]:
        return normalize_implied_long_table(raw, f"{path.name} / {sheet}"), sheet, "隐含利率长表"
    table_type = "综合宽表" if groups["implied1d"] or groups["implied3m"] else "FRED宽表"
    return normalize_wide_table(raw, f"{path.name} / {sheet}"), sheet, table_type


def fetch_fred(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    try:
        import pandas_datareader.data as web
    except ImportError as exc:
        raise RuntimeError("缺少 pandas-datareader，请先执行：pip install pandas-datareader") from exc
    raw = web.DataReader(list(FRED_SERIES.keys()), "fred", start, end)
    if raw.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    raw.index = pd.to_datetime(raw.index).normalize()
    raw = raw.rename(columns=FRED_SERIES).reset_index()
    date_column = "DATE" if "DATE" in raw.columns else raw.columns[0]
    raw = raw.rename(columns={date_column: DATE_COL})
    raw[DATE_COL] = pd.to_datetime(raw[DATE_COL]).dt.normalize()
    for col in [IMP1D_COL, IMP3M_COL]:
        raw[col] = float("nan")
    return raw[STANDARD_COLUMNS]


def merge_by_date(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    result: pd.DataFrame | None = None
    for frame in frames:
        if frame is None or frame.empty:
            continue
        incoming = frame.copy()
        incoming[DATE_COL] = pd.to_datetime(incoming[DATE_COL]).dt.normalize()
        incoming = incoming.sort_values(DATE_COL).drop_duplicates(DATE_COL, keep="last").set_index(DATE_COL)
        if result is None:
            result = incoming.copy()
            continue
        result = result.reindex(result.index.union(incoming.index))
        for col in RATE_COLUMNS:
            if col not in incoming.columns:
                incoming[col] = float("nan")
            if col not in result.columns:
                result[col] = float("nan")
            result[col] = incoming[col].combine_first(result[col])
    if result is None:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    return result.reset_index().sort_values(DATE_COL).reset_index(drop=True)[STANDARD_COLUMNS]


def latest_non_null(df: pd.DataFrame, col: str) -> tuple[str, float | None]:
    valid = df[[DATE_COL, col]].dropna()
    if valid.empty:
        return "—", None
    row = valid.iloc[-1]
    return row[DATE_COL].strftime("%Y-%m-%d"), float(row[col])


def write_output(df: pd.DataFrame, output_path: Path) -> None:
    temp_path = output_path.with_name(output_path.stem + ".tmp.csv")
    df.to_csv(temp_path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    os.replace(temp_path, output_path)
    print(f"基准 CSV 已覆盖更新：{output_path}（{len(df)} 行）")


def json_value(value: object) -> object:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        return round(value, 6)
    return value


def archive_file(path: Path) -> None:
    if not path.exists():
        return
    OLD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = OLD_DIR / f"{path.stem}_{stamp}{path.suffix}"
    counter = 1
    while target.exists():
        target = OLD_DIR / f"{path.stem}_{stamp}_{counter}{path.suffix}"
        counter += 1
    shutil.move(str(path), str(target))
    print(f"旧页面已移至：{target}")


def replace_html_constant(html: str, pattern: str, replacement: str, label: str) -> str:
    new_html, count = re.subn(pattern, lambda _: replacement, html, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f"交互页面中未找到 {label}，页面结构可能已变化")
    return new_html


def update_html(df: pd.DataFrame, html_path: Path) -> None:
    if not html_path.exists():
        raise FileNotFoundError(f"找不到交互页面模板：{html_path}")

    fred_source = df.loc[df[[SOFR_COL, IORB_COL, CP_COL]].notna().any(axis=1)]
    implied_source = df.loc[df[[IMP1D_COL, IMP3M_COL]].notna().any(axis=1)]
    fred_rows = [
        {"date": json_value(row[DATE_COL]), "sofr": json_value(row[SOFR_COL]),
         "iorb": json_value(row[IORB_COL]), "cp": json_value(row[CP_COL])}
        for _, row in fred_source.iterrows()
    ]
    implied_rows = [
        {"date": json_value(row[DATE_COL]), "implied1d": json_value(row[IMP1D_COL]),
         "implied3m": json_value(row[IMP3M_COL])}
        for _, row in implied_source.iterrows()
    ]
    fred_json = json.dumps(fred_rows, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    implied_json = json.dumps(implied_rows, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    html = html_path.read_text(encoding="utf-8")
    html = replace_html_constant(
        html, r"const builtInFredRows = \[.*?\];",
        "const builtInFredRows = " + fred_json + ";", "builtInFredRows"
    )
    html = replace_html_constant(
        html, r"const builtInImpliedRows = \[.*?\];",
        "const builtInImpliedRows = " + implied_json + ";", "builtInImpliedRows"
    )
    archive_file(html_path)
    temp_path = html_path.with_name(html_path.stem + ".tmp.html")
    temp_path.write_text(html, encoding="utf-8")
    os.replace(temp_path, html_path)
    print(f"交互页面已更新：{html_path}（FRED {len(fred_rows)} 行，隐含利率 {len(implied_rows)} 行）")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="一键合并每日 1.xlsx 到 imply.xlsx，并更新综合数据与 HTML")
    parser.add_argument("base", nargs="?", default=str(DEFAULT_BASE_PATH),
                        help="基准 FRED CSV 表")
    parser.add_argument("--implied", default=str(DEFAULT_IMPLIED_PATH),
                        help="隐含利率基础表 imply.xlsx")
    parser.add_argument("--daily", default=str(DEFAULT_DAILY_PATH),
                        help="每日新增表 1.xlsx")
    parser.add_argument("--html", default=str(DEFAULT_HTML_PATH),
                        help="交互页面模板 usd_liquidity.html")
    parser.add_argument("--start", help="FRED 拉取起始日期")
    parser.add_argument("--end", help="更新截止日期 YYYY-MM-DD")
    parser.add_argument("--no-fetch", action="store_true", help="不联网拉取 FRED")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_path = Path(args.base).expanduser().resolve()
    implied_path = Path(args.implied).expanduser().resolve()
    daily_path = Path(args.daily).expanduser().resolve()
    html_path = Path(args.html).expanduser().resolve()

    if not base_path.exists():
        print(f"错误：找不到基准表：{base_path}", file=sys.stderr)
        return 2
    if not implied_path.exists():
        print(f"错误：找不到隐含利率表：{implied_path}", file=sys.stderr)
        return 2

    try:
        # 第一步：把每天新增的 1.xlsx 合并到 imply.xlsx 去重并保存
        merge_and_deduplicate_implied(implied_path, daily_path)

        # 第二步：基于更新后的 imply.xlsx 与基础表执行后续任务
        base_df, base_sheet, base_type = load_any_table(base_path, None)
        implied_df, implied_sheet, implied_type = load_any_table(implied_path, None)

        available_dates = [base_df[DATE_COL].min()]
        if not implied_df.empty:
            available_dates.append(implied_df[DATE_COL].min())
        start = parse_date(args.start) if args.start else min(available_dates)
        end = parse_date(args.end) if args.end else pd.Timestamp.today().normalize()

        fred_df = pd.DataFrame(columns=STANDARD_COLUMNS)
        if not args.no_fetch:
            print(f"正在从 FRED 获取 {start.date()} 至 {end.date()} 的最新数据……")
            fred_df = fetch_fred(start, end)
        else:
            print("已跳过 FRED 联网拉取（--no-fetch）")

        combined = merge_by_date([base_df, implied_df, fred_df])
        combined = combined[(combined[DATE_COL] >= start) & (combined[DATE_COL] <= end)].reset_index(drop=True)
        if combined.empty:
            raise ValueError("合并后没有可输出的数据行")

        write_output(combined, base_path)
        update_html(combined, html_path)

        print("\n所有任务执行成功：")
        print(f"1. 每日增量表 {daily_path.name} 已成功合并并去重保存至 {implied_path.name}")
        print(f"2. 综合基准表 {base_path.name} 与网页端已同步更新完毕。")
        return 0
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())