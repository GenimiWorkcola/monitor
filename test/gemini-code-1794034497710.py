import os
import requests
import pandas as pd
import matplotlib
# 强制使用无界面后端，防止运行环境报错
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def fetch_hkma_data():
    url = "https://api.hkma.gov.hk/public/market-data-and-statistics/monthly-statistical-bulletin/financial/monetary-statistics"
    offset = 0
    limit = 100
    all_records = []
    
    print("Fetching data from HKMA API...")
    while True:
        params = {"offset": offset, "limit": limit}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                print(f"API request failed with status code: {response.status_code}")
                break
            result = response.json().get('result', {})
            records = result.get('records', [])
            if not records:
                break
            all_records.extend(records)
            datasize = result.get('datasize', 0)
            offset += len(records)
            if offset >= datasize:
                break
        except Exception as e:
            print(f"Error fetching data at offset {offset}: {e}")
            break
            
    if not all_records:
        raise ValueError("Failed to fetch data from HKMA API.")
        
    df = pd.DataFrame(all_records)
    return df

if __name__ == '__main__':
    # 1. 获取并清洗数据
    df = fetch_hkma_data()
    df['end_of_month'] = pd.to_datetime(df['end_of_month'])
    df = df[df['end_of_month'] >= '2015-01-01'].sort_values('end_of_month').copy()

    # 转换数值类型
    df['aggr_balance'] = pd.to_numeric(df['aggr_balance'], errors='coerce')
    df['ef_bills_notes'] = pd.to_numeric(df['ef_bills_notes'], errors='coerce')

    # 计算加总项：银行体系总结余 + 外汇基金票据及债券未偿还总额
    df['total_liquidity'] = df['aggr_balance'] + df['ef_bills_notes']

    # 2. 绘制图表
    fig, ax1 = plt.subplots(figsize=(16, 8), dpi=300)
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')

    # 左轴 (L)：银行体系总结余 & 外汇基金票據及债券
    line1 = ax1.plot(df['end_of_month'], df['aggr_balance'], color='#17becf', linewidth=2, label='Aggregate Balance (L)')
    line2 = ax1.plot(df['end_of_month'], df['ef_bills_notes'], color='#d62728', linewidth=2, label='Exchange Fund Bills & Notes (L)')
    ax1.set_ylabel('Millions, HKD (Lines)', fontsize=11)
    ax1.set_xlabel('Date', fontsize=11)

    # 右轴 (R)：两者加总的柱状图
    ax2 = ax1.twinx()
    bar1 = ax2.bar(df['end_of_month'], df['total_liquidity'], width=25, color='#ffbb78', alpha=0.65, label='Aggregate Balance + EF Bills & Notes (R)')
    ax2.set_ylabel('Millions, HKD (Bar Sum)', fontsize=11)
    ax2.grid(False) # 避免右轴网格线重叠混乱

    # 图表标题与坐标轴美化
    ax1.set_title('Hong Kong Monetary Base - Aggregate Balance & Exchange Fund Bills & Notes (2015-2026)', fontsize=14, fontweight='bold', pad=15)
    ax1.set_xlim(pd.to_datetime('2015-01-01'), df['end_of_month'].max())
    ax1.xaxis.set_major_locator(mdates.YearLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))

    # 合并图例
    lines = line1 + line2 + [bar1]
    ax1.legend(lines, [l.get_label() for l in lines], loc='upper left', frameon=True, facecolor='white')

    plt.tight_layout()
    output_path = 'hk_monetary_base_2015_onwards.png'
    plt.savefig(output_path, dpi=300)
    print(f"Chart successfully generated and saved to: {output_path}")