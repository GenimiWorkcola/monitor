# -*- coding: utf-8 -*-
"""
央行资金面数据与交互页面一键更新（支持R001/R007成交量比值双轴、OMO 1D/7D利率及自定义输入）。
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import shutil
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "liquidity_data.xlsx"
HTML_PATH = BASE_DIR / "liquidity.html"
OLD_DIR = BASE_DIR / "old data"
SHEET_NAME = "资金面数据"

OMO_BASE_URL = "https://www.cfets-nex.com.cn/Market/marketOverview/marketOverview/?PageIndex={}"
REPO_BASE_URL = "https://www.chinamoney.com.cn/dqs/rest/cm-u-dlrp/PrDlyBltnExcel"
MAX_PAGES = 40
RETRY = 5
TARGETS = ("R001", "R007", "DR001", "DR007")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
OMO_FIELDS = [
    "央行逆回购投放(亿)", "逆回购到期(亿)", "净投放(亿)", "资金面概况",
    "隔夜Shibor", "7天Shibor", "3个月Shibor", "OMO_1D", "OMO_7D"
]
REPO_FIELDS = [
    "开盘利率(%)", "收盘利率(%)", "加权利率(%)", "加权平均利率(利率债)(%)",
    "平均回购期限(天)", "成交笔数(笔)", "成交量(亿元)",
]
ALL_COLUMNS = (
    ["日期"] + OMO_FIELDS + ["OMO数据来源", "OMO来源链接"]
    + [f"{symbol}_{field}" for symbol in TARGETS for field in REPO_FIELDS]
    + ["回购数据来源", "回购来源链接", "R001_R007_成交量比值", "R001_DR001_价差BP"]
)

# 7天期逆回购利率的历次生效节点；新调整只需在此追加一行。
OMO_7D_STEPS = [
    (dt.date(2020, 1, 1), 2.50),
    (dt.date(2020, 2, 3), 2.40),
    (dt.date(2020, 3, 30), 2.20),
    (dt.date(2022, 1, 17), 2.10),
    (dt.date(2022, 8, 15), 2.00),
    (dt.date(2023, 6, 13), 1.90),
    (dt.date(2023, 8, 15), 1.80),
    (dt.date(2024, 7, 22), 1.70),
    (dt.date(2024, 9, 27), 1.50),
    (dt.date(2025, 5, 8), 1.40),
]


def get_omo_rates(day_str: str) -> tuple[float | None, float | None]:
    """返回 OMO_1D、OMO_7D；1D 指临时隔夜正回购利率（利率走廊下限）。"""
    day = dt.datetime.strptime(day_str, "%Y-%m-%d").date()
    effective_rates = [rate for effective, rate in OMO_7D_STEPS if day >= effective]
    if not effective_rates:
        return None, None
    rate_7d = effective_rates[-1]
    if day < dt.date(2024, 7, 8):
        return None, rate_7d  # 临时隔夜正、逆回购机制自该日起设立
    spread = 0.25 if day >= dt.date(2026, 6, 17) else 0.20
    return round(rate_7d - spread, 3), round(rate_7d, 3)


def timestamped_target(path: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = OLD_DIR / f"{path.stem}_{stamp}{path.suffix}"
    counter = 1
    while target.exists():
        target = OLD_DIR / f"{path.stem}_{stamp}_{counter}{path.suffix}"
        counter += 1
    return target


def archive_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    OLD_DIR.mkdir(parents=True, exist_ok=True)
    target = timestamped_target(path)
    shutil.move(str(path), str(target))
    print(f"旧文件已移至：{target}")
    return target


def read_data() -> pd.DataFrame:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"未找到 {DATA_PATH.name}")
    df = pd.read_excel(DATA_PATH, sheet_name=SHEET_NAME)
    for column in ALL_COLUMNS:
        if column not in df.columns:
            df[column] = None
    df = df[ALL_COLUMNS]
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    return df.drop_duplicates(subset="日期", keep="last").sort_values("日期").reset_index(drop=True)


def merge_updates(df: pd.DataFrame, updates: list[dict]) -> pd.DataFrame:
    records = {row["日期"]: row for row in df.to_dict(orient="records")}
    for update in updates:
        row = records.setdefault(update["日期"], {"日期": update["日期"]})
        row.update(update)
    
    merged = pd.DataFrame([records[key] for key in sorted(records)])
    for column in ALL_COLUMNS:
        if column not in merged.columns:
            merged[column] = None
    merged = merged[ALL_COLUMNS]
    
    # 衍生指标：比值遇到零分母或缺失值时留空，禁止 Infinity／NaN 进入 JSON。
    r001_vol = pd.to_numeric(merged["R001_成交量(亿元)"], errors="coerce")
    r007_vol = pd.to_numeric(merged["R007_成交量(亿元)"], errors="coerce")
    ratio = r001_vol.div(r007_vol.mask(r007_vol.eq(0)))
    merged["R001_R007_成交量比值"] = ratio.where(np.isfinite(ratio)).round(3)

    r001_rate = pd.to_numeric(merged["R001_加权利率(%)"], errors="coerce")
    dr001_rate = pd.to_numeric(merged["DR001_加权利率(%)"], errors="coerce")
    merged["R001_DR001_价差BP"] = ((r001_rate - dr001_rate) * 100).round(3)

    # 自动补全历史OMO利率
    for idx, row in merged.iterrows():
        r1, r7 = get_omo_rates(row["日期"])
        if pd.isna(row.get("OMO_1D")):
            merged.at[idx, "OMO_1D"] = r1
        if pd.isna(row.get("OMO_7D")):
            merged.at[idx, "OMO_7D"] = r7

    for column in ["央行逆回购投放(亿)", "逆回购到期(亿)", "净投放(亿)"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce").round(0).astype("Int64")
    return merged


def write_data(df: pd.DataFrame) -> None:
    archive_file(DATA_PATH)
    temp_path = DATA_PATH.with_name(DATA_PATH.stem + ".tmp.xlsx")
    df.to_excel(temp_path, sheet_name=SHEET_NAME, index=False)
    os.replace(temp_path, DATA_PATH)
    print(f"合并数据表已更新：{DATA_PATH}（{len(df)} 个日期）")


def parse_page(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for block in soup.select(".has-date"):
        date_element = block.select_one(".date")
        if not date_element:
            continue
        day = date_element.get_text(strip=True)
        for box in block.select(".report-box"):
            h4, p = box.select_one("h4"), box.select_one("p")
            if h4 and p and "每日市场动态" in h4.get_text():
                out.append((day, p.get_text(strip=True)))
    return out


def parse_summary(text: str) -> dict:
    match = re.search(r"开展(.*?)逆回购操作", text)
    invest = sum(float(x) for x in re.findall(r"(\d+(?:\.\d+)?)亿", match.group(1))) if match else None
    match = re.search(r"逆回购到期(\d+(?:\.\d+)?)亿", text)
    mature = float(match.group(1)) if match else None
    net = invest - mature if invest is not None and mature is not None else None

    def shibor(name):
        found = re.search(name + r"品种报\s*(\d+\.\d+)\s*%", text)
        return float(found.group(1)) if found else None

    condition = ""
    for sentence in re.split(r"。", text):
        if "资金面" in sentence:
            condition = sentence
    condition = re.sub(r"^今日", "", condition).replace("资金面", "").strip("，,。 ")
    
    res = {
        "央行逆回购投放(亿)": invest, "逆回购到期(亿)": mature, "净投放(亿)": net,
        "资金面概况": condition, "隔夜Shibor": shibor("隔夜"),
        "7天Shibor": shibor("7天"), "3个月Shibor": shibor("3个月"),
        "OMO数据来源": "CFETS-NEX", "OMO来源链接": OMO_BASE_URL.format(1),
    }
    return res


def fetch_omo_page(session: requests.Session, page: int):
    for attempt in range(RETRY):
        try:
            response = session.get(OMO_BASE_URL.format(page), timeout=30)
            response.encoding = "utf-8"
        except requests.RequestException as exc:
            print(f"OMO 第 {page} 页第 {attempt + 1} 次请求异常：{exc}")
            time.sleep(3)
            continue
        if "aliyun_waf" in response.text:
            wait = 5 * (attempt + 1)
            print(f"OMO 第 {page} 页触发反爬，{wait} 秒后重试")
            time.sleep(wait)
            continue
        items = parse_page(response.text)
        if items:
            return items
        time.sleep(2)
    return None


def update_omo(df: pd.DataFrame) -> list[dict]:
    omo_dates = df.loc[df["隔夜Shibor"].notna() | df["净投放(亿)"].notna(), "日期"]
    if omo_dates.empty:
        raise ValueError("合并表中没有可用于增量判断的 OMO／Shibor 历史数据")
    latest = dt.datetime.strptime(omo_dates.max(), "%Y-%m-%d").date()
    session = requests.Session()
    session.headers.update(HEADERS)
    updates, stop, page = [], False, 1
    while not stop and page <= MAX_PAGES:
        items = fetch_omo_page(session, page)
        if items is None:
            break
        for day, text in items:
            current = dt.datetime.strptime(day, "%Y-%m-%d").date()
            if current <= latest:
                stop = True
                break
            r1, r7 = get_omo_rates(day)
            d_dict = parse_summary(text)
            d_dict["OMO_1D"] = r1
            d_dict["OMO_7D"] = r7
            updates.append({"日期": day, **d_dict})
        page += 1
        if not stop:
            time.sleep(1.5)
    return updates


def download_repo_day(session: requests.Session, day: date) -> list[dict]:
    url = f"{REPO_BASE_URL}?lang=cn&searchDate={day.isoformat()}&publishedTime=2200"
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            if not response.content.startswith(b"PK"):
                raise ValueError("服务器返回的不是有效 Excel 文件")
            workbook = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True, data_only=True)
            sheet = workbook[workbook.sheetnames[0]]
            found = {}
            for row in sheet.iter_rows(values_only=True):
                if len(row) >= 10 and row[1] in TARGETS:
                    found[row[1]] = row[2:9]
            workbook.close()
            return [
                {"日期": day.isoformat(), "品种": symbol, **dict(zip(REPO_FIELDS, found[symbol])),
                 "数据来源": "中国货币网", "来源链接": url}
                for symbol in TARGETS if symbol in found
            ]
        except (requests.RequestException, ValueError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{day} 回购数据下载失败：{last_error}")


def update_repo(df: pd.DataFrame) -> list[dict]:
    rate_columns = [f"{symbol}_加权利率(%)" for symbol in TARGETS]
    repo_dates = df.loc[df[rate_columns].notna().any(axis=1), "日期"]
    end = date.today()
    start = dt.datetime.strptime(repo_dates.max(), "%Y-%m-%d").date() if not repo_dates.empty else end - timedelta(days=13)
    if start > end:
        return []
    session = requests.Session()
    session.headers.update({"User-Agent": HEADERS["User-Agent"], "Referer": "https://www.chinamoney.com.cn/"})
    updates = []
    day = start
    while day <= end:
        try:
            daily = download_repo_day(session, day)
            if daily:
                wide = {"日期": day.isoformat(), "回购数据来源": "中国货币网", "回购来源链接": daily[0]["来源链接"]}
                for record in daily:
                    for field in REPO_FIELDS:
                        wide[f"{record['品种']}_{field}"] = record[field]
                updates.append(wide)
        except RuntimeError:
            pass
        day += timedelta(days=1)
    return updates


def json_value(value):
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (pd.Timestamp, dt.date, dt.datetime)):
        value = value.strftime("%Y-%m-%d")
    elif isinstance(value, float):
        return round(value, 4) if np.isfinite(value) else None
    return value


def replace_constant(html: str, pattern: str, replacement: str, label: str) -> str:
    new_html, count = re.subn(pattern, lambda _: replacement, html, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f"当前 liquidity.html 中未找到 {label}")
    return new_html


def regenerate_html(df: pd.DataFrame) -> None:
    if not HTML_PATH.exists():
        raise FileNotFoundError("未找到 liquidity.html")
    records = [{key: json_value(value) for key, value in row.items()} for row in df.to_dict(orient="records")]
    data_json = json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = HTML_PATH.read_text(encoding="utf-8")
    html = replace_constant(html, r"const EMBEDDED_DATA = \[.*?\];",
                            "const EMBEDDED_DATA = " + data_json + ";", "EMBEDDED_DATA")
    html = replace_constant(html, r'const GENERATED_AT = "[^"]*";',
                            f'const GENERATED_AT = "{generated_at}";', "GENERATED_AT")
    archive_file(HTML_PATH)
    temp_path = HTML_PATH.with_name(HTML_PATH.stem + ".tmp.html")
    temp_path.write_text(html, encoding="utf-8")
    os.replace(temp_path, HTML_PATH)
    print(f"HTML 已更新：{HTML_PATH}")


def main() -> None:
    df = read_data()
    updates = update_omo(df) + update_repo(df)
    merged = merge_updates(df, updates)
    # 即使两个数据源均无新增，也保存衍生指标和新增字段的重新计算结果。
    write_data(merged)
    regenerate_html(merged)
    print("全部更新完成。")


if __name__ == "__main__":
    main()