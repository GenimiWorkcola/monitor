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

# 設定英文字體與粗體，關閉負號亂碼
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

print("Reading and parsing data files...")

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
#    注意：此檔案表頭與資料欄位錯位一欄（日期在A欄，表頭「日期」在B1），
#    故採用固定位置映射：A=日期, B=SOFR, C=IORB, D=CP, E=1D, F=3M
# ---------------------------------------------------------
rates_path = os.path.join(current_dir, '利率.xlsx')
with zipfile.ZipFile(rates_path, 'r') as z:
    sheet_xml = z.read('xl/worksheets/sheet1.xml')
    strings_xml = z.read('xl/sharedStrings.xml') if 'xl/sharedStrings.xml' in z.namelist() else None

shared_strings = []
if strings_xml:
    tree_s = ET.fromstring(strings_xml)
    for elem in tree_s.iter():
        if elem.tag.endswith('t'):
            shared_strings.append(elem.text or '')

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
                t_attr = cell.attrib.get('t', '')
                if t_attr == 'inlineStr':
                    for is_elem in cell.iter():
                        if is_elem.tag.endswith('is'):
                            texts = [tt.text or '' for tt in is_elem.iter() if tt.tag.endswith('t')]
                            val = ''.join(texts)
                            break
                if val is None:
                    for v in cell.iter():
                        if v.tag.endswith('v'):
                            val = v.text
                            break
                if val is not None:
                    if t == 's':
                        val = shared_strings[int(val)]
                    row_vals[col_r] = val
        rows_dict[row_idx] = row_vals

COLMAP = {'A': 'Date', 'B': 'SOFR', 'C': 'IORB', 'D': 'CP', 'E': 'Implied_1D', 'F': 'Implied_3M'}

recs = []
for r in sorted(rows_dict.keys()):
    if r == 1:
        continue
    rv = rows_dict[r]
    rec = {}
    for col_letter, name in COLMAP.items():
        rec[name] = rv.get(col_letter + str(r))
    recs.append(rec)

df_rates = pd.DataFrame(recs)

# 支援 Excel 日期序列號與標準日期字串的相容解析
def parse_date_robust(val):
    if val is None or pd.isna(val):
        return pd.NaT
    val_str = str(val).strip()
    try:
        num = float(val_str)
        if num > 10000:
            return pd.Timestamp('1899-12-30') + pd.to_timedelta(num, unit='D')
    except ValueError:
        pass
    return pd.to_datetime(val_str, errors='coerce')

df_rates['Date'] = df_rates['Date'].apply(parse_date_robust)
for col in ['SOFR', 'IORB', 'CP', 'Implied_1D', 'Implied_3M']:
    df_rates[col] = pd.to_numeric(df_rates[col], errors='coerce')
df_rates = df_rates.dropna(subset=['Date']).sort_values('Date').reset_index(drop=True)

# ---------------------------------------------------------
# 4. 計算 1D 隱含利率 與 CP 之間的差異 (1D Implied - CP)
# ---------------------------------------------------------
df_rates['Diff_1D_minus_CP'] = df_rates['Implied_1D'] - df_rates['CP']

# ---------------------------------------------------------
# 5. 數據切片 (2026年至今) 與基準歸一化 (Normalization = 100)
# ---------------------------------------------------------
start_date = '2026-01-01'

df_gold_2026 = df_gold[df_gold['Date'] >= start_date].copy()
df_kospi_2026 = df_kospi[df_kospi['Date'] >= start_date].copy()
df_rates_2026 = df_rates[df_rates['Date'] >= start_date].copy()

if not df_gold_2026.empty:
    df_gold_2026['Gold_Norm'] = df_gold_2026['Gold'] / df_gold_2026['Gold'].iloc[0] * 100
if not df_kospi_2026.empty:
    df_kospi_2026['KOSPI_Norm'] = df_kospi_2026['KOSPI'] / df_kospi_2026['KOSPI'].iloc[0] * 100

# ---------------------------------------------------------
# 6. 繪圖（上下雙子圖 - 移除IORB，KOSPI改用韓國國旗藍 #0047A0；
#    上圖疊加 1D-CP 差異柱狀色塊）
# ---------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(
    2, 1, figsize=(14, 10), dpi=300,
    sharex=True,
    gridspec_kw={'height_ratios': [1, 1], 'hspace': 0.12}
)

# --- 上半部分：Gold & KOSPI Normalized Performance ---
if not df_gold_2026.empty:
    ax1.plot(df_gold_2026['Date'], df_gold_2026['Gold_Norm'], label='Domestic Gold Spot (Normalized = 100)', color='#ff7f0e', linewidth=2)
if not df_kospi_2026.empty:
    # 韓國國旗藍 (Korean Flag Blue: #0047A0)
    ax1.plot(df_kospi_2026['Date'], df_kospi_2026['KOSPI_Norm'], label='KOSPI Index (Normalized = 100)', color='#0047A0', linewidth=2)
