# -*- coding: utf-8 -*-
"""
央行资金面数据更新脚本
======================
功能：
1. 读取本脚本同文件夹下的 cfets_nex_omo_data_with_shibor.xlsx
2. 从 CFETS-NEX 每日简报页按时间倒序增量抓取「每日市场动态」，
   遇到 Excel 已有日期即停止；自动跳过只有晨报的日期和节假日
3. 合并去重后写回 cfets_nex_omo_data_with_shibor.xlsx
4. 旧文件自动移入同文件夹下的 old data 子文件夹（同名自动覆盖）

依赖安装（首次运行前执行一次）：
    pip install requests beautifulsoup4 pandas openpyxl

运行：
    python 更新央行资金面数据.py
"""

import os
import re
import sys
import time
import shutil
import datetime as dt

import requests
import pandas as pd
from bs4 import BeautifulSoup

# ---------- 配置 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_NAME = "cfets_nex_omo_data_with_shibor.xlsx"
FILE_PATH = os.path.join(BASE_DIR, FILE_NAME)
OLD_DIR = os.path.join(BASE_DIR, "old data")

BASE_URL = "https://www.cfets-nex.com.cn/Market/marketOverview/marketOverview/?PageIndex={}"
MAX_PAGES = 40          # 最多翻页数（防止异常死循环）
RETRY = 5               # 每页被反爬拦截时的重试次数

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}


# ---------- 解析 ----------
def parse_page(html):
    """解析一页简报列表，返回 [(日期, 每日市场动态正文), ...]（晨报自动忽略）"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for block in soup.select(".has-date"):
        d_el = block.select_one(".date")
        if not d_el:
            continue
        date_str = d_el.get_text(strip=True)
        for box in block.select(".report-box"):
            h4 = box.select_one("h4")
            p = box.select_one("p")
            if h4 and p and "每日市场动态" in h4.get_text():
                out.append((date_str, p.get_text(strip=True)))
    return out


def parse_summary(text):
    """从每日市场动态正文中提取结构化字段"""
    # 投放：对「开展……逆回购操作」段中所有“XX亿”求和（兼容7天期/隔夜期/14天期等多品种）
    m = re.search(r"开展(.*?)逆回购操作", text)
    invest = None
    if m:
        invest = sum(float(x) for x in re.findall(r"(\d+(?:\.\d+)?)亿", m.group(1)))
    m2 = re.search(r"逆回购到期(\d+(?:\.\d+)?)亿", text)
    mature = float(m2.group(1)) if m2 else None
    net = (invest - mature) if invest is not None and mature is not None else None

    def shibor(name):
        mm = re.search(name + r"品种报\s*(\d+\.\d+)\s*%", text)
        return float(mm.group(1)) if mm else None

    # 资金面概况：取含“资金面”的那句话，去掉“今日”“资金面”字样
    sent = ""
    for s in re.split(r"。", text):
        if "资金面" in s:
            sent = s
    sent = re.sub(r"^今日", "", sent).replace("资金面", "").strip("，,。 ")
    return invest, mature, net, sent, shibor("隔夜"), shibor("7天"), shibor("3个月")


def fetch_page(sess, page):
    """带反爬重试的抓取；成功返回条目列表，失败返回 None"""
    url = BASE_URL.format(page)
    for attempt in range(RETRY):
        try:
            resp = sess.get(url, timeout=30)
            resp.encoding = "utf-8"
        except Exception as e:
            print(f"  第{page}页 第{attempt+1}次请求异常: {e}")
            time.sleep(3)
            continue
        if "aliyun_waf" in resp.text:          # 阿里云WAF拦截页
            wait = 5 * (attempt + 1)
            print(f"  第{page}页 触发反爬，{wait}秒后重试 ({attempt+1}/{RETRY})")
            time.sleep(wait)
            continue
        items = parse_page(resp.text)
        if items:
            return items
        time.sleep(2)
    return None


# ---------- 主流程 ----------
def main():
    if not os.path.exists(FILE_PATH):
        print(f"未找到 {FILE_NAME}，请把它放到本脚本同一文件夹后重试。")
        sys.exit(1)

    df = pd.read_excel(FILE_PATH)
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    latest = dt.datetime.strptime(df["日期"].max(), "%Y-%m-%d").date()
    print(f"现有数据 {len(df)} 条，最新日期 {latest}，开始增量抓取...\n")

    sess = requests.Session()
    sess.headers.update(HEADERS)

    new_rows, stop = [], False
    page = 1
    while not stop and page <= MAX_PAGES:
        items = fetch_page(sess, page)
        if items is None:
            print(f"第{page}页多次重试仍被拦截，提前结束（已抓到的部分不受影响）")
            break
        for date_str, txt in items:
            d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
            if d <= latest:
                stop = True
                break
            inv, mat, net, cond, on, d7, m3 = parse_summary(txt)
            new_rows.append({
                "日期": date_str, "央行逆回购投放(亿)": inv, "逆回购到期(亿)": mat,
                "净投放(亿)": net, "资金面概况": cond,
                "隔夜Shibor": on, "7天Shibor": d7, "3个月Shibor": m3,
            })
        print(f"第{page}页: {items[0][0]} ~ {items[-1][0]}" + ("  -> 到达已有日期，停止" if stop else ""))
        page += 1
        time.sleep(1.5)  # 控制频率，降低触发反爬概率

    if not new_rows:
        print("\n没有新数据，Excel 已是最新，文件未改动。")
        return

    new_df = pd.DataFrame(new_rows)
    merged = pd.concat([df, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset="日期", keep="last").sort_values("日期").reset_index(drop=True)
    for c in ["央行逆回购投放(亿)", "逆回购到期(亿)", "净投放(亿)"]:
        merged[c] = merged[c].astype(float).round(0).astype("Int64")

    # 旧文件移入 old data（自动覆盖同名旧文件）
    os.makedirs(OLD_DIR, exist_ok=True)
    old_target = os.path.join(OLD_DIR, FILE_NAME)
    if os.path.exists(old_target):
        os.remove(old_target)
    shutil.move(FILE_PATH, old_target)

    merged.to_excel(FILE_PATH, index=False)

    dates = sorted(r["日期"] for r in new_rows)
    print(f"\n更新完成：新增 {len(new_rows)} 条（{dates[-1]} ~ {dates[0]} 之间的新日期）")
    print(f"新表共 {len(merged)} 条，最新日期 {merged['日期'].max()}")
    print(f"新文件: {FILE_PATH}")
    print(f"旧文件已移至: {old_target}")
    print("提示：节假日（如端午）无数据属正常，不算缺失。")


if __name__ == "__main__":
    main()
