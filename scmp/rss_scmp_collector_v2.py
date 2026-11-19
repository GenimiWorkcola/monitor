import socket
import requests
import os
import re
import json
from datetime import datetime, timezone, timedelta
import feedparser
from ebooklib import epub
from google import genai
from google.genai import types

# 確保工作目錄切換到腳本所在資料夾
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ==================== 時區設定 ====================
# 以香港時間（UTC+8，無夏令時）判定「當天」
LOCAL_TZ = timezone(timedelta(hours=8))

# 限制底層網路連線預設逾時（秒），避免 RSS 抓取或網路請求無限掛起
socket.setdefaulttimeout(20)

# ==================== 設定 Gemini API（從 GitHub Secrets 注入的環境變數讀取）====================
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "未檢測到 GEMINI_API_KEY 環境變數！"
        "請在 GitHub Actions workflow 中設定：env: GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}"
    )
client = genai.Client(
    api_key=API_KEY,
    http_options=types.HttpOptions(timeout=60000),
)

# ==================== 歷史文章庫設定 ====================
# 當日所有已抓取文章的累積存檔（JSON 格式，作為去重比對的依據）
ARCHIVE_FILE = "scmp_archive.json"

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

# 2. 严格中港跨境/宏观互动特许词（最高优先级放行）
HK_CHINA_STRICT_KEYWORDS = [
    'central government', 'beijing-hong kong',
    'one country, two systems', 'national development', 'five-year plan',
    'stock connect', 'bond connect', 'wealth management connect'
]

# 3A. 绝对排除词（无需绑定香港，出现即排除，如纯粹娱乐体育）
GLOBAL_EXCLUDE_KEYWORDS = [
    'entertainment', 'celebrity', 'movie', 'film', 'music', 'pop', 'fashion', 'lifestyle', 'singer',
    'sport', 'sports', 'football', 'soccer', 'basketball', 'tennis', 'olympics', 'marathon',
    'rugby sevens', 'horse racing', 'jockey club'
]

# 3B. 香港地域锚点词（用于识别文章是否与香港有关）
HK_GEO_ANCHORS = [
    'hong kong', 'hongkong', 'hk', 'kowloon', 'lantau',
    'wan chai', 'tsim sha tsui', 'mong kok', 'causeway bay'
]

# 3C. 条件排除词（必须同时命中【HK_GEO_ANCHORS】才触发过滤，彻底避免误伤美/法/内地新闻）
HK_CONTEXT_EXCLUDE_KEYWORDS = [
    # 行政长官与本地施政/立法会日常
    'chief executive', 'john lee', 'policy address', 'legco', 'legislative council',
    'executive council', 'exco', 'financial secretary', 'paul chan',
    'secretary for', 'bureau', 'civil servant', 'government headquarters',

    # 本地司法、警务、海关、消防与社会民生
    'district council', 'subsidised housing', 'public housing', 'mtr', 'kmb',
    'hospital authority', 'education bureau', 'dse', 'university of hong kong', 'hku',
    'traffic accident', 'traffic collision', 'burglary', 'scam victim', 'minor injury',
    'high court', 'district court', 'magistrates', 'icac', 'customs', 'fire services',
    'typhoon', 'black rainstorm', 'observatory', 'police'
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

    # 1. 最高優先級：中港跨境/宏觀互動特許詞，出現即放行
    for kw in HK_CHINA_STRICT_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', combined_text):
            return True

    # 2. 絕對排除詞：出現即排除（無需綁定香港）
    for kw in GLOBAL_EXCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', combined_text):
            return False

    # 3. 條件排除：必須同時命中香港地域錨點 + 本地議題詞才排除
    has_hk_anchor = any(
        re.search(r'\b' + re.escape(kw) + r'\b', combined_text)
        for kw in HK_GEO_ANCHORS
    )
    if has_hk_anchor:
        for kw in HK_CONTEXT_EXCLUDE_KEYWORDS:
            if re.search(r'\b' + re.escape(kw) + r'\b', combined_text):
                return False

    # 4. 基礎國家/大政方針關鍵詞
    for kw in INCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', combined_text):
            return True

    return False

def format_pub_date(raw_date):
    if not raw_date:
        return datetime.now(LOCAL_TZ).strftime('%Y-%m-%d')
    cleaned = re.sub(r'\s+\d{2}:\d{2}:\d{2}.*', '', raw_date)
    return cleaned.strip()

# ==================== 當天日期判定 ====================
def get_entry_datetime(entry):
    """取出文章發布時間（轉為 UTC 的 datetime）；無法判定則回傳 None"""
    parsed = getattr(entry, 'published_parsed', None)
    if not parsed:
        parsed = getattr(entry, 'updated_parsed', None)
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except Exception:
            return None
    return None

def is_published_today(entry):
    """僅接受「當天（香港時間）發布」的文章"""
    pub_dt = get_entry_datetime(entry)
    if pub_dt is None:
        return False
    today = datetime.now(LOCAL_TZ).date()
    return pub_dt.astimezone(LOCAL_TZ).date() == today

# ==================== 歷史文章庫讀寫 ====================
def load_archive():
    """讀取當日歷史文章庫；若檔案不存在或損毀則回傳空列表"""
    if not os.path.exists(ARCHIVE_FILE):
        return []
    try:
        with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            print(f"已載入歷史文章庫：共 {len(data)} 篇舊文章。")
            return data
    except Exception as e:
        print(f"[警告] 讀取歷史文章庫失敗 ({e})，將從空庫開始。")
    return []

def save_archive(articles):
    """把完整文章庫（舊 + 新）寫回 JSON，供下次執行時比對去重"""
    with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"歷史文章庫已更新：共 {len(articles)} 篇。")

