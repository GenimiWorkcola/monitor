import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

# 设置中文字体与负号显示
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------
# 1. 读取并解析 《国内现货黄金价格.txt》
# ---------------------------------------------------------
with open('国内现货黄金价格.txt', 'r', encoding='utf-8') as f:
    lines = f.read().strip().split('\n')

headers = [h.strip('"') for h in lines[0].split(',')]
data_row = [d.strip('"') for d in lines[1].split(',')]

dates_gold = []
values_gold = []

for h, v in zip(headers, data_row):
    if h.startswith('值['):
        date_str = h.replace('值[', '').replace(']', '')
        try:
            val = float(v)
            dates_gold.append(pd.to_datetime(date_str, format='%Y%m%d'))
            values_gold.append(val)
        except ValueError:
            continue

df_gold = pd.DataFrame({'Date': dates_gold, 'Gold': values_gold}).sort_values('Date')

# ---------------------------------------------------------
# 2. 读取并解析 《KOSPI_2025_to_present_merged.csv》
# ---------------------------------------------------------
df_kospi = pd.read_csv('KOSPI_2025_to_present_merged.csv')

# 自动寻找日期列和收盘价列
date_col = [c for c in df_kospi.columns if 'date' in c.lower() or '日期' in c][0]
close_col = [c for c in df_kospi.columns if 'close' in c.lower() or '收盘' in c.lower() or 'adj' in c.lower()][0]

df_kospi['Date'] = pd.to_datetime(df_kospi[date_col])
df_kospi['KOSPI'] = pd.to_numeric(df_kospi[close_col].astype(str).str.replace(',', ''), errors='coerce')
df_kospi = df_kospi[['Date', 'KOSPI']].dropna().sort_values('Date')

# ---------------------------------------------------------
# 3. 读取并解析 《利率.xlsx》 (处理 Strict OpenXML 格式)
# ---------------------------------------------------------
# 提取工作表 xml
with zipfile.ZipFile('利率.xlsx', 'r') as z:
    sheet_xml = z.read('xl/worksheets/sheet1.xml')
    strings_xml = z.read('xl/sharedStrings.xml') if 'xl/sharedStrings.xml' in z.namelist() else None

# 解析 sharedStrings
shared_strings = []
if strings_xml:
    tree = ET.fromstring(strings_xml)
    for elem in tree.iter():
        if elem.tag.endswith('t'):
            shared_strings.append(elem.text or '')

# 解析 cell 数据
tree = ET.fromstring(sheet_xml)
rows_dict = {}

for row in tree.iter():
    if row.tag.endswith('row'):
        row_idx = int(row.attrib['r'])
        row_vals = {}
        for cell in row.iter():
            if cell.tag.endswith('c'):
                col_r = cell.attrib['r']
                t = cell.attrib.get('t', '')
                val = None
                for v in cell.iter():
                    if v.tag.endswith('v'):
                        val = v.text
                        break
                if val is not None:
                    if t == 's':
                        val = shared_strings[int(val)]
                    row_vals[col_r] = val
        rows_dict[row_idx] = row_vals

# 转换表头与数据
header_row = rows_dict[1]
col_map = {k: v for k, v in header_row.items()}
data_list = []
for r in sorted(rows_dict.keys()):
    if r == 1:
        continue
    row_data = {}
    for col_r, val in rows_dict[r].items():
        col_name = col_map.get(col_r, col_r)
        row_data[col_name] = val
    data_list.append(row_data)

df_rates = pd.DataFrame(data_list)

# 清理利率数据列名与类型
date_col_r = [c for c in df_rates.columns if '日期' in str(c) or 'date' in str(c).lower()][0]
df_rates['Date'] = pd.to_datetime(df_rates[date_col_r])

for col in df_rates.columns:
    if col != 'Date':
        df_rates[col] = pd.to_numeric(df_rates[col], errors='coerce')

df_rates = df_rates.sort_values('Date')

# ---------------------------------------------------------
# 4. 数据切片 (2026今年以来) 与基准归一化 (Normalization = 100)
# ---------------------------------------------------------
start_date = '2026-01-01'

df_gold_2026 = df_gold[df_gold['Date'] >= start_date].copy()
df_kospi_2026 = df_kospi[df_kospi['Date'] >= start_date].copy()
df_rates_2026 = df_rates[df_rates['Date'] >= start_date].copy()

# 基准归一化处理（今年首个有效交易日为100）
df_gold_2026['Gold_Norm'] = df_gold_2026['Gold'] / df_gold_2026['Gold'].iloc[0] * 100
df_kospi_2026['KOSPI_Norm'] = df_kospi_2026['KOSPI'] / df_kospi_2026['KOSPI'].iloc[0] * 100

# ---------------------------------------------------------
# 5. 画图（上下两部分子图）
# ---------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(
    2, 1, figsize=(14, 10), dpi=300, 
    sharex=True, 
    gridspec_kw={'height_ratios': [1, 1], 'hspace': 0.12}
)

# --- 上半部分：黄金 & KOSPI 归一化指数 ---
ax1.plot(df_gold_2026['Date'], df_gold_2026['Gold_Norm'], label='国内现货黄金 (Normalized = 100)', color='#ff7f0e', linewidth=2)
ax1.plot(df_kospi_2026['Date'], df_kospi_2026['KOSPI_Norm'], label='KOSPI 指数 (Normalized = 100)', color='#17becf', linewidth=2)
ax1.axhline(100, color='gray', linestyle='--', linewidth=0.8, alpha=0.7)
ax1.set_ylabel('归一化指数 (2026年初 = 100)', fontsize=11)
ax1.set_title('2026年至今 黄金与KOSPI价格走势归一化对比及美元流动性利率', fontsize=14, fontweight='bold', pad=12)
ax1.legend(loc='upper left', frameon=True, facecolor='white')
ax1.grid(True, linestyle=':', alpha=0.6)

# --- 下半部分：各个利率曲线 ---
# 1. IORB（准备金利率）：#d62728，阶梯线（step='post' 或 'pre'），线宽 2
col_iorb = [c for c in df_rates.columns if 'iorb' in c.lower() or 'ioer' in c.lower()]
if col_iorb:
    ax2.step(df_rates_2026['Date'], df_rates_2026[col_iorb[0]], where='post', color='#d62728', linewidth=2, label='IORB（准备金利率）')

# 2. SOFR（担保隔夜融资利率）：#1f77b4，实线，线宽 1.4
col_sofr = [c for c in df_rates.columns if 'sofr' in c.lower()]
if col_sofr:
    ax2.plot(df_rates_2026['Date'], df_rates_2026[col_sofr[0]], color='#1f77b4', linewidth=1.4, label='SOFR（担保隔夜融资利率）')

# 3. 30天AA金融CP贴现率（平滑插值）：#2ca02c，实线，线宽 2
col_cp = [c for c in df_rates.columns if 'cp' in c.lower() or '贴现率' in c]
if col_cp:
    # 插值平滑线
    cp_series = df_rates_2026[col_cp[0]].interpolate(method='linear')
    ax2.plot(df_rates_2026['Date'], cp_series, color='#2ca02c', linewidth=2, label='30天AA金融CP贴现率（平滑插值）')
    
    # 4. CP贴现率（实际无空值数据点）：散点图，#