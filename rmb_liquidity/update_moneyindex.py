#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动更新 moneyindex.html 中的人民币资金面情绪指数数据。

使用方法：
1. 将本脚本与 moneyindex.html 放在同一文件夹。
2. 安装依赖：pip install requests
3. 运行：python update_moneyindex.py

每次运行会下载 CFETS-NEX 页面，解析最新数据，与旧 HTML 数据合并去重，
生成新的 moneyindex.html 和 moneyindex.csv，并把旧版本移入 old data 文件夹。
也可以指定 HTML 所在目录：python update_moneyindex.py --dir "D:\\数据"
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

URL = "https://www.cfets-nex.com.cn/Market/marketData/money"
HTML_NAME = "moneyindex.html"
CSV_NAME = "moneyindex.csv"
ARCHIVE_DIR_NAME = "old data"


def download_page(url: str) -> str:
    """下载网页源码，并检查 HTTP 状态。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.cfets-nex.com.cn/",
    }
    # 网站偶尔会临时返回 405，短暂等待后重试。
    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except requests.RequestException:
            if attempt == max_attempts:
                raise
            print(f"      第 {attempt} 次下载失败，2 秒后重试……")
            time.sleep(2)
    raise RuntimeError("下载失败")  # 理论上不会执行到这里


def extract_js_object(source: str, declaration_pattern: str) -> str:
    """从 JS 声明后提取完整对象，正确处理字符串和嵌套花括号。"""
    match = re.search(declaration_pattern, source, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"未找到数据声明：{declaration_pattern}")

    start = source.find("{", match.end())
    if start < 0:
        raise ValueError("数据声明后未找到对象起始符 {")

    depth = 0
    quote = None
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue

        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]

    raise ValueError("数据对象不完整：未找到对应的结束符 }")


def parse_fxsi_object(js_object: str) -> dict[str, list]:
    """解析 timeList 与 dataList，不使用 eval。"""
    time_match = re.search(r'"?timeList"?\s*:\s*(\[[\s\S]*?\])\s*,', js_object)
    if not time_match:
        raise ValueError("未找到 timeList")

    try:
        time_list = [str(item).strip() for item in ast.literal_eval(time_match.group(1))]
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"timeList 解析失败：{exc}") from exc

    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    item_pattern = re.compile(
        r"\{\s*[\"']?value[\"']?\s*:\s*(" + number + r")"
        r"\s*,\s*[\"']?single[\"']?\s*:\s*\[\s*"
        + r"\s*,\s*".join(["(" + number + r")"] * 3)
        + r"\s*\]\s*\}",
        flags=re.IGNORECASE,
    )
    data_list = [
        {
            "value": float(match.group(1)),
            "single": [float(match.group(i)) for i in range(2, 5)],
        }
        for match in item_pattern.finditer(js_object)
    ]

    if not time_list or not data_list:
        raise ValueError("timeList 或 dataList 为空")
    if len(time_list) != len(data_list):
        raise ValueError(
            f"时间与数据数量不一致：timeList={len(time_list)}，dataList={len(data_list)}"
        )
    return {"timeList": time_list, "dataList": data_list}


def parse_web_page(source: str) -> dict[str, list]:
    obj = extract_js_object(source, r"\bvar\s+FXSIData\s*=")
    return parse_fxsi_object(obj)


def parse_existing_html(source: str) -> dict[str, list]:
    obj = extract_js_object(source, r"\bconst\s+initialRawData\s*=")
    return parse_fxsi_object(obj)


def merge_data(old: dict[str, list], new: dict[str, list]) -> dict[str, list]:
    """以时间为键合并；新数据覆盖同一时间点的旧数据。"""
    merged: dict[str, dict] = {}
    for raw in (old, new):
        for time, item in zip(raw["timeList"], raw["dataList"]):
            datetime.strptime(time, "%Y-%m-%d %H:%M")  # 校验时间格式
            merged[time] = {
                "value": float(item["value"]),
                "single": [float(value) for value in item["single"]],
            }

    times = sorted(merged)
    return {
        "timeList": times,
        "dataList": [merged[time] for time in times],
    }


def replace_initial_data(template: str, data: dict[str, list]) -> str:
    """仅替换 HTML 中的 initialRawData，保留页面样式和交互功能。"""
    match = re.search(r"\bconst\s+initialRawData\s*=", template)
    if not match:
        raise ValueError("HTML 中未找到 const initialRawData =")
    old_obj = extract_js_object(template, r"\bconst\s+initialRawData\s*=")
    obj_start = template.find(old_obj, match.end())
    if obj_start < 0:
        raise ValueError("无法定位 initialRawData 数据对象")

    data_json = json.dumps(data, ensure_ascii=False, indent=2)
    return template[:obj_start] + data_json + template[obj_start + len(old_obj) :]


def archive_path(archive_dir: Path, now: datetime, suffix: str) -> Path:
    """生成不冲突的归档文件名，例如 moneyindex_20261027_153000.csv。"""
    stem = f"moneyindex_{now:%Y%m%d_%H%M%S}"
    candidate = archive_dir / f"{stem}.{suffix}"
    counter = 1
    while candidate.exists():
        candidate = archive_dir / f"{stem}_{counter}.{suffix}"
        counter += 1
    return candidate


def make_csv(data: dict[str, list]) -> str:
    """生成与 HTML 数据完全一致的 CSV 内容。"""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["时间", "整体情绪指数", "大型银行", "中小银行", "非银机构"])
    for time, item in zip(data["timeList"], data["dataList"]):
        values = [item["value"], *item["single"]]
        writer.writerow([time, *(format(float(value), ".15g") for value in values)])
    return output.getvalue()


def update(work_dir: Path, url: str = URL) -> None:
    html_path = work_dir / HTML_NAME
    csv_path = work_dir / CSV_NAME
    if not html_path.is_file():
        raise FileNotFoundError(f"未找到 {html_path}，请将脚本与现有 HTML 放在同一目录")

    print(f"[1/6] 读取旧文件：{html_path}")
    old_template = html_path.read_text(encoding="utf-8-sig")
    old_data = parse_existing_html(old_template)
    print(f"      旧数据：{len(old_data['timeList'])} 条")

    print(f"[2/6] 下载网页：{url}")
    web_source = download_page(url)
    new_data = parse_web_page(web_source)
    print(f"      网页数据：{len(new_data['timeList'])} 条")

    print("[3/6] 合并数据并按时间排序、去重")
    merged = merge_data(old_data, new_data)
    added = len(set(merged["timeList"]) - set(old_data["timeList"]))
    print(f"      合并后：{len(merged['timeList'])} 条；新增时间点：{added} 个")

    print("[4/6] 准备新 HTML 和 CSV，并校验数据")
    updated_html = replace_initial_data(old_template, merged)
    updated_csv = make_csv(merged)
    html_temp = work_dir / ".moneyindex.html.tmp"
    csv_temp = work_dir / ".moneyindex.csv.tmp"
    archive_dir = work_dir / ARCHIVE_DIR_NAME
    archive_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    archived_html = archive_path(archive_dir, now, "html")
    archived_csv = archive_path(archive_dir, now, "csv") if csv_path.exists() else None

    try:
        html_temp.write_text(updated_html, encoding="utf-8")
        csv_temp.write_text(updated_csv, encoding="utf-8-sig")
        # 再次解析生成结果，避免损坏唯一的主文件。
        checked = parse_existing_html(html_temp.read_text(encoding="utf-8"))
        if len(checked["timeList"]) != len(merged["timeList"]):
            raise ValueError("生成后的 HTML 数据校验失败")
        with csv_temp.open("r", encoding="utf-8-sig", newline="") as file:
            csv_rows = list(csv.reader(file))
        if len(csv_rows) != len(merged["timeList"]) + 1:
            raise ValueError("生成后的 CSV 数据校验失败")

        print("[5/6] 归档旧文件并保存新文件")
        shutil.move(str(html_path), str(archived_html))
        html_temp.replace(html_path)
        if archived_csv is not None:
            shutil.move(str(csv_path), str(archived_csv))
        csv_temp.replace(csv_path)
    finally:
        if html_temp.exists():
            html_temp.unlink()
        if csv_temp.exists():
            csv_temp.unlink()

    print("[6/6] 更新完成")
    print(f"      新 HTML：{html_path}")
    print(f"      新 CSV：{csv_path}")
    print(f"      旧 HTML：{archived_html}")
    if archived_csv is not None:
        print(f"      旧 CSV：{archived_csv}")
    else:
        print("      旧 CSV：不存在，无需归档")
    print(f"      最新时间点：{merged['timeList'][-1]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="自动更新 moneyindex.html 和 moneyindex.csv")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="moneyindex.html 所在目录；默认为脚本所在目录",
    )
    parser.add_argument("--url", default=URL, help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        update(args.dir.expanduser().resolve(), args.url)
        return 0
    except (requests.RequestException, OSError, ValueError) as exc:
        print(f"更新失败：{exc}", file=sys.stderr)
        print("请根据上方错误检查网络、文件权限及 old data 文件夹。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
