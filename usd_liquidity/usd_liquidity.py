#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FRED + USD.CNY 隐含利率综合数据表更新脚本

目标：
把 FRED 利率（SOFR、IORB、30天AA金融CP贴现率）与 USD.CNY 1D/3M 隐含利率
整合为一张可直接拖入交互 HTML 的标准宽表。

新增功能：
    默认先用 Playwright 打开中国货币网“外币隐含利率”页面，抓取最新发布日期
    的 USD.CNY / Shibor / 即期询价报价均值 / 掉期点 数据（仅 1D、3M 两个期限），
    增量追加到 imply.xlsx 末尾（追加前自动去重同日期旧行），并把旧 imply.xlsx
    归档到 old data 文件夹（同名文件直接覆盖）。抓取失败不影响后续更新。

标准输出文件：FRED rates.csv（覆盖原文件）
标准输出列：
    日期
    SOFR（%）
    IOER/IORB（%）
    30天AA金融CP贴现率（%）
    1D隐含利率（%）
    3M隐含利率（%）

依赖安装：
    pip install pandas openpyxl pandas-datareader playwright
    playwright install chromium

一键运行（推荐）：
    python usd_liquidity_update.py

默认读取脚本同目录：
    FRED rates.csv
    imply.xlsx
    usd_liquidity.html

默认输出：
    覆盖更新同目录的 FRED rates.csv（保留标准六列）
    同时更新 usd_liquidity.html 的内嵌数据，旧页面归档至 old data。
    增量更新 imply.xlsx，旧文件归档至 old data。
    不再生成带日期的综合 Excel 文件。

可选参数：
    python usd_liquidity_update.py --no-fetch       # 不联网拉取 FRED，只合并本地数据
    python usd_liquidity_update.py --no-scrape      # 不用 Playwright 抓取中国货币网

重要口径：
- 新隐含利率表会自动筛选 USD.CNY / Shibor / 即期询价报价均值 / 掉期点；
- 当前交互页面使用 1D 和 3M 两个期限；
- 日期升序、按日期去重；
- 空值保持为空，不前向填充、不插值；
- CP 平滑插值由交互 HTML 在浏览器端完成；
- 默认自动从 FRED 下载 SOFR、IORB、DCPF1M 最新数据。
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
CP_COL = "30天AA金融CP贴现率（%）"
IMP1D_COL = "1D隐含利率（%）"
IMP3M_COL = "3M隐含利率（%）"
STANDARD_COLUMNS = [DATE_COL, SOFR_COL, IORB_COL, CP_COL, IMP1D_COL, IMP3M_COL]
RATE_COLUMNS = STANDARD_COLUMNS[1:]
FRED_SERIES = {"SOFR": SOFR_COL, "IORB": IORB_COL, "DCPF1M": CP_COL}
TENOR_COLUMNS = {"1D": IMP1D_COL, "3M": IMP3M_COL}

# ---- 中国货币网“外币隐含利率”抓取相关常量 ----
CHINAMONEY_URL = "https://www.chinamoney.com.cn/chinese/bkcurvuiruuh/"
# imply.xlsx 的标准列（注意第 4、9 列同名“即期汇率”，与现有文件保持一致）
IMPLIED_HEADER = [
    "日期", "货币对", "人民币利率", "即期汇率", "远/掉期点",
    "期限", "隐含利率(%)", "人民币利率(%)", "即期汇率", "远/掉期点(Pips)",
]
IMPLIED_TENORS = ("1D", "3M")          # 只保留这两个期限
# 行内列位置（0 起）
POS_DATE, POS_PAIR, POS_CNYRATE, POS_SPOTTYPE, POS_SWAPTYPE = 0, 1, 2, 3, 4
POS_TENOR, POS_IMPLIED, POS_CNYRATE_VAL, POS_SPOT_VAL, POS_SWAP_VAL = 5, 6, 7, 8, 9
IMPLIED_FILTER_RULES = [
    (POS_PAIR, "USD.CNY"),
    (POS_CNYRATE, "Shibor"),
    (POS_SPOTTYPE, "即期询价报价均值"),
    (POS_SWAPTYPE, "掉期点"),
]

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_PATH = BASE_DIR / "FRED rates.csv"
DEFAULT_IMPLIED_PATH = BASE_DIR / "imply.xlsx"
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


