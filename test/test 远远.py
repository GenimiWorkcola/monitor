import os
import matplotlib
# 强制使用无界面后端，防止 GitHub Actions 环境报错
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

# 获取当前脚本所在的绝对目录 (即 test 文件夹)
current_dir = os.path.dirname(os.path.abspath(__file__))

# 1. 数据加载与清洗
exch_path = os.path.join(current_dir, 'EXCHUS.csv')
sse_path = os.path.join(current_dir, '上证指数历史数据.csv')
hsi_path = os.path.join(current_dir, '恒生 HistoricalPrices.csv')
val_path = os.path.join(current_dir, 'VALEXPCNM052N.csv')

df_exch = pd.read_csv(exch_path)
df_exch['observation_date'] = pd.to_datetime(df_exch['observation_date'])

df_sse = pd.read_csv(sse_path)
df_sse['收盘'] = df_sse['收盘'].astype(str).str.replace(',', '').astype(float)
df_sse['日期'] = pd.to_datetime(df_sse['日期'])
df_sse = df_sse.sort_values('日期').rename(columns={'日期': 'Date', '收盘': 'SSE'})

df_hsi = pd.read_csv(hsi_path)
df_hsi.columns = df_hsi.columns.str.strip()
df_hsi['Close'] = df_hsi['Close'].astype(str).str.replace(',', '').astype(float)
df_hsi['Date'] = pd.to_datetime(df_hsi['Date'], format='%m/%d/%y')
df_hsi = df_hsi.sort_values('Date').rename(columns={'Close': 'HSI'})

df_val = pd.read_csv(val_path)
df_val['observation_date'] = pd.to_datetime(df_val['observation_date'])
df_val = df_val.sort_values('observation_date')
# 计算出口金额同比增速 (YoY %)
df_val['Export_YoY'] = df_val['VALEXPCNM052N'].pct_change(periods=12) * 100

# 数据合并 (2015-2026)
merged = pd.merge(df_sse[['Date', 'SSE']], df_hsi[['Date', 'HSI']], on='Date', how='inner')
merged = merged[(merged['Date'] >= '2015-01-01') & (merged['Date'] <= '2026-03-01')].copy()

# 方法 A：基期归一化 (以 2015 年起点为 100)
merged['SSE_Norm'] = merged['SSE'] / merged['SSE'].iloc[0] * 100
merged['HSI_Norm'] = merged['HSI'] / merged['HSI'].iloc[0] * 100

# ----------------- 定义中国外贸出口五大区间（已更新配色方案） -----------------
export_periods = [
    ("2015-01-01", "2016-02-29", "E1: Bottoming & Clearing", "#6baed6"),       # 筑底清盘：冰冷柔和蓝
    ("2016-03-01", "2018-06-30", "E2: Moderate Recovery", "#fd8d3c"),        # 温和复苏：琥珀暖橙（已避开绿色）
    ("2018-07-01", "2020-02-29", "E3: Tariff War & Volatility", "#756bb1"),  # 关税摩擦：深紫博弈色
    ("2020-03-01", "2022-12-31", "E4: Pandemic Boom & Massive Orders", "#4daf4a"), # 与下方 P3 宽松大泛滥绿强共振
    ("2023-01-01", "2026-03-01", "E5: New Three & High Resilience", "#1f77b4"),  # 韧性出海：活力深蓝
]

# ----------------- 定义美元流动性细分区间（置于底部色条） -----------------
liquidity_periods = [
    ("2015-01-01", "2016-02-29", "P1-a", "#990000"),
    ("2016-03-01", "2017-12-31", "P1-b", "#d73027"),
    ("2018-01-01", "2019-08-31", "P1-c", "#f46d43"),
    ("2019-09-01", "2020-02-29", "P2", "#ff7f00"),
    ("2020-03-01", "2022-02-28", "P3", "#4daf4a"), # 对应 E4 的 QE 宽松大泛滥绿
    ("2022-03-01", "2023-07-31", "P4", "#984ea3"),
    ("2023-08-01", "2024-12-31", "P5", "#377eb8"),
    ("2025-01-01", "2025-11-30", "P6", "#a65628"),
    ("2025-12-01", "2026-03-01", "P7", "#f781bf"),
]