def build_known_keys(archive):
    """建立已存在文章的指紋集合，用 link 與標題雙重比對"""
    keys = set()
    for art in archive:
        link = art.get('link', '')
        title = art.get('title_en', '').strip().lower()
        if link:
            keys.add(('link', link))
        if title:
            keys.add(('title', title))
    return keys

def batch_translate_articles(articles):
    """將所有篩選出的「新」文章打包成單一 Prompt 一次性翻譯；若失敗則自動保留英文原文"""
    if not articles:
        return articles

    print(f"[{datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')}] 正在將 {len(articles)} 篇「新」文章打包，發送至 Gemini 進行批量翻譯...")

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
            pattern = rf'--- ARTICLE {idx} ---\s*TITLE:\s*(.*?)\s*CONTENT:\s*(.*?)(?=(?:--- ARTICLE \d+ ---)|\Z)'
            match = re.search(pattern, response_text, re.DOTALL)

            if match:
                art['title_display'] = match.group(1).strip()
                art['content_display'] = match.group(2).strip()
                success_count += 1
            else:
                art['title_display'] = art['title_en']
                art['content_display'] = art['content_en']

        print(f"批量翻譯完成！成功解析 {success_count}/{len(articles)} 篇。")

    except Exception as e:
        print(f"[警告] 批量呼叫 Gemini API 發生錯誤 ({e})。已啟動降級機制，全面保留英文原文輸出！")
        for art in articles:
            art['title_display'] = art['title_en']
            art['content_display'] = art['content_en']

    return articles

def fetch_new_articles(known_keys):
    """抓取 RSS，只保留「當天發布」且「不重複」的新文章"""
    processed_links = set()
    new_articles = []
    skipped_not_today = 0   # 非當天發布而跳過的數量
    skipped_no_date = 0     # 無法判定日期而跳過的數量

    print(f"[{datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')}] 開始深度抓取 RSS 全文...")
    for url in RSS_URLS:
        source_xml = url.split('/')[-1]  # 例如 scmp-news.xml
        try:
            feed = feedparser.parse(url)
            print(f"成功拉取: {source_xml} (包含 {len(feed.entries)} 條消息)")
            for entry in feed.entries:
                link = getattr(entry, 'link', '')
                title = getattr(entry, 'title', '')

                # ===== 去重比對：與本次已處理 + 歷史文章庫 雙重比對 =====
                if link in processed_links:
                    continue
                if ('link', link) in known_keys:
                    continue
                if ('title', title.strip().lower()) in known_keys:
                    continue

                # ===== 只抓取「當天（香港時間）發布」的文章 =====
                if not is_published_today(entry):
                    if get_entry_datetime(entry) is None:
                        skipped_no_date += 1
                        print(f" -> 跳過（無法判定發布日期）[{source_xml}]: {title[:30]}...")
                    else:
                        skipped_not_today += 1
                    processed_links.add(link)
                    continue

                raw_content = ""
                if hasattr(entry, 'content') and entry.content:
                    raw_content = entry.content[0].get('value', '')
                elif hasattr(entry, 'summary_detail') and entry.summary_detail:
                    raw_content = entry.summary_detail.get('value', '')

                if not raw_content:
                    raw_content = getattr(entry, 'summary', getattr(entry, 'description', ''))

                full_content = clean_html(raw_content)

                if is_relevant_article(title, full_content):
                    print(f" -> 新文章命中 [{source_xml}]: {title[:30]}...")

                    raw_date = getattr(entry, 'published', datetime.now(LOCAL_TZ).strftime('%a, %d %b %Y %H:%M:%S +0000'))
                    pub_date_clean = format_pub_date(raw_date)

                    article_item = {
                        "link": link,                      # 歸檔用：去重指紋
                        "title_en": title,
                        "content_en": full_content,
                        "pub_date": pub_date_clean,
                        "source_tag": source_xml,          # 記錄來源 xml 檔名
                        "fetched_at": datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')
                    }
                    new_articles.append(article_item)

                # 無論是否入選，都標記為本次已處理（同一次執行內不重複檢查）
                processed_links.add(link)
        except Exception as e:
            print(f"[錯誤] 解析 {url} 失敗: {e}")

    print(f"本次抓取完成：共發現 {len(new_articles)} 篇當天發布且不重複的新文章。")
    print(f"（跳過：非當天發布 {skipped_not_today} 篇 / 無法判定日期 {skipped_no_date} 篇）")
    return new_articles

