r"""
笔趣阁类盗版站小说爬虫：整本抓取 -> 单个 txt（保留章节标题，便于转 epub）

用法：
    python novel_scraper.py <小说目录页URL> <输出.txt>
示例：
    python novel_scraper.py https://www.xbiquge345.com/book/62887/ 末世为王.txt

说明：
    - 目录页一次性列出全书章节链接；脚本逐个抓取章节页，
      提取 <h1> 章节标题 和 <div id="txt"> 正文，写入 txt。
    - 正文按 <br/> 分段，段首的全角/半角空白缩进会清除，
      并剔除站方插入的广告段（"一秒记住..."）。
    - txt 格式：每章标题独占一行，正文每段一行，章与章之间空一行。
      转 epub 时（calibre / novel2ebook 等）可用正则 第\d+章 自动分章。
    - 仅建议个人学习使用；小说版权归作者所有，请支持正版。
"""

import sys, re, time, random, html as ihtml
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Referer": "https://www.xbiquge345.com/",
}


def get_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch(session, url, retries=3, timeout=10):
    """带重试的 GET，该站声明 charset=utf-8，强制按 utf-8 解码避免乱码。"""
    for i in range(retries):
        try:
            print(f"    请求: {url}", flush=True)
            r = session.get(url, timeout=timeout)
            r.encoding = "utf-8"
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
            print(f"    [{i+1}/{retries}] 状态码 {r.status_code}, 页面仅 {len(r.text)} 字符", flush=True)
        except requests.RequestException as e:
            print(f"    [{i+1}/{retries}] 请求异常: {type(e).__name__}: {e}", flush=True)
        if i < retries - 1:
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"抓取失败: {url}")


def get_chapter_list(session, book_url):
    """从目录页提取全部章节链接（按出现顺序去重）。"""
    soup = BeautifulSoup(fetch(session, book_url), "html.parser")
    chapters, seen = [], set()
    for a in soup.select('a[href*="/chapter/"]'):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        # 形如 /chapter/62887/34901505.html
        if re.fullmatch(r"/chapter/\d+/\d+\.html", href) and href not in seen:
            seen.add(href)
            chapters.append((title, href))
    return chapters


def parse_chapter(html_text):
    """返回 (章节标题, [段落, ...])"""
    soup = BeautifulSoup(html_text, "html.parser")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    div = soup.find("div", id="txt")
    if div is None:
        raise ValueError("找不到正文容器 div#txt")

    # 剔除广告段（"一秒记住【笔趣阁】..."）
    for p in div.find_all("p"):
        p.decompose()

    # 网页正文是一整段 HTML 靠 <br/> 分行：先切成段落再清洗
    paras = []
    for piece in re.split(r"<br\s*/?>", str(div)):
        t = BeautifulSoup(piece, "html.parser").get_text()   # 去残留标签
        t = ihtml.unescape(t).replace("\u3000", " ").strip()
        t = re.sub(r"^[ \t\u00a0]+", "", t)                  # 去段首缩进
        if t:
            paras.append(t)
    return title, paras


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    book_url, out_path = sys.argv[1], sys.argv[2]
    base = re.match(r"(https?://[^/]+)", book_url).group(1)

    print("启动成功，正在获取目录页...", flush=True)
    session = get_session()
    chapters = get_chapter_list(session, book_url)
    print(f"共发现 {len(chapters)} 章")

    # 逐章追加写入，中途挂了也不丢已抓内容
    with open(out_path, "w", encoding="utf-8") as f:
        for i, (title, href) in enumerate(chapters, 1):
            print(f"[{i}/{len(chapters)}] 抓取: {title}", flush=True)
            html_text = fetch(session, base + href)
            title, paras = parse_chapter(html_text)
            f.write(title + "\n")
            f.write("\n".join(paras))
            f.write("\n\n")
            f.flush()
            print(f"    完成: {len(paras)} 段", flush=True)
            time.sleep(random.uniform(0.5, 1.5))   # 礼貌延时，别打满对方

    print(f"完成 -> {out_path}")


if __name__ == "__main__":
    sys.argv = ["novel_scraper.py",
                "https://www.xbiquge345.com/book/62887/",
                "末世为王.txt"]
    main()