def date_key(value: object) -> str:
    """把任意日期值统一成 YYYY-MM-DD 字符串，无法解析时返回空串。"""
    ts = parse_date(value)
    return "" if pd.isna(ts) else ts.strftime("%Y-%m-%d")


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
            return pd.read_csv(path, dtype=object, encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, dtype=object)


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
        "cp": [c for c, n in normalized.items() if "dcpf1m" in n or "cp贴现率" in n or "商业票据贴现率" in n or ("30天" in n and "cp" in n)],
        "tenor": [c for c, n in normalized.items() if n in {"期限", "tenor"}],
        "implied": [c for c, n in normalized.items() if "隐含利率" in n and not n.startswith("1d") and not n.startswith("3m")],
        "implied1d": [c for c, n in normalized.items() if n.startswith("1d") and "隐含利率" in n],
        "implied3m": [c for c, n in normalized.items() if n.startswith("3m") and "隐含利率" in n],
        "currency": [c for c, n in normalized.items() if n == "货币对"],
        "cny_rate": [c for c, n in normalized.items() if n == "人民币利率"],
        "spot": [c for c, n in normalized.items() if n == "即期汇率"],
        "swap": [c for c, n in normalized.items() if n == "远掉期点"],
    }


def normalize_wide_table(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    """读取综合宽表或 FRED 宽表。"""
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
    """按隐含利率 skill 的筛选规则处理原始长表。"""
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
    """把外币隐含利率长表透视为 1D/3M 两列。"""
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


# ---------------------------------------------------------------------------
# 中国货币网：Playwright 抓取最新一期外币隐含利率并增量追加到 imply.xlsx
# ---------------------------------------------------------------------------

def fetch_chinamoney_rows() -> list[list[str]]:
    """用 Playwright 打开中国货币网“外币隐含利率”页面，结构化读取目标表格。

    返回表格全部行（每行是单元格文本列表，首行为表头）；未找到目标表格返回空列表。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("缺少 playwright 库，请执行：pip install playwright 以及 playwright install chromium") from exc

    print("正在使用 Playwright 访问中国货币网抓取最新外币隐含利率……")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            locale="zh-CN",
        )
        page = context.new_page()
        try:
            page.goto(CHINAMONEY_URL, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(3000)  # 等待动态表格渲染
            for table in page.locator("table").all():
                try:
                    rows = table.evaluate(
                        """(table) => {
                            const out = [];
                            for (const tr of table.querySelectorAll('tr')) {
                                const cells = Array.from(tr.querySelectorAll('td, th'))
                                    .map(c => (c.innerText || '').trim());
                                if (cells.length) out.push(cells);
                            }
                            return out;
                        }"""
                    )
                except Exception:
                    continue
                flat = " ".join(" ".join(r) for r in rows)
                if "货币对" in flat and "期限" in flat and "隐含利率" in flat:
                    print(f"成功定位目标表格，共读取 {len(rows)} 行。")
                    return rows
            print("提示：页面中未找到包含 货币对/期限/隐含利率 的目标表格。")
            return []
        finally:
            browser.close()


def parse_chinamoney_rows(rows: list[list[str]]) -> pd.DataFrame:
    """把抓取的原始行清洗成 imply.xlsx 标准格式的最新一期 1D/3M 数据。"""
    out = pd.DataFrame(columns=IMPLIED_HEADER)
    if not rows or len(rows) < 2:
        return out

    records: list[list[object]] = []
    for row in rows[1:]:  # 跳过表头
        cells = [str(c).strip() for c in row]
        if len(cells) <= POS_SWAP_VAL:
            continue
        # 固定列位置的筛选规则：USD.CNY / Shibor / 即期询价报价均值 / 掉期点
        if any(cells[pos] != expected for pos, expected in IMPLIED_FILTER_RULES):
            continue
        tenor = cells[POS_TENOR].upper()
        if tenor not in IMPLIED_TENORS:
            continue
        day = date_key(cells[POS_DATE])
        if not day:
            continue
        records.append([
            day,
            cells[POS_PAIR],
            cells[POS_CNYRATE],
            cells[POS_SPOTTYPE],
            cells[POS_SWAPTYPE],
            tenor,
            parse_rate(cells[POS_IMPLIED]),
            parse_rate(cells[POS_CNYRATE_VAL]),
            parse_rate(cells[POS_SPOT_VAL]),
            parse_rate(cells[POS_SWAP_VAL]),
        ])

    if not records:
        return out

    work = pd.DataFrame(records, columns=IMPLIED_HEADER)
    # 只保留页面上最新发布的那一个日期（例如今天更新后最新行为 11-09）
    latest_day = work[DATE_COL].max()
    work = work[work[DATE_COL] == latest_day]
    # 同一日期内 1D 排在 3M 前
    tenor_order = {tenor: i for i, tenor in enumerate(IMPLIED_TENORS)}
    work = work.sort_values(DATE_COL, key=lambda s: s.map(tenor_order))
    print(f"筛选结果：{latest_day}，{len(work)} 行（{', '.join(work['期限'])}）")
    return work.reset_index(drop=True)


def archive_implied_overwrite(path: Path) -> None:
    """把旧 imply.xlsx 移入 old data 文件夹；如已有同名文件则直接覆盖。"""
    if not path.exists():
        return
    OLD_DIR.mkdir(parents=True, exist_ok=True)
    target = OLD_DIR / path.name
    if target.exists():
        target.unlink()
    shutil.move(str(path), str(target))
    print(f"旧 imply 文件已归档至：{target}（同名文件已覆盖）")


def update_imply_file(implied_path: Path, new_rows: pd.DataFrame) -> pd.DataFrame:
    """读取现有 imply.xlsx，剔除与新增数据同日期旧行后追加，归档旧文件并保存新文件。"""
    if new_rows.empty:
        raise ValueError("没有可追加的新数据行")

    existing = pd.DataFrame(columns=IMPLIED_HEADER)
    if implied_path.exists():
        try:
            existing = pd.read_excel(implied_path, dtype=object)
            # pandas 读取时会把重复的“即期汇率”列改名成“即期汇率.1”，这里按位置改回标准列名
            if len(existing.columns) >= len(IMPLIED_HEADER):
                existing = existing.iloc[:, :len(IMPLIED_HEADER)].copy()
                existing.columns = IMPLIED_HEADER
        except Exception as exc:
            print(f"读取现有 imply.xlsx 失败（{exc}），将只保存本次抓取的数据。")
            existing = pd.DataFrame(columns=IMPLIED_HEADER)

    # 去掉日期无法解析的行（例如重复表头行），并剔除与新增数据同日期旧行，保证幂等
    existing = existing[existing[DATE_COL].map(date_key) != ""].copy()
    new_days = set(new_rows[DATE_COL].map(date_key))
    before = len(existing)
    existing = existing[~existing[DATE_COL].map(date_key).isin(new_days)].copy()
    if before - len(existing) > 0:
        print(f"已剔除同日期旧行 {before - len(existing)} 行。")

    combined = pd.concat([existing, new_rows], ignore_index=True)
    # 与现有文件保持一致：日期降序，同日 1D 在 3M 前
    tenor_order = {tenor: i for i, tenor in enumerate(IMPLIED_TENORS)}
    combined["_d"] = combined[DATE_COL].map(date_key)
    combined["_t"] = combined["期限"].map(tenor_order).fillna(99).astype(int)
    combined = combined.sort_values(["_d", "_t"], ascending=[False, True]).drop(columns=["_d", "_t"])

    archive_implied_overwrite(implied_path)
    temp_path = implied_path.with_name(implied_path.stem + ".tmp.xlsx")
    combined.to_excel(temp_path, index=False)
    os.replace(temp_path, implied_path)
    print(f"imply.xlsx 已增量更新：{implied_path}（共 {len(combined)} 行，新增 {len(new_rows)} 行）")
    return combined


def merge_by_date(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """后传入的数据优先；仅在后来数据非空时覆盖旧值。"""
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


def build_stats(df: pd.DataFrame) -> pd.DataFrame:
    labels = {
        SOFR_COL: ("SOFR", "SOFR"),
        IORB_COL: ("IOER/IORB", "IORB"),
        CP_COL: ("30天AA金融CP贴现率", "DCPF1M"),
        IMP1D_COL: ("USD.CNY 1D隐含利率", "1D"),
        IMP3M_COL: ("USD.CNY 3M隐含利率", "3M"),
    }
    rows = []
    for col, (label, code) in labels.items():
        valid = df[[DATE_COL, col]].dropna()
        latest_date, latest_value = latest_non_null(df, col)
        rows.append({
            "指标": label,
            "代码/期限": code,
            "最新非空日期": latest_date,
            "最新值（%）": latest_value,
            "区间首个非空日期": valid[DATE_COL].iloc[0].strftime("%Y-%m-%d") if not valid.empty else "—",
            "区间首个值（%）": float(valid[col].iloc[0]) if not valid.empty else None,
            "区间最低值（%）": float(valid[col].min()) if not valid.empty else None,
            "区间最高值（%）": float(valid[col].max()) if not valid.empty else None,
            "非空条数": int(valid[col].notna().sum()),
        })
    return pd.DataFrame(rows)


def build_series_info() -> pd.DataFrame:
    return pd.DataFrame([
        {"指标": "SOFR", "来源/代码": "FRED: SOFR", "说明": "Secured Overnight Financing Rate；节假日为空。", "Source Name": "FRED", "Source URL": "https://fred.stlouisfed.org/series/SOFR"},
        {"指标": "IOER/IORB", "来源/代码": "FRED: IORB", "说明": "Interest Rate on Reserve Balances；每日 7 天频率。", "Source Name": "FRED", "Source URL": "https://fred.stlouisfed.org/series/IORB"},
        {"指标": "30天AA金融CP贴现率", "来源/代码": "FRED: DCPF1M", "说明": "30-Day AA Financial Commercial Paper Interest Rate，Discount Basis；无报价日期为空。", "Source Name": "FRED", "Source URL": "https://fred.stlouisfed.org/series/DCPF1M"},
        {"指标": "1D隐含利率", "来源/代码": "USD.CNY / 1D", "说明": "来自中国货币网外币隐含利率页面，筛选 USD.CNY、Shibor、即期询价报价均值、掉期点，Playwright 自动抓取后增量存入 imply.xlsx。", "Source Name": "中国货币网", "Source URL": CHINAMONEY_URL},
        {"指标": "3M隐含利率", "来源/代码": "USD.CNY / 3M", "说明": "来自中国货币网外币隐含利率页面，筛选 USD.CNY、Shibor、即期询价报价均值、掉期点，Playwright 自动抓取后增量存入 imply.xlsx。", "Source Name": "中国货币网", "Source URL": CHINAMONEY_URL},
    ])


def write_output(df: pd.DataFrame, output_path: Path, log_rows: list[dict[str, object]]) -> None:
    """以 UTF-8 BOM CSV 原子覆盖 FRED rates.csv；不再生成综合 Excel。"""
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
    """以当前页面为模板，仅替换两组内嵌数据，保留样式、外链 JS 和交互逻辑。"""
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
    parser = argparse.ArgumentParser(description="一键更新 FRED + USD.CNY 综合数据表和 usd_liquidity.html")
    parser.add_argument("base", nargs="?", default=str(DEFAULT_BASE_PATH),
                        help="基准 FRED 表，默认读取脚本同目录的 FRED rates.csv")
    parser.add_argument("--implied", default=str(DEFAULT_IMPLIED_PATH),
                        help="隐含利率历史表，默认读写脚本同目录的 imply.xlsx")
    parser.add_argument("--html", default=str(DEFAULT_HTML_PATH),
                        help="交互页面模板，默认更新脚本同目录的 usd_liquidity.html")
    parser.add_argument("--sheet", default=None, help="指定读取的工作表")
    parser.add_argument("--start", help="FRED 拉取起始日期，默认取输入数据最早日期")
    parser.add_argument("--end", help="更新截止日期，默认今天，格式 YYYY-MM-DD")
    parser.add_argument("--no-fetch", action="store_true", help="不联网拉取 FRED，仅合并输入数据")
    parser.add_argument("--no-scrape", action="store_true", help="不用 Playwright 抓取中国货币网，直接沿用现有 imply.xlsx")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_path = Path(args.base).expanduser().resolve()
    implied_path = Path(args.implied).expanduser().resolve()
    html_path = Path(args.html).expanduser().resolve()
    if not base_path.exists():
        print(f"错误：找不到基准表：{base_path}", file=sys.stderr)
        return 2

    log_rows: list[dict[str, object]] = [
        {"项目": "运行时间", "内容": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        {"项目": "基准表", "内容": str(base_path)},
    ]

    # 第一步：Playwright 抓取中国货币网最新一期 1D/3M 数据，增量追加到 imply.xlsx
    if not args.no_scrape:
        try:
            raw_rows = fetch_chinamoney_rows()
            new_implied = parse_chinamoney_rows(raw_rows)
            if new_implied.empty:
                print("提示：未抓取到符合条件的新数据（USD.CNY / Shibor / 即期询价报价均值 / 掉期点 / 1D+3M），沿用现有 imply.xlsx。")
                log_rows.append({"项目": "中国货币网抓取", "内容": "未获取到符合条件的新数据，沿用现有 imply.xlsx"})
            else:
                days = ", ".join(sorted(set(new_implied[DATE_COL])))
                update_imply_file(implied_path, new_implied)
                log_rows.append({"项目": "中国货币网抓取", "内容": f"新增 {len(new_implied)} 行（日期 {days}）"})
        except Exception as exc:
            print(f"中国货币网抓取失败（不影响后续更新）：{exc}", file=sys.stderr)
            log_rows.append({"项目": "中国货币网抓取", "内容": f"失败：{exc}"})
    else:
        log_rows.append({"项目": "中国货币网抓取", "内容": "已跳过（--no-scrape）"})

    try:
        base_df, base_sheet, base_type = load_any_table(base_path, args.sheet)
        if base_df.empty:
            raise ValueError("基准表未识别到有效数据行")
        log_rows.append({"项目": "基准表类型", "内容": f"{base_type} / {base_sheet} / {len(base_df)}行"})

        implied_df = pd.DataFrame(columns=STANDARD_COLUMNS)
        if args.implied:
            if not implied_path.exists():
                raise FileNotFoundError(f"找不到隐含利率表：{implied_path}")
            implied_df, implied_sheet, implied_type = load_any_table(implied_path, None)
            log_rows.append({"项目": "隐含利率表", "内容": f"{implied_path.name} / {implied_type} / {implied_sheet} / {len(implied_df)}行"})

        available_dates = [base_df[DATE_COL].min()]
        if not implied_df.empty:
            available_dates.append(implied_df[DATE_COL].min())
        start = parse_date(args.start) if args.start else min(available_dates)
        end = parse_date(args.end) if args.end else pd.Timestamp.today().normalize()
        if pd.isna(start) or pd.isna(end):
            raise ValueError("无法识别 --start 或 --end 日期")

        fred_df = pd.DataFrame(columns=STANDARD_COLUMNS)
        if not args.no_fetch:
            print(f"正在从 FRED 获取 {start.date()} 至 {end.date()} 的 SOFR / IORB / DCPF1M……")
            fred_df = fetch_fred(start, end)
            log_rows.append({"项目": "FRED拉取区间", "内容": f"{start.date()} 至 {end.date()}"})
            log_rows.append({"项目": "FRED返回行数", "内容": len(fred_df)})
        else:
            log_rows.append({"项目": "FRED拉取", "内容": "已跳过（--no-fetch）"})

        # 优先级：基准表 < 新隐含利率表 < FRED官方表。
        # FRED 表只含 SOFR/IORB/CP，不会覆盖隐含利率列。
        combined = merge_by_date([base_df, implied_df, fred_df])
        combined = combined[(combined[DATE_COL] >= start) & (combined[DATE_COL] <= end)].reset_index(drop=True)
        if combined.empty:
            raise ValueError("合并后没有可输出的数据行")

        output_path = base_path  # 固定覆盖同目录的 FRED rates.csv

        log_rows.append({"项目": "输出行数", "内容": len(combined)})
        log_rows.append({"项目": "输出日期范围", "内容": f"{combined[DATE_COL].min().date()} 至 {combined[DATE_COL].max().date()}"})
        for col in RATE_COLUMNS:
            d, v = latest_non_null(combined, col)
            log_rows.append({"项目": f"{col}最新非空", "内容": f"{d} / {'' if v is None else f'{v:.2f}%'}"})
        log_rows.append({"项目": "输出文件", "内容": str(output_path)})
        log_rows.append({"项目": "交互页面", "内容": str(html_path)})
        write_output(combined, output_path, log_rows)
        update_html(combined, html_path)

        print("\nFRED rates.csv 与交互页面均已更新")
        print(f"输出文件：{output_path}")
        print(f"数据范围：{combined[DATE_COL].min().date()} 至 {combined[DATE_COL].max().date()}，共 {len(combined)} 行")
        for col in RATE_COLUMNS:
            d, v = latest_non_null(combined, col)
            print(f"{col}: 最新非空 {d}，{'' if v is None else f'{v:.2f}%'}")
        print("\n可直接将更新后的 FRED rates.csv 拖入交互 HTML 页面。")
        return 0
    except Exception as exc:
        print(f"更新失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
