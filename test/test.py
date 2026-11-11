import pandas as pd

# 1. 讀取兩個 CSV 檔案
# 請確保檔案與程式在同一目錄下，或填入正確的路徑
df1 = pd.read_csv('Korea KOSPI.csv')
df2 = pd.read_csv('韩国KOSPI指数历史数据.csv')

# 2. 清理欄位名稱（去除可能存在的前後空白）
df1.columns = [c.strip() for c in df1.columns]
df2.columns = [c.strip() for c in df2.columns]

# 3. 統一解析日期欄位
df1['Date_parsed'] = pd.to_datetime(df1['Date'])
df2['Date_parsed'] = pd.to_datetime(df2['日期'])

# 4. 清理數值欄位中的千分位逗號（以 df2 為例）
for col in ['收盘', '开盘', '高', '低']:
    if col in df2.columns:
        df2[col] = df2[col].astype(str).str.replace(',', '').astype(float)

# 5. 將兩張表轉換為標準化欄位格式
# 標準欄位：日期 (Date)、开盘 (Open)、最高 (High)、最低 (Low)、收盘 (Close)、交易量 (Volume)、涨跌幅 (Change)
df1_std = pd.DataFrame({
    'Date_parsed': df1['Date_parsed'],
    '日期': df1['Date_parsed'].dt.strftime('%Y-%m-%d'),
    '开盘': df1['Open'],
    '最高': df1['High'],
    '最低': df1['Low'],
    '收盘': df1['Close'],
    '交易量': None,
    '涨跌幅': None
})

df2_std = pd.DataFrame({
    'Date_parsed': df2['Date_parsed'],
    '日期': df2['Date_parsed'].dt.strftime('%Y-%m-%d'),
    '开盘': df2['开盘'],
    '最高': df2['高'],
    '最低': df2['低'],
    '收盘': df2['收盘'],
    '交易量': df2['交易量'],
    '涨跌幅': df2['涨跌幅']
})

# 6. 合并資料並依日期排序
combined = pd.concat([df2_std, df1_std], ignore_index=True)
combined = combined.sort_values(by='Date_parsed', ascending=False)

# 7. 依日期去重（若有重複日期，優先保留排在前面的 df2 資料，因其包含交易量與漲跌幅）
combined = combined.drop_duplicates(subset=['Date_parsed'], keep='first')

# 8. 篩選 2025 年 1 月 1 日至今的數據
df_2025_plus = combined[combined['Date_parsed'] >= '2025-01-01'].copy()
df_2025_plus = df_2025_plus.sort_values(by='Date_parsed', ascending=False)

# 9. 移除輔助用日期欄位，並導出結果
final_df = df_2025_plus.drop(columns=['Date_parsed'])
output_filename = 'KOSPI_2025_to_present_merged.csv'
final_df.to_csv(output_filename, index=False, encoding='utf-8-sig')

print(f"數據合併完成！總行數：{len(final_df)} 行")
print(f"數據範圍：{final_df['日期'].min()} 至 {final_df['日期'].max()}")
print(f"已成功儲存至：{output_filename}")