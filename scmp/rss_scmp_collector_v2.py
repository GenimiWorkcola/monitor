import time
import os
import re
from datetime import datetime
import feedparser
from google import genai
from ebooklib import epub

# 確保工作目錄切換到 scmp 檔案夾
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 初始化 Gemini 客戶端（自動讀取 GitHub Secrets 裡的 GEMINI_API_KEY）
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("[警告] 未檢測到 GEMINI_API_KEY，將無法進行翻譯。")
    client = None
else:
    client = genai.Client(api_key=api_key)

# 訂閱源列表（自動去重）
RSS_URLS = list(set([
    "https://feed.quanwenrss.com/feed/scmp-news.xml",
    "https://feed.quanwenrss.com/feed/scmp-opinion.xml",
    "https://feed.quanwenrss.com/feed/scmp-economy.xml",
    "https://feed.quanwenrss.com/feed/scmp-country-reports.xml",
    "https://feed.quanwenrss.com/feed/scmp-tech.xml"
]))

# 1. 基础国家/大政方针包含关键词
INCLUDE_KEYWORDS = [
    'china', 'chinese', 'beijing',
    'us', 'usa', 'united states', 'america', 'american', 'washington',
    'france', 'french', 'paris'
]

# 2. 专门针对“中国香港 / 内地与香港关系”的特许包含词（优先保留）
HK_CHINA_KEYWORDS = [
    'china hong kong', 'hong kong china', 'mainland-hong kong', 
    'hong kong-mainland', 'greater bay area', 'central government',
    'national security law', 'article 23', 'one country, two systems'
]

# 3. 娱乐、体育与纯本地民生/社区的排除关键词（不含通用 hong kong）
EXCLUDE_KEYWORDS = [
    'entertainment', 'celebrity', 'movie', 'film', 'music', 'pop', 'fashion', 'lifestyle',
    'sport', 'sports', 'football', 'soccer', 'basketball', 'tennis', 'olympics', 'marathon',
    'district council', 'subsidised housing', 'public housing', 'traffic accident',
    'traffic collision', 'burglary', 'scam victim', 'minor injury'
]

def clean_html(raw_html):
    """徹底清除 HTML 標籤，保留純文字結構"""
    if not raw_html:
        return ""
    raw_html = re.sub(r'</p>|<br\s*/?>', '\n', raw_html)
    clean_re = re.compile('<.*?>')
    text = re.sub(clean_re, '', raw_html)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)

def is_relevant_article(title, content):
    """
    智能篩選邏輯：
    1. 含有娛樂、體育、本地微觀治安/民生詞 -> 排除
    2. 包含“中國香港/大灣區/兩岸/中央”專屬聯動詞 -> 優先保留
    3. 包含目標國家（中國、美國、法國）關鍵字 -> 保留
    """
    combined_text = f"{title} {content}".lower()
    
    for ex_kw in EXCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(ex_kw) + r'\b', combined_text):
            return False

    for hk_cn_kw in HK_CHINA_KEYWORDS:
        if re.search(r'\b' + re.escape(hk_cn_kw) + r'\b', combined_text):
            return True

    for inc_kw in INCLUDE_KEYWORDS:
        if re