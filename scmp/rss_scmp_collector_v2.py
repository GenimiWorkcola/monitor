import time
import os
import re
from datetime import datetime
import feedparser
from google import genai
from ebooklib import epub

# 確保工作目錄切換到 scmp 資料夾
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

# 包含關鍵字（包含中國、美國或法國）
INCLUDE_KEYWORDS = [
    'china', 'chinese', 'beijing',
    'us', 'usa', 'united states', 'america', 'american', 'washington',
    'france', 'french', 'paris'
]

# 排除關鍵字（排除香港本地、娛樂、體育相關）
EXCLUDE_KEYWORDS = [
    'hong kong', 'hk', 'local',
    'entertainment', 'celebrity', 'movie', 'film', 'music', 'pop', 'fashion', 'lifestyle',
    'sport', 'sports', 'football', 'soccer', 'basketball', 'tennis', 'olympics', 'marathon'
]

def clean_html(raw_html):
    """徹底清除 HTML 標籤，保留純文字結構"""
    if not raw_html:
        return ""
    # 將換行標籤轉為真實換行，方便後續排版
    raw_html = re.sub(r'</p>|<br\s*/?>', '\n', raw_html)
    clean_re = re.compile('<.*?>')
    text = re.sub(clean_re, '', raw_html)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)

def is_relevant_article(title, content):
    """篩選邏輯：必須包含目標國家關鍵字，且不能包含排除關鍵字"""
    combined_text = f"{title} {content}".lower()
    for ex_kw in EXCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(ex_kw) + r'\b', combined_text):
            return False
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
    
    for attempt in range(3):
        try:
            # 使用穩定且推薦的 gemini-1.5-flash 模型
            response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents=prompt,
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            print(f"[警告] Gemini 翻譯出錯 (嘗試 {attempt+1}/3): {e}")
            time.sleep(2)
            
    return text  # 若多次失敗則降級返回原文

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
        try:
            feed = feedparser.parse(url)
            print(f"成功拉取: {url} (包含 {len(feed.entries)} 條消息)")
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
                    print(f" -> 匹配成功，正在由 Gemini 完整翻譯: {title[:40]}...")
                    summary_zh = translate_text(full_content)
                    
                    raw_date = getattr(entry, 'published', datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000'))
                    pub_date_clean = format_pub_date(raw_date)
                    
                    article_item = {
                        "link": link,
                        "title_en": title,
                        "summary_zh": summary_zh,
                        "pub_date": pub_date_clean
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
        # 建立單個章節的 HTML 檔案
        c = epub.EpubHtml(
            title=art['title_en'], 
            file_name=f'chap_{idx}.xhtml', 
            lang='zh'
        )
        
        # 處理正文段落，轉成適合電子書閱讀的 HTML P 標籤
        paragraphs = art['summary_zh'].split('\n')
        html_p = ''.join([f'<p style="text-indent: 2em; line-height: 1.6; margin-bottom: 0.8em;">{p.strip()}</p>' for p in paragraphs if p.strip()])
        
        c.content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"/>
            <title>{art['title_en']}</title>
        </head>
        <body style="font-family: sans-serif; padding: 10px;">
            <h2 style="border-bottom: 1px solid #ccc; padding-bottom: 5px;">{art['title_en']}</h2>
            <p style="color: #666; font-size: 0.9em;"><strong>發布日期：</strong>{art['pub_date']}</p>
            <hr/>
            <div>{html_p}</div>
        </body>
        </html>
        """
        
        book.add_item(c)
        chapters.append(c)
        toc.append(c)

    # 設定電子書的目錄樹 (Table of Contents)， Kindle 上點擊目錄可跳轉
    book.toc = tuple(toc)

    # 新增標準的導航檔案 (NCX & NAV)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # 設定閱讀順序
    book.spine = ['nav'] + chapters

    # 寫出 EPUB 檔案
    epub.write_epub(epub_filename, book, {})
    print(f"已成功生成帶有超連結目錄的 EPUB 檔案: {epub_filename}")

def main():
    print("RSS 全文 Gemini 翻譯 & EPUB 生成服務已啟動...")
    articles = fetch_and_process()
    save_to_epub(articles)

if __name__ == "__main__":
    main()