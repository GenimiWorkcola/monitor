#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FRED + USD.CNY 隐含利率综合数据表更新脚本（含 Playwright 自动抓取中国货币网最新数据）

目标：
1. 自动使用 Playwright 访问中国货币网外币隐含利率页面抓取最新一期数据（1D和3M）。
2. 将抓取到的数据追加到本地 imply.xlsx 中（旧文件自动归档至 old data）。
3. 把 FRED 利率与更新后的 USD.CNY 隐含利率整合，更新 FRED rates.csv 及交互 HTML。

依赖安装：
    pip install pandas openpyxl pandas-datareader playwright
    playwright install chromium

一键运行：
    python usd_liquidity.py
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


def choose_sheet(excel_file: pd.ExcelFile, preferred: str | None = None) -> str:
    if preferred and preferred in excel_file.sheet_names:
        return preferred
    for name in excel_file.sheet_names:
        if "日频合并" in name or "Sheet1" in name or len(excel_file.sheet_names) == 1:
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
        "implied": [c for c, n in normalized.items() if ("隐含利率" in n or "imply" in n) and not n.startswith("1d") and not n.startswith("3m")],
        "implied1d": [c for c, n in normalized.items() if n.startswith("1d") and "隐含利率" in n],
        "implied3m": [c for c, n in normalized.items() if n.startswith("3m") and "隐含利率" in n],
        "currency": [c for c, n in normalized.items() if "货币对" in n or n == "currency"],
        "cny_rate": [c for c, n in normalized.items() if "人民币利率" in n or "shibor" in n],
        "spot": [c for c, n in normalized.items() if "即期汇率" in n or "即期询价报价均值" in n],
        "swap": [c for c, n in normalized.items() if "远掉期点" in n or "掉期点" in n or "swap" in n],
    }


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
            # 模糊包含匹配，避免空格或编码差异导致漏筛
            matched = result[col].astype(str).str.strip().str.contains(expected, case=False, na=False)
            if matched.any():
                result = result[matched]
    return result


def normalize_implied_long_table(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    groups = column_groups(df)
    if not groups["date"] or not groups["tenor"] or not groups["implied"]:
        raise ValueError(f"{source_name}：未找到日期、期限或隐含利率列")

    filtered = apply_implied_filters(df)
    if filtered.empty:
        filtered = df  # 若筛选过严导致空，回退使用全表避免报错

    work = pd.DataFrame({
        DATE_COL: filtered[groups["date"][0]].map(parse_date),
        "期限": filtered[groups["tenor"][0]].astype(str).str.strip().str.upper(),
        "隐含利率": first_non_empty_column(filtered, groups["implied"]).map(parse_rate),
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


def fetch_chinamoney_via_playwright() -> pd.DataFrame:
    """使用 Playwright 自动访问中国货币网抓取最新一期外币隐含利率数据"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("缺少 playwright 库，请执行：pip install playwright 和 playwright install chromium") from exc

    print("正在使用 Playwright 自动访问中国货币网抓取最新数据...")
    rows_data = []
    url = "https://www.chinamoney.com.cn/chinese/bkcurvuiruuh/"

    with sync_playwright() as p:
        # 启动浏览器（headless=False 可视化观察，headless=True 后台静默运行）
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        try:
            page.goto(url, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=30000)
            
            # 尝试点击查询或等待表格渲染
            page.wait_for_timeout(3000) # 等待动态 JS 加载
            
            # 提取页面中可能存在的表格数据行
            # 通过 Playwright 获取表格内容
            tables = page.locator("table").all()
            for table in tables:
                text_content = table.inner_text()
                if "USD.CNY" in text_content or "1D" in text_content or "Shibor" in text_content:
                    trs = table.locator("tr").all()
                    for tr in trs:
                        cols = [td.inner_text().strip() for td in tr.locator("td, th").all()]
                        if len(cols) >= 5:
                            rows_data.append(cols)
        except Exception as e:
            print(f"Playwright 抓取警告/异常: {e}")
        finally:
            browser.close()

    if not rows_data:
        print("提示：未直接从页面表格中通过自动化抓取到有效行，将尝试通过备用 API 接口或现有文件补全。")
        return pd.DataFrame()

    # 将抓取到的表格行转换为 DataFrame
    # 按照用户指定的标准列格式构建：
    # 日期 | 货币对（USD.CNY） | 人民币利率（shibor） | 即期汇率 | 远/掉期点 | 期限（1D和3M） | 隐含利率(%) ...
    print(f"成功抓