import time
import os
import re
from datetime import datetime
import feedparser
from deep_translator import MyMemoryTranslator

# 确保无论在哪执行，工作目录都切换到该脚本所在的文件夹（即 scmp 文件夹）
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 订阅源列表（自动去重）
RSS_URLS = list(set([
    "https://feed.quanwenrss.com/feed/scmp-news.xml",
    "https://feed.quanwenrss.com/feed/scmp-opinion.xml",
    "https://feed.quanwenrss.com/feed/scmp-economy.xml",
    "https://feed.quanwenrss.com/feed/scmp-country-reports.xml",
    "https://feed.quanwenrss.com/feed/scmp-tech.xml"
]))

# 包含关键词（包含中国、美国或法国）
INCLUDE_KEYWORDS = [
    'china', 'chinese', 'beijing',
    'us', 'usa', 'united states', 'america', 'american', 'washington',
    'france', 'french', 'paris'
]

# 排除关键词（排除香港本地、娱乐、体育相关）
EXCLUDE_KEYWORDS = [
    'hong kong', 'hk', 'local',
    'entertainment', 'celebrity', 'movie', 'film', 'music', 'pop', 'fashion', 'lifestyle',
    'sport', 'sports', 'football', 'soccer', 'basketball', 'tennis', 'olympics', 'marathon'
]

def clean_html(raw_html):
    if not raw_html:
        return ""
    clean_re = re.compile('<.*?>')
    text = re.sub(clean_re, '', raw_html)
    return text.strip()

def is_relevant_article(title, summary):
    combined_text = f"{title} {summary}".lower()
    for ex_kw in EXCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(ex_kw) + r'\b', combined_text):
            return False
    for inc_kw in INCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(inc_kw) + r'\b', combined_text):
            return True
    return False

def translate_text(text):
    """使用 MyMemoryTranslator 进行稳定翻译，完美支持云端服务器 IP"""
    if not text:
        return ""
    try:
        translator = MyMemoryTranslator(source='en', target='zh-CN')
        if len(text) <= 450:
            result = translator.translate(text)
            return result if result else text
        
        chunks = [text[i:i+400] for i in range(0, len(text), 400)]
        translated_chunks = []
        for chunk in chunks:
            res = translator.translate(chunk)
            if res:
                translated_chunks.append(res)
            else:
                translated_chunks.append(chunk)
            time.sleep(0.3)
        return ' '.join(translated_chunks)
    except Exception as e:
        print(f"[警告] 翻译失败 ({e})，保留原文。")
        return text

def format_pub_date(raw_date):
    """将发布时间精简，仅保留日期部分（如 Wed, 04 Nov 2026），去掉具体时分秒和时区"""
    if not raw_date:
        return datetime.now().strftime('%a, %d %b %Y')
    cleaned = re.sub(r'\s+\d{2}:\d{2}:\d{2}.*', '', raw_date)
    return cleaned.strip()

def fetch_and_process():
    processed_links = set()
    new_articles = []
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始抓取 RSS...")
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            print(f"成功拉取: {url} (包含 {len(feed.entries)} 条消息)")
            for entry in feed.entries:
                link = getattr(entry, 'link', '')
                if link in processed_links:
                    continue
                processed_links.add(link)
                
                title = getattr(entry, 'title', '')
                summary = clean_html(getattr(entry, 'summary', getattr(entry, 'description', '')))
                
                if is_relevant_article(title, summary):
                    print(f" -> 匹配文章: {title[:50]}...")
                    summary_zh = translate_text(summary)
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
            print(f"[错误] 解析 {url} 失败: {e}")

    return new_articles

def update_daily_txt(articles):
    if not articles:
        print("本次没有筛选出符合条件的新文章。")
        return
        
    today_str = datetime.now().strftime('%Y-%m-%d')
    filename = f"scmp_{today_str}.txt"
    
    existing_titles = set()
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            existing_content = f.read()
            # 读取已有的标题用于去重
            existing_titles = set(re.findall(r'【Title】(.*)', existing_content))

    added_count = 0
    new_txt_buffer = ""
    
    for art in articles:
        if art['title_en'] in existing_titles:
            continue
            
        added_count += 1
        new_txt_buffer += f"========================================\n"
        new_txt_buffer += f"【Title】{art['title_en']}\n"
        new_txt_buffer += f"【时间】{art['pub_date']}\n"
        new_txt_buffer += f"----------------------------------------\n"
        new_txt_buffer += f"【中文正文】\n{art['summary_zh']}\n\n"

    if added_count > 0:
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(new_txt_buffer)
        print(f"已成功追加 {added_count} 篇新文章到文件: {filename}")
    else:
        print("本次抓取到的匹配文章均已在今日 TXT 中，无需重复写入。")

def main():
    print("RSS 监控服务已启动...")
    articles = fetch_and_process()
    update_daily_txt(articles)

if __name__ == "__main__":
    main()