observations = [
    ("2016-01-01", "2017-12-31", "2016-2017\nHang Seng Outperformed", 110, '#2ca02c'),
    ("2020-01-01", "2020-06-30", "2020 H1\nConverged", 100, '#ff7f0e'),
    ("2020-10-01", "2021-02-28", "2020.10-2021.2\nHang Seng Outperformed", 130, '#2ca02c'),
    ("2021-07-01", "2021-10-31", "2021.7-10\nUnderperformed", 110, '#d62728'),
    ("2022-06-01", "2022-10-31", "2022.6-10\nFell & Underperformed", 80, '#d62728'),
    ("2023-01-01", "2025-08-31", "2023-2025.8\nHang Seng Underperformed", 70, '#d62728'),
    ("2025-01-01", "2025-01-31", "2025.1\nOutperformed", 90, '#2ca02c'),
    ("2025-09-01", "2026-03-01", "2025.9-Present\nUnderperformed", 85, '#d62728'),
]

# 创建图表：上方外贸独立子图(ax_export) + 中间主图(ax1) + 底部流动性色条(ax_bar)
fig, (ax_export, ax1, ax_bar) = plt.subplots(
    3, 1, figsize=(16, 12), dpi=300, 
    gridspec_kw={'height_ratios': [2.5, 8, 0.8], 'hspace': 0.15},
    sharex=True
)

# 使用安全的默认样式
plt.style.use('default')
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial']

ax2 = ax1.twinx()

# 1. 绘制上方外贸独立图 (ax_export)
for start, end, code, color in export_periods:
    s_dt = pd.to_datetime(start)
    e_dt = pd.to_datetime(end)
    ax_export.axvspan(s_dt, e_dt, color=color, alpha=0.15)
    mid_dt = s_dt + (e_dt - s_dt) / 2
    ax_export.text(mid_dt, 0.85, code, color=color, fontweight='bold', fontsize=9, ha='center', va='center',
                   bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor=color, alpha=0.85))

ax_export.plot(df_val['observation_date'], df_val['VALEXPCNM052N'], color='#2ca02c', linewidth=2, label='China Export Value (USD Million)')
ax_export.set_ylabel('Export Value ($M)', fontsize=10, color='#2ca02c')
ax_export.set_title('China Export Dynamics (E1-E5) & Market Performance with USD Liquidity (2015-2026)', fontsize=14, fontweight='bold', pad=12)
ax_export.legend(loc='upper left', frameon=True, facecolor='white')
ax_export.set_xlim(pd.to_datetime('2015-01-01'), pd.to_datetime('2026-03-01'))

# 2. 绘制中间主图 (ax1 & ax2)
line1 = ax1.plot(merged['Date'], merged['HSI_Norm'], color='#1f77b4', linewidth=2, label='Hang Seng Index (Normalized = 100)')
line2 = ax1.plot(merged['Date'], merged['SSE_Norm'], color='#d62728', linewidth=2, label='SSE Composite Index (Normalized = 100)')
line3 = ax2.plot(df_exch['observation_date'], df_exch['EXCHUS'], color='black', linestyle=':', linewidth=1.5, alpha=0.7, label='USD/CNY Rate')

for start, end, label, y_pos, color in observations:
    s_dt = pd.to_datetime(start)
    e_dt = pd.to_datetime(end)
    ax1.axvspan(s_dt, e_dt, color=color, alpha=0.10)
    mid_dt = s_dt + (e_dt - s_dt) / 2
    ax1.annotate(label, xy=(mid_dt, y_pos), fontsize=8, fontweight='bold',
                 ha='center', va='center', color=color,
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor=color, alpha=0.85))

ax1.set_ylabel('Normalized Index Value (2015-01-05 = 100)', fontsize=11)
ax2.set_ylabel('USD / CNY Rate', fontsize=11)

ax1.xaxis.set_major_locator(mdates.YearLocator())
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
ax2.set_ylim(6.0, 7.6)

lines = line1 + line2 + line3
ax1.legend(lines, [l.get_label() for l in lines], loc='upper left', frameon=True, facecolor='white')

# 3. 绘制底部流动性色条 (ax_bar)
ax_bar.set_ylim(0, 1)
ax_bar.axis('off')

for start, end, code, color in liquidity_periods:
    s_dt = pd.to_datetime(start)
    e_dt = pd.to_datetime(end)
    ax_bar.axvspan(s_dt, e_dt, color=color, alpha=0.85)
    mid_dt = s_dt + (e_dt - s_dt) / 2
    ax_bar.text(mid_dt, 0.5, code, color='white', fontweight='bold', fontsize=8, ha='center', va='center')

# 保存图片至 test 文件夹
output_image_path = os.path.join(current_dir, 'method_a_with_liquidity_colorbar.png')
plt.savefig(output_image_path, dpi=300, bbox_inches='tight')
print(f"Plot created and saved successfully at: {output_image_path}")