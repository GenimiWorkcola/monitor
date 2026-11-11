import os
import zipfile
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# 設定當前工作目錄為腳本所在資料夾
current_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(current_dir)

# 設定中文字體與負號顯示
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

print("正在讀取並解析資料檔案...")

# ---------------------------------------------------------
# 1. 讀取並解析 《国内现货黄金价格.txt》
# ---------------------------------------------------------
gold_path = os.path.join(current_dir, '国内现货黄金价格.txt')
with open(gold_path, 'r', encoding='utf-8') as f:
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
# 2. 讀取並解析 《KOSPI_2025_to_present_merged.csv》
# ---------------------------------------------------------
kospi_path = os.path.join(current_dir, 'KOSPI_2025_to_present_merged.csv')
df_kospi = pd.read_csv(kospi_path)

date_col = [c for c in df_kospi.columns if 'date' in c.lower() or '日期' in c][0]
close_col = [c for c in df_kospi.columns if 'close' in c.lower() or '收盘' in c.lower() or 'adj' in c.lower()][0]

df_kospi['Date'] = pd.to_datetime(df_kospi[date_col])
df_kospi['KOSPI'] = pd.to_numeric(df_kospi[close_col].astype(str).str.replace(',', ''), errors='coerce')
df_kospi = df_kospi[['Date', 'KOSPI']].dropna().sort_values('Date')

# ---------------------------------------------------------
# 3. 讀取並解析 《利率.xlsx》 (針對 Strict OpenXML 格式的穩定解析)
# ---------------------------------------------------------
rates_path = os.path.join(current_dir, '利率.xlsx')
with zipfile.ZipFile(rates_path, 'r') as z:
    sheet_xml = z.read('xl/worksheets/sheet1.xml')
    strings_xml = z.read('xl/sharedStrings.xml') if 'xl/sharedStrings.xml' in z.namelist() else None

# 解析 sharedStrings
shared_strings = []
if strings_xml:
    tree_s = ET.fromstring(strings_xml)
    for elem in tree_s.iter():
        if elem.tag.endswith('t'):
            shared_strings.append(elem.text or '')

# 解析 cell 資料
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

# 整理成 DataFrame
header_row = rows_dict.get(1, {})
columns = [header_row.get(col_key, col_key) for col_key in sorted(header_row.keys())]

data_list = []
for r in sorted(rows_dict.keys()):
    if r == 1:
        continue
    row_data = {}
    for col_key, val in rows_dict[r].items():
        # 對應欄位名稱
        col_name = header_row.get(col_key[0] + '1', col_key)
        row_data[col_name] = val
    data_list.append(row_data)

df_rates = pd.DataFrame(data_list)

# 重新命名與清理欄位型態
# 依據實際結構：第一欄通常為日期 (A欄/A行)
date_col_name = df_rates.columns[0]
df_rates['Date'] = pd.to_datetime(df_rates[date_col_name], errors='coerce')

for col in df_rates.columns:
    if col != 'Date' and col != date_col_name:
        df_rates[col] = pd.to_numeric(df_rates[col], errors='coerce')

df_rates = df_rates.dropna(subset=['Date']).sort_values('Date')

# ---------------------------------------------------------
# 4. 數據切片 (2026年至今) 與基準歸一化 (Normalization = 100)
# ---------------------------------------------------------
start_date = '2026-01-01'

df_gold_2026 = df_gold[df_gold['Date'] >= start_date].copy()
df_kospi_2026 = df_kospi[df_kospi['Date'] >= start_date].copy()
df_rates_2026 = df_rates[df_rates['Date'] >= start_date].copy()

# 基准歸一化處理（今年首個有效交易日為 100）
if not df_gold_2026.empty:
    df_gold_2026['Gold_Norm'] = df_gold_2026['Gold'] / df_gold_2026['Gold'].iloc[0] * 100
if not df_kospi_2026.empty:
    df_kospi_2026['KOSPI_Norm'] = df_kospi_2026['KOSPI'] / df_kospi_2026['KOSPI'].iloc[0] * 100

# ---------------------------------------------------------
# 5. 繪圖（上下雙子圖）
# ---------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(
    2, 1, figsize=(14, 10), dpi=300, 
    sharex=True, 
    gridspec_kw={'height_ratios': [1, 1], 'hspace': 0.12}
)