def save_to_epub(articles):
    """把「完整文章庫（當日所有歷史文章 + 最新文章）」打包成帶有超連結目錄 (TOC) 的 EPUB"""
    if not articles:
        print("本次沒有任何文章，未生成 EPUB。")
        return

    today_str = datetime.now(LOCAL_TZ).strftime('%Y-%m-%d')
    epub_filename = f"scmp_{today_str}.epub"

    book = epub.EpubBook()
    book.set_identifier(f'scmp-daily-{today_str}')
    book.set_title(f'SCMP 每日精選新聞 - {today_str}（共 {len(articles)} 篇）')
    book.set_language('zh')
    book.add_author('SCMP Auto Collector')

    chapters = []
    nav_links = []  # 目錄條目：標題 + 來源 xml

    for idx, art in enumerate(articles, start=1):
        title_to_use = art.get('title_display', art['title_en'])
        content_to_use = art.get('content_display', art['content_en'])
        source_xml = art.get('source_tag', '')

        c = epub.EpubHtml(
            title=title_to_use,
            file_name=f'chap_{idx}.xhtml',
            lang='zh'
        )

        paragraphs = content_to_use.split('\n')
        html_p = ''.join([f'<p style="text-indent: 2em; line-height: 1; margin-bottom: 0.4em; margin-top: 0;">{p.strip()}</p>' for p in paragraphs if p.strip()])

        # 文章內「返回目錄」連結：直接定位到目錄頁中本文章所在的位置（錨點 #article-N）
        c.content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"/>
            <title>{title_to_use}</title>
        </head>
        <body style="font-family: Times New Roman; padding: 5px; margin: 0;">
            <h2 style="border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-bottom: 6px; font-size: 1.2em;">{title_to_use}</h2>
            <p style="color: #555; font-size: 0.8em; margin: 2px 0;"><strong>Source:</strong> {source_xml}</p>
            <p style="color: #555; font-size: 0.8em; margin: 2px 0 8px 0;"><strong>Date:</strong> {art['pub_date']}</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 5px 0;" />
            <div>{html_p}</div>
            <div style="margin-top: 20px; text-align: center;">
                <hr style="border: none; border-top: 1px dashed #ccc; margin: 10px 0;" />
                <a href="nav.xhtml#article-{idx}" style="font-size: 0.9em; text-decoration: none; color: #0066cc;">返回目錄 (Table of Contents)</a>
            </div>
        </body>
        </html>
        """

        book.add_item(c)
        chapters.append(c)

        # 目錄條目：文章標題後方標註來源 xml
        toc_label = f"{title_to_use} <{source_xml}>" if source_xml else title_to_use
        nav_links.append(epub.Link(f'chap_{idx}.xhtml', toc_label, f'chap_{idx}'))

    # ==================== 自訂可點擊目錄頁（每條目後附來源 xml，並設置錨點 id="article-N"）====================
    toc_page = epub.EpubHtml(title='目錄 Table of Contents', file_name='nav.xhtml', lang='zh')
    toc_items = []
    for idx, art in enumerate(articles, start=1):
        title_to_use = art.get('title_display', art['title_en'])
        source_xml = art.get('source_tag', '')
        date_str = art.get('pub_date', '')
        source_badge = f'<span style="color: #0066cc; font-size: 0.75em;">[{source_xml}]</span>' if source_xml else ''
        date_badge = f'<span style="color: #888; font-size: 0.75em;">{date_str}</span>' if date_str else ''
        # 每個目錄條目加上錨點 id="article-N"，供文章頁「返回目錄」精確定位
        toc_items.append(
            f'<li id="article-{idx}" style="margin-bottom: 8px; line-height: 1.5;">'
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
    <body style="font-family: sans-serif; padding: 10px; margin: 0;">
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
    print(f"已成功生成完整 EPUB 檔案: {epub_filename}（含歷史文章共 {len(articles)} 篇）")

if __name__ == "__main__":
    # 1. 讀取當日歷史文章庫（先前已抓取並翻譯過的文章）
    archive = load_archive()

    # 2. 建立已存在文章的指紋（link + 標題），用於去重比對
    known_keys = build_known_keys(archive)

    # 3. 抓取 RSS，只保留「當天發布」且「不重複」的新文章
    new_articles = fetch_new_articles(known_keys)

    # 4. 僅對新文章進行翻譯（無新文章時跳過 API 呼叫）
    if new_articles:
        new_articles = batch_translate_articles(new_articles)
        # 5. 把新文章「追加」進歷史文章庫，並寫回存檔
        archive.extend(new_articles)
        save_archive(archive)
    else:
        print("本次沒有新文章，歷史文章庫維持不變。")

    # 6. 每次都重新生成完整大 EPUB：當日所有歷史文章 + 最新文章
    save_to_epub(archive)