ax1.axhline(100, color='gray', linestyle='--', linewidth=0.8, alpha=0.7)
ax1.set_ylabel('Normalized Index (2026 YTD = 100)', fontsize=11, fontweight='bold')
ax1.set_title('2026 YTD Gold & KOSPI Normalized Performance with USD Liquidity Rates', fontsize=14, fontweight='bold', pad=12)
ax1.grid(True, linestyle=':', alpha=0.6)

# --- 上圖疊加：1D Implied Rate - CP Rate 利差柱狀色塊 ---
daily_idx = pd.date_range(df_rates_2026['Date'].min(), df_rates_2026['Date'].max(), freq='D')
s_1d = df_rates_2026.set_index('Date')['Implied_1D'].reindex(daily_idx).interpolate(method='linear')
s_cp = df_rates_2026.set_index('Date')['CP'].reindex(daily_idx).interpolate(method='linear')
df_diff = pd.DataFrame({'Date': daily_idx, 'Diff_1D_minus_CP': s_1d.values - s_cp.values}).dropna()

if not df_diff.empty:
    ax1b = ax1.twinx()
    bar_width = 1.2
    colors = np.where(df_diff['Diff_1D_minus_CP'] >= 0, '#e41a1c', '#1b9e77')
    ax1b.bar(df_diff['Date'], df_diff['Diff_1D_minus_CP'],
             width=bar_width, color=colors, alpha=0.35, edgecolor='none', zorder=0)
    ax1b.axhline(0, color='gray', linewidth=0.8, alpha=0.6)
    ax1b.set_ylabel('1D Implied - CP Rate Diff (%) [Interpolated]', fontsize=11, fontweight='bold', color='#555555')
    ax1b.tick_params(axis='y', labelcolor='#555555')
    from matplotlib.patches import Patch
    legend_patches = [
        Patch(facecolor='#e41a1c', alpha=0.5, label='1D Implied - CP > 0 (Interpolated)'),
        Patch(facecolor='#1b9e77', alpha=0.5, label='1D Implied - CP < 0 (Interpolated)'),
    ]
    handles, labels_ = ax1.get_legend_handles_labels()
    handles += legend_patches
    labels_ += [p.get_label() for p in legend_patches]
    ax1.legend(handles, labels_, loc='upper left', frameon=True, facecolor='white', prop={'weight': 'bold'})
else:
    ax1.legend(loc='upper left', frameon=True, facecolor='white', prop={'weight': 'bold'})

# --- 下半部分：Interest Rates (Removed IORB) ---
ax2.plot(df_rates_2026['Date'], df_rates_2026['SOFR'], color='#1f77b4', linewidth=1.4, label='SOFR (Secured Overnight Financing Rate)')

cp_series = df_rates_2026['CP'].interpolate(method='linear')
ax2.plot(df_rates_2026['Date'], cp_series, color='#2ca02c', linewidth=2, label='30-Day AA Financial CP Discount Rate (Interpolated)')

df_cp_actual = df_rates_2026.dropna(subset=['CP'])
ax2.scatter(df_cp_actual['Date'], df_cp_actual['CP'], color='#145a1e', s=36,
            edgecolors='#ffffff', linewidths=0.5, label='CP Rate (Actual Data Points)', zorder=5)

ax2.plot(df_rates_2026['Date'], df_rates_2026['Implied_1D'], linestyle='--', color='#c62828',
         linewidth=1.8, marker='o', markersize=5, label='1D Implied Rate (USD.CNY)')

ax2.plot(df_rates_2026['Date'], df_rates_2026['Implied_3M'], linestyle='--', color='#f4b6c2',
         linewidth=1.1, marker='o', markersize=3.5, label='3M Implied Rate (USD.CNY)')

ax2.set_ylabel('Interest Rate (%)', fontsize=11, fontweight='bold')
ax2.legend(loc='upper right', frameon=True, facecolor='white', fontsize=9, prop={'weight': 'bold'})
ax2.grid(True, linestyle=':', alpha=0.6)

ax2.xaxis.set_major_locator(mdates.MonthLocator())
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
for label in (ax1.get_xticklabels() + ax1.get_yticklabels() + ax2.get_xticklabels() + ax2.get_yticklabels()):
    label.set_fontweight('bold')

fig.autofmt_xdate()

output_path = os.path.join(current_dir, 'gold_kospi_rates_comparison_en_bold.png')
plt.savefig(output_path, dpi=300, bbox_inches='tight')
print(f"Chart successfully generated and saved to: {output_path}")
print(f"Diff stats (1D Implied - CP, interpolated daily, 2026): n={len(df_diff)}, "
      f"mean={df_diff['Diff_1D_minus_CP'].mean():.3f}, min={df_diff['Diff_1D_minus_CP'].min():.3f}, max={df_diff['Diff_1D_minus_CP'].max():.3f}")
plt.show()