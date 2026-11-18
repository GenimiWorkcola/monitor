import os
import re
from datetime import datetime
import feedparser
from ebooklib import epub

# 確保工作目錄切換到腳本所在資料夾
os.chdir(os.path.dirname(os.path.abspath(__file__)))

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

def fetch_and_process():
    processed_links = set()
    raw_articles = []

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始深度抓取 RSS 全文...")
    for url in RSS_URLS:
        source_xml = url.split('/')[-1]  # 例如 scmp-news.xml
        try:
            feed = feedparser.parse(url)
            print(f"成功拉取: {source_xml} (包含 {len(feed.entries)} 條消息)")
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
                    print(f" -> 命中文章 [{source_xml}]: {title[:30]}...")

                    raw_date = getattr(entry, 'published', datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000'))
                    pub_date_clean = format_pub_date(raw_date)

                    article_item = {
                        "title_en": title,
                        "content_en": full_content,
                        "pub_date": pub_date_clean,
                        "source_tag": source_xml  # 記錄來源 xml 檔名
                    }
                    raw_articles.append(article_item)
        except Exception as e:
            print(f"[錯誤] 解析 {url} 失敗: {e}")

    return raw_articles

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
    book.set_language('en')
    book.add_author('SCMP Auto Collector')

    chapters = []
    nav_links = []  # 目錄條目：標題 + 來源 xml

    for idx, art in enumerate(articles, start=1):
        title_to_use = art['title_en']
        content_to_use = art['content_en']
        source_xml = art.get('source_tag', '')

        c = epub.EpubHtml(
            title=title_to_use,
            file_name=f'chap_{idx}.xhtml',
            lang='en'
        )

        paragraphs = content_to_use.split('\n')
        html_p = ''.join([f'<p style="text-indent: 2em; line-height: 1; margin-bottom: 0.4em; margin-top: 0;">{p.strip()}</p>' for p in paragraphs if p.strip()])

        c.content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"/>
            <title>{title_to_use}</title>
        </head>
        <body style="font-family: 'Times New Roman', Times, serif; padding: 5px; margin: 0;">
            <h2 style="border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-bottom: 6px; font-size: 1.2em;">{title_to_use}</h2>
            <p style="color: #555; font-size: 0.8em; margin: 2px 0;"><strong>Source:</strong> {source_xml}</p>
            <p style="color: #555; font-size: 0.8em; margin: 2px 0 8px 0;"><strong>Date:</strong> {art['pub_date']}</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 5px 0;" />
            <div>{html_p}</div>
            <div style="margin-top: 20px; text-align: center;">
                <hr style="border: none; border-top: 1px dashed #ccc; margin: 10px 0;" />
                <a href="nav.xhtml" style="font-size: 0.9em; text-decoration: none; color: #0066cc;">返回目錄 (Table of Contents)</a>
            </div>
        </body>
        </html>
        """

        book.add_item(c)
        chapters.append(c)

        # 目錄條目：文章標題後方標註來源 xml
        toc_label = f"{title_to_use} <{source_xml}>" if source_xml else title_to_use
        nav_links.append(epub.Link(f'chap_{idx}.xhtml', toc_label, f'chap_{idx}'))

    # ==================== 自訂可點擊目錄頁（每條目後附來源 xml）====================
    toc_page = epub.EpubHtml(title='Table of Contents', file_name='nav.xhtml', lang='en')
    toc_items = []
    for idx, art in enumerate(articles, start=1):
        title_to_use = art.get('title_display', art['title_en'])
        source_xml = art.get('source_tag', '')
        date_str = art.get('pub_date', '')
        source_badge = f'<span style="color: #0066cc; font-size: 0.75em;">[{source_xml}]</span>' if source_xml else ''
        date_badge = f'<span style="color: #888; font-size: 0.75em;">{date_str}</span>' if date_str else ''
        toc_items.append(
            f'<li style="margin-bottom: 8px; line-height: 1.5;">'
            f'<a href="chap_{idx}.xhtml" style="text-decoration: none; color: #333;">{idx}. {title_to_use}</a> '
            f'{source_badge} {date_badge}</li>'
        )
    toc_page.content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8"/>
        <title>目錄 Table of Contents</title>
    </head>
    <body style="font-family: 'Times New Roman', Times, serif; padding: 10px; margin: 0;">
        <h2 style="border-bottom: 2px solid #0066cc; padding-bottom: 6px;">目錄（共 {len(articles)} 篇）</h2>
        <ol style="padding-left: 20px;">{''.join(toc_items)}</ol>
    </body>
    </html>
    """
    book.add_item(toc_page)

    # EPUB3 導航 (nav.xhtml) 與 NCX 目錄：條目同樣附來源 xml
    book.toc = tuple(nav_links)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters

    epub.write_epub(epub_filename, book, {})
    print(f"已成功生成 EPUB 檔案: {epub_filename}")

if __name__ == "__main__":
    articles = fetch_and_process()
    save_to_epub(articles)
