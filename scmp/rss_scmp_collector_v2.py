import time
import os
import re
from datetime import datetime
import feedparser
from deep_translator import MyMemoryTranslator

# 确保工作目录切换到 scmp 文件夹
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
    """彻底清除 HTML 标签，保留纯文本结构"""
    if not raw_html:
        return ""
    # 将换行标签转为真实换行，方便后续分段
    raw_html = re.sub(r'</p>|<br\s*/?>', '\n', raw_html)
    clean_re = re.compile('<.*?>')
    text = re.sub(clean_re, '', raw_html)
    # 清理多余空行
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)

def is_relevant_article(title, content):
    """筛选逻辑：必须包含目标国家关键词，且不能包含排除关键词"""
    combined_text = f"{title} {content}".lower()
    for ex_kw in EXCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(ex_kw) + r'\b', combined_text):
            return False
    for inc_kw in INCLUDE_KEYWORDS:
        if re.search(r'\b' + re.escape(inc_kw) + r'\b', combined_text):
            return True
    return False

def translate_text(text):
    """强力分段翻译，确保全文完整转换为中文"""
    if not text:
        return ""
    try:
        translator = MyMemoryTranslator(source='en', target='zh-CN')
        paragraphs = text.split('\n')
        translated_paragraphs = []
        
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            
            # 如果段落较长，按 300 字符安全切块
            if len(p) > 300:
                chunks = [p[i:i+300] for i in range(0, len(p), 300)]
                chunk_res = []
                for chunk in chunks:
                    res = translator.translate(chunk)
                    chunk_res.append(res if res else chunk)
                    time.sleep(0.4) # 稳定延时
                translated_paragraphs.append(" ".join(chunk_res))
            else:
                res = translator.translate(p)
                translated_paragraphs.append(res if res else p)
                time.sleep(0.4)
                
        return '\n\n'.join(translated_paragraphs)
    except Exception as e:
        print(f"[警告] 翻译过程出现异常: {e}")
        return text

def format_pub_date(raw_date):
    """精简时间格式，仅保留日期（如 Wed, 04 Nov 2026）"""
    if not raw_date:
        return datetime.now().strftime('%a, %d %b %Y')
    cleaned = re.sub(r'\s+\d{2}:\d{2}:\d{2}.*', '', raw_date)
    return cleaned.strip()

def fetch_and_process():
    processed_links = set()
    new_articles = []
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始深度抓取 RSS 全文...")
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
                
                # 【核心修改】优先提取 RSS 中的完整全文节点 (content:encoded 或 content)，其次才是 summary
                raw_content = ""
                if hasattr(entry, 'content') and entry.content:
                    raw_content = entry.content[0].get('value', '')
                elif hasattr(entry, 'summary_detail') and entry.summary_detail:
                    raw_content = entry.summary_detail.get('value', '')
                
                if not raw_content:
                    raw_content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                
                # 清洗 HTML 标签，还原完整正文纯文本
                full_content = clean_html(raw_content)
                
                if is_relevant_article(title, full_content):
                    print(f" -> 匹配成功，正在完整翻译: {title[:40]}...")
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
        print(f"已成功追加 {added_count} 篇完整文章到文件: {filename}")
    else:
        print("本次抓取到的匹配文章均已在今日 TXT 中，无需重复写入。")

def main():
    print("RSS 全文监控服务已启动...")
    articles = fetch_and_process()
    update_daily_txt(articles)

if __name__ == "__main__":
    main()