# --- 上半部分：黃金 & KOSPI 歸一化走勢 ---
if not df_gold_2026.empty:
    ax1.plot(df_gold_2026['Date'], df_gold_2026['Gold_Norm'], label='国内现货黄金 (Normalized = 100)', color='#ff7f0e', linewidth=2)
if not df_kospi_2026.empty:
    ax1.plot(df_kospi_2026['Date'], df_kospi_2026['KOSPI_Norm'], label='KOSPI 指数 (Normalized = 100)', color='#17becf', linewidth=2)
ax1.axhline(100, color='gray', linestyle='--', linewidth=0.8, alpha=0.7)
ax1.set_ylabel('归一化指数 (2026年初 = 100)', fontsize=11)
ax1.set_title('2026年至今 黄金与KOSPI走势对比及美元流动性利率', fontsize=14, fontweight='bold', pad=12)
ax1.legend(loc='upper left', frameon=True, facecolor='white')
ax1.grid(True, linestyle=':', alpha=0.6)

# --- 下半部分：利率曲線（嚴格按要求格式與配色定義） ---
cols = df_rates.columns

# 1. IORB（准备金利率）：#d62728，阶梯线（step: 'end'），线宽 2
col_iorb = next((c for c in cols if 'iorb' in str(c).lower() or 'ioer' in str(c).lower()), None)
if col_iorb:
    ax2.step(df_rates_2026['Date'], df_rates_2026[col_iorb], where='post', color='#d62728', linewidth=2, label='IORB（准备金利率）')

# 2. SOFR（担保隔夜融资利率）：#1f77b4，实线，线宽 1.4
col_sofr = next((c for c in cols if 'sofr' in str(c).lower()), None)
if col_sofr:
    ax2.plot(df_rates_2026['Date'], df_rates_2026[col_sofr], color='#1f77b4', linewidth=1.4, label='SOFR（担保隔夜融资利率）')

# 3. 30天AA金融CP贴现率（平滑插值）：#2ca02c，实线，线宽 2
col_cp = next((c for c in cols if 'cp' in str(c).lower() or '贴现率' in str(c)), None)
if col_cp:
    cp_series = df_rates_2026[col_cp].interpolate(method='linear')
    ax2.plot(df_rates_2026['Date'], cp_series, color='#2ca02c', linewidth=2, label='30天AA金融CP贴现率（平滑插值）')
    
    # 4. CP贴现率（实际无空值数据点）：散点图，#145a1e，符号大小 6 (s=36)，白色边框 #fff，宽度 0.5
    df_cp_actual = df_rates_2026.dropna(subset=[col_cp])
    ax2.scatter(df_cp_actual['Date'], df_cp_actual[col_cp], color='#145a1e', s=36, 
                edgecolors='#ffffff', linewidths=0.5, label='CP贴现率（实际数据点）', zorder=5)

# 5. 1D隐含利率（USD.CNY）：#c62828，虚线，线宽 1.8，大小 5 圆形点
col_1d = next((c for c in cols if '1d' in str(c).lower()), None)
if col_1d:
    ax2.plot(df_rates_2026['Date'], df_rates_2026[col_1d], linestyle='--', color='#c62828', 
             linewidth=1.8, marker='o', markersize=5, label='1D隐含利率（USD.CNY）')

# 6. 3M隐含利率（USD.CNY）：#f4b6c2，虚线，线宽 1.1，大小 3.5 圆形点
col_3m = next((c for c in cols if '3m' in str(c).lower()), None)
if col_3m:
    ax2.plot(df_rates_2026['Date'], df_rates_2026[col_3m], linestyle='--', color='#f4b6c2', 
             linewidth=1.1, marker='o', markersize=3.5, label='3M隐含利率（USD.CNY）')

ax2.set_ylabel('利率 (%)', fontsize=11)
ax2.legend(loc='upper right', frameon=True, facecolor='white', fontsize=9)
ax2.grid(True, linestyle=':', alpha=0.6)

# 横軸時間格式化
ax2.xaxis.set_major_locator(mdates.MonthLocator())
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
fig.autofmt_xdate()

# 儲存圖片
output_path = os.path.join(current_dir, 'gold_kospi_rates_comparison.png')
plt.savefig(output_path, dpi=300, bbox_inches='tight')
print(f"圖表已成功生成並儲存至: {output_path}")