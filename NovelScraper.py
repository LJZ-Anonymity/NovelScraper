r"""
笔趣阁类盗版站小说爬虫：整本抓取 -> 单个 txt（保留章节标题，便于转 epub）

用法：
    python NovelScraper.py <小说目录页URL> <输出.txt>
示例：
    python NovelScraper.py https://www.xbiquge345.com/book/62887/ 末世为王.txt

说明：
    - 目录页一次性列出全书章节链接；脚本逐个抓取章节页，
      提取 <h1> 章节标题 和 <div id="txt"> 正文，写入 txt。
    - 正文按 <br/> 分段，段首空白缩进会清除，并剔除站方插入的广告段。
    - txt 格式：每章标题独占一行，正文每段一行，章与章之间空一行。
    - 支持断点续传：若输出文件已存在，会读取已完成的章节标题并跳过。
    - 仅建议个人学习使用；小说版权归作者所有，请支持正版。
"""

from bs4 import BeautifulSoup
from html import unescape
import requests
import logging
import random
import time
import sys
import os
import re

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.xbiquge345.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 章节链接正则：匹配 /chapter/62887/34901505.html 之类的路径
CHAPTER_HREF_RE = re.compile(r"/chapter/\d+/\d+\.html")

# 广告/站点水印关键词（正文中出现整段匹配即丢弃）
AD_PATTERN = re.compile(
    r"一秒记住|笔趣阁|请记住本站|手机版阅读网址|"
    r"www\.\S+\.(?:com|net|org|cn)|"
    r"天才一秒记住|最新章节|全文免费阅读"
)

# 章节标题识别正则（用于断点续传时判断已写入的行）
CHAPTER_TITLE_RE = re.compile(r"^\s*第[0-9零一二三四五六七八九十百千两]+[章节回]")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("novel")


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------

def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch(session: requests.Session, url: str, retries: int = 3, timeout: int = 15) -> str:
    """带重试的 GET。优先使用响应头声明的编码，失败时回退到 apparent_encoding。"""
    last_err = None
    for i in range(retries):
        try:
            log.info("请求: %s", url)
            r = session.get(url, timeout=timeout)
            if r.status_code != 200:
                log.warning("[%d/%d] 状态码 %s", i + 1, retries, r.status_code)
            else:
                # 优先用响应头声明的编码；若无则用 apparent_encoding
                if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
                    r.encoding = r.apparent_encoding or "utf-8"
                if len(r.text) < 200:
                    log.warning("[%d/%d] 页面仅 %d 字符，可能异常", i + 1, retries, len(r.text))
                return r.text
        except requests.RequestException as e:
            last_err = e
            log.warning("[%d/%d] 请求异常: %s: %s", i + 1, retries, type(e).__name__, e)
        if i < retries - 1:
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"抓取失败: {url} ({last_err})")


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def get_chapter_list(session: requests.Session, book_url: str) -> list[tuple[str, str]]:
    """从目录页提取全部章节链接（按出现顺序去重）。"""
    soup = BeautifulSoup(fetch(session, book_url), "html.parser")
    chapters: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href or not CHAPTER_HREF_RE.search(href):
            continue
        # 规范化：只保留 /chapter/... 之后的路径
        m = CHAPTER_HREF_RE.search(href)
        path = m.group(0)
        if path in seen:
            continue
        seen.add(path)
        title = a.get_text(strip=True) or path
        chapters.append((title, path))
    return chapters


def clean_line(line: str) -> str:
    """清洗单行文本：去缩进、去实体、去首尾空白。"""
    t = unescape(line)
    t = t.replace("\u3000", " ").replace("\xa0", " ")
    t = re.sub(r"^[ \t]+", "", t)
    return t.strip()


def parse_chapter(html_text: str, fallback_title: str = "") -> tuple[str, list[str]]:
    """返回 (章节标题, [段落, ...])。

    直接操作原始 soup：把 <br> 替换为换行符再 get_text，避免 re.split 误切 HTML。
    """
    soup = BeautifulSoup(html_text, "html.parser")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else fallback_title

    div = soup.find("div", id="txt")
    if div is None:
        raise ValueError("找不到正文容器 div#txt")

    # 去掉脚本/样式/广告 p 标签
    for tag in div(["script", "style", "p"]):
        tag.decompose()
    for br in div.find_all("br"):
        br.replace_with("\n")

    paras: list[str] = []
    for raw in div.get_text().split("\n"):
        t = clean_line(raw)
        if not t:
            continue
        if AD_PATTERN.search(t):
            continue
        paras.append(t)
    return title, paras


# ---------------------------------------------------------------------------
# 断点续传
# ---------------------------------------------------------------------------

def load_done_titles(out_path: str) -> set[str]:
    """读取已有 txt 中已完成的章节标题集合。"""
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return set()
    done: set[str] = set()
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and CHAPTER_TITLE_RE.match(line):
                    done.add(line)
    except OSError as e:
        log.warning("读取已存在文件失败，将从头开始: %s", e)
        return set()
    return done


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    book_url, out_path = sys.argv[1], sys.argv[2]

    m = re.match(r"(https?://[^/]+)", book_url)
    if not m:
        log.error("URL 格式错误: %s", book_url)
        sys.exit(1)
    base = m.group(1)

    # 断点续传：若文件已存在，读取已完成章节
    done_titles = load_done_titles(out_path)
    if done_titles:
        log.info("检测到已完成 %d 章，将跳过它们", len(done_titles))

    log.info("正在获取目录页...")
    session = get_session()
    chapters = get_chapter_list(session, book_url)
    if not chapters:
        log.error("未发现任何章节，请检查 URL 或站点结构是否变化")
        sys.exit(1)
    log.info("共发现 %d 章", len(chapters))

    # 首次写入用 "w"（清空旧文件），续传用 "a"
    mode = "a" if done_titles else "w"
    written = 0
    skipped = 0

    with open(out_path, mode, encoding="utf-8") as f:
        for i, (catalog_title, href) in enumerate(chapters, 1):
            if catalog_title in done_titles:
                skipped += 1
                continue

            log.info("[%d/%d] 抓取: %s", i, len(chapters), catalog_title)
            try:
                html_text = fetch(session, base + href)
                title, paras = parse_chapter(html_text, fallback_title=catalog_title)
            except (RuntimeError, ValueError) as e:
                log.error("    章节失败，跳过: %s", e)
                continue

            f.write(title + "\n")
            if paras:
                f.write("\n".join(paras))
            f.write("\n\n")
            f.flush()
            written += 1
            log.info("    完成: %d 段", len(paras))

            time.sleep(random.uniform(0.5, 1.5))

    log.info("完成 -> %s（新写 %d 章，跳过 %d 章）", out_path, written, skipped)


if __name__ == "__main__":
    # 调试时可直接写死参数；平时注释掉，用命令行传参
    # sys.argv = ["novel_scraper.py",
    #             "https://www.xbiquge345.com/book/62887/",
    #             "末世为王.txt"]
    main()