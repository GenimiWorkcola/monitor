import os
import re
from datetime import datetime
import feedparser
from ebooklib import epub
from google import genai
from google.genai import types

# 確保工作目錄切換到腳本所在資料夾
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ==================== 設定 Gemini API ====================
API_KEY = os.environ.get("GEMINI_API_KEY", "在此處填入您的_GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)

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

# 3. 娱乐、体育与纯本地民生/社区的排除关键词
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
    combined_text = f"{title} {content}".lower()
    
    for ex_kw in EXCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(ex_kw) + r'\b', combined_text):
            return False

    for hk_cn_kw in HK_CHINA_KEYWORDS:
        if re.search(r'\b' + re.escape(hk_cn_kw) + r'\b', combined_text):
            return True

    for inc_kw in INCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(inc_kw) + r'\b', combined_text):
            return True

    return False

def format_pub_date(raw_date):
    if not raw_date:
        return datetime.now().strftime('%Y-%m-%d')
    cleaned = re.sub(r'\s+\d{2}:\d{2}:\d{2}.*', '', raw_date)
    return cleaned.strip()

def batch_translate_articles(articles):
    """將所有篩選出的文章打包成單一 Prompt 一次性翻譯；若失敗則自動保留英文原文"""
    if not articles:
        return articles

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在將 {len(articles)} 篇文章打包，發送至 Gemini 進行批量翻譯...")

    combined_input = ""
    for idx, art in enumerate(articles, start=1):
        combined_input += f"--- ARTICLE {idx} ---\n"
        combined_input += f"TITLE: {art['title_en']}\n"
        combined_input += f"CONTENT:\n{art['content_en']}\n\n"

    prompt = f"""
你是一位專業的新聞翻譯家。請將以下多篇英文新聞標題與內文全部翻譯成專業、流暢、信實的繁體中文。
【格式嚴格要求】
1. 請維持與原本相對應的編號格式（例如 `--- ARTICLE 1 ---`、`TITLE:`、`CONTENT:`）。
2. 每篇文章請明確標示對應的 `--- ARTICLE X ---`。
3. 確保所有文章都被完整翻譯，不要省略任何文章或內文段落。

待翻譯內容：
{combined_input}
"""

    try:
        response = client.models.generate_content(
            model='gemini-3.8-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
            )
        )
        response_text = response.text

        success_count = 0
        for idx, art in enumerate(articles, start=1):
            pattern = rf'--- ARTICLE {idx} ---\s*TITLE:\s*(.*?)\s*CONTENT:\s*(.*?)(?=(?:--- ARTICLE \d+ ---)|$)'
            match = re.search(pattern, response_text, re.DOTALL)
            
            if match:
                art['title_display'] = match.group(1).strip()
                art['content_display'] = match.group(2).strip()
                success_count += 1
            else:
                # 單篇解析失敗時降級保留原文
                art['title_display'] = art['title_en']
                art['content_display'] = art['content_en']

        print(f"批量翻譯完成！成功解析 {success_count}/{len(articles)} 篇。")

    except Exception as e:
        # 整體呼叫失敗時的降級防護：保留英文全文輸出，不中斷程式
        print(f"[警告] 批量呼叫 Gemini API 發生錯誤 ({e})。已啟動降級機制，全面保留英文原文輸出！")
        for art in articles:
            art['title_display'] = art['title_en']
            art['content_display'] = art['content_en']

    return articles

def fetch_and_process():
    processed_links = set()
    raw_articles = []
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始深度抓取 RSS 全文...")
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
                    print(f" -> 命中文章: {title[:30]}...")
                    
                    raw_date = getattr(entry, 'published', datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000'))
                    pub_date_clean = format_pub_date(raw_date)
                    
                    article_item = {
                        "title_en": title,
                        "content_en": full_content,
                        "pub_date": pub_date_clean
                    }
                    raw_articles.append(article_item)
        except Exception as e:
            print(f"[錯誤] 解析 {url} 失敗: {e}")

    # 進行批量翻譯（內含 API 異常時保留英文原文的降級防護）
    processed_articles = batch_translate_articles(raw_articles)
    return processed_articles

def save_to_epub(articles):
    """把文章打包成帶有超連結目錄 (TOC) 的 EPUB 電子書（不含任何 source 網址）"""
    if not articles:
        print("本次沒有篩選出符合條件的新文章。")
        return
        
    today_str = datetime.now().strftime('%Y-%m-%d')
    epub_filename = f"scmp_{today_str}.epub"
    
    book = epub.EpubBook()
    book.set_identifier(f'scmp-daily-{today_str}')
    book.set_title(f'SCMP 每日精選新聞 - {today_str}')
    book.set_language('zh')
    book.add_author('Gemini Auto Collector')

    chapters = []
    toc = []

    for idx, art in enumerate(articles, start=1):
        c = epub.EpubHtml(
            title=art['title_display'], 
            file_name=f'chap_{idx}.xhtml', 
            lang='zh'
        )
        
        paragraphs = art['content_display'].split('\n')
        html_p = ''.join([f'<p style="text-indent: 2em; line-height: 1.6; margin-bottom: 0.6em; margin-top: 0;">{p.strip()}</p>' for p in paragraphs if p.strip()])
        
        c.content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"/>
            <title>{art['title_display']}</title>
        </head>
        <body style="font-family: sans-serif; padding: 10px; margin: 0;">
            <h2 style="border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-bottom: 6px; font-size: 1.2em;">{art['title_display']}</h2>
            <p style="color: #555; font-size: 0.8em; margin: 2px 0 12px 0;"><strong>發布日期:</strong> {art['pub_date']}</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 5px 0;" />
            <div style="margin-top: 10px;">{html_p}</div>
            <div style="margin-top: 30px; text-align: center;">
                <hr style="border: none; border-top: 1px dashed #ccc; margin: 10px 0;" />
                <a href="nav.xhtml" style="font-size: 0.9em; text-decoration: none; color: #0066cc;">返回目錄 (Table of Contents)</a>
            </div>
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
    print(f"已成功生成 EPUB 檔案: {epub_filename}（未包含任何來源網址）")

def main():
    print("RSS 全文處理服務已啟動...")
    articles = fetch_and_process()
    save_to_epub(articles)

if __name__ == "__main__":
    main()