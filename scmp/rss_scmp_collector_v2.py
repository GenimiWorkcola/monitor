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
    print("[警告] 未檢測到 GEMINI_API_KEY，將跳過翻譯保留原文。")
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
    # 娱乐、流行文化
    'entertainment', 'celebrity', 'movie', 'film', 'music', 'pop', 'fashion', 'lifestyle',
    # 体育
    'sport', 'sports', 'football', 'soccer', 'basketball', 'tennis', 'olympics', 'marathon',
    # 纯本地民生/交通/社区事件（过滤无宏观意义的本地新闻）
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
    
    # 优先检测：是否包含硬性排除词（娱乐、体育、微观社区治安）
    for ex_kw in EXCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(ex_kw) + r'\b', combined_text):
            return False

    # 特许检测：是否涉及中国香港关系/大湾区/国家安全等重磅议题
    for hk_cn_kw in HK_CHINA_KEYWORDS:
        if re.search(r'\b' + re.escape(hk_cn_kw) + r'\b', combined_text):
            return True

    # 常规检测：是否包含中美法等目标国家词汇
    for inc_kw in INCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(inc_kw) + r'\b', combined_text):
            return True

    return False

def translate_text(text):
    """使用 Gemini API 進行高品質、流暢的全文翻譯"""
    if not text:
        return ""
    if not client:
        print("[警告] 未檢測到 GEMINI_API_KEY，跳過翻譯保留原文。")
        return text
    
    prompt = (
        "請將以下英文新聞全文翻譯為專業、流暢、地道的中文新聞。 "
        "直接輸出翻譯後的中文內容，不要加任何多餘的解釋說明或客套話：\n\n" + text
    )
    
    # 模型的容错机制
    candidate_models = ['gemini-3.8-flash', 'gemini-2.5-flash', 'gemini-1.5-flash']
    
    for model_name in candidate_models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                print(f"[警告] 嘗試模型 {model_name} (第 {attempt+1} 次) 出錯: {e}")
                time.sleep(2)
            
    return text

def format_pub_date(raw_date):
    """精簡時間格式，僅保留日期（如 Wed, 04 Nov 2026）"""
    if not raw_date:
        return datetime.now().strftime('%a, %d %b %Y')
    cleaned = re.sub(r'\s+\d{2}:\d{2}:\d{2}.*', '', raw_date)
    return cleaned.strip()

def fetch_and_process():
    processed_links = set()
    new_articles = []
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始深度抓取 RSS 全文並使用 Gemini 翻譯...")
    for url in RSS_URLS:
        source_name = url.split('/')[-1]
        try:
            feed = feedparser.parse(url)
            print(f"成功拉取: {source_name} (包含 {len(feed.entries)} 條消息)")
            for entry in feed.entries:
                link = getattr(entry, 'link', '')
                if link in processed_links:
                    continue
                processed_links.add(link)
                
                title = getattr(entry, 'title', '')
                
                raw_content = ""
                if hasattr(entry, 'content') and entry.content:
                    raw_content = entry.content[0].get('value', '')
                elif hasattr(entry, 'summary_detail') and entry.summary_detail:
                    raw_content = entry.summary_detail.get('value', '')
                
                if not raw_content:
                    raw_content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                
                full_content = clean_html(raw_content)
                
                if is_relevant_article(title, full_content):
                    print(f" -> 匹配成功 [{source_name}]，正在由 Gemini 翻譯: {title[:30]}...")
                    summary_zh = translate_text(full_content)
                    
                    raw_date = getattr(entry, 'published', datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000'))
                    pub_date_clean = format_pub_date(raw_date)
                    
                    article_item = {
                        "link": link,
                        "title_en": title,
                        "summary_zh": summary_zh,
                        "pub_date": pub_date_clean,
                        "source_xml": source_name
                    }
                    new_articles.append(article_item)
        except Exception as e:
            print(f"[錯誤] 解析 {url} 失敗: {e}")

    return new_articles

def save_to_epub(articles):
    """把文章打包成帶有超連結目錄 (TOC) 的 EPUB 電子書"""
    if not articles:
        print("本次沒有篩選出符合條件的新文章。")
        return
        
    today_str = datetime.now().strftime('%Y-%m-%d')
    epub_filename = f"scmp_{today_str}.epub"
    
    book = epub.EpubBook()
    book.set_identifier(f'scmp-daily-{today_str}')
    book.set_title(f'SCMP 每日精選新聞 - {today_str}')
    book.set_language('zh')
    book.add_author('SCMP Auto Collector')

    chapters = []
    toc = []

    for idx, art in enumerate(articles, start=1):
        c = epub.EpubHtml(
            title=art['title_en'], 
            file_name=f'chap_{idx}.xhtml', 
            lang='zh'
        )
        
        paragraphs = art['summary_zh'].split('\n')
        html_p = ''.join([f'<p style="text-indent: 2em; line-height: 1.25; margin-bottom: 0.4em; margin-top: 0;">{p.strip()}</p>' for p in paragraphs if p.strip()])
        
        c.content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"/>
            <title>{art['title_en']}</title>
        </head>
        <body style="font-family: sans-serif; padding: 5px; margin: 0;">
            <h2 style="border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-bottom: 6px; font-size: 1.2em;">{art['title_en']}</h2>
            <p style="color: #555; font-size: 0.8em; margin: 2px 0;"><strong>來源欄目：</strong>{art['source_xml']}</p>
            <p style="color: #555; font-size: 0.8em; margin: 2px 0 8px 0;"><strong>發布日期：</strong>{art['pub_date']}</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 5px 0;" />
            <div>{html_p}</div>
        </body>
        </html>
        """
        
        book.add_item(c)
        chapters.append(c)
        toc.append(c)

    book.toc = tuple(toc)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters

    epub.write_epub(epub_filename, book, {})
    print(f"已成功生成 EPUB 檔案: {epub_filename}（行距已收窄，並已標註來源 XML）")

def main():
    print("RSS 全文 Gemini 翻譯 & EPUB 生成服務已啟動...")
    articles = fetch_and_process()
    save_to_epub(articles)

if __name__ == "__main__":
    main()