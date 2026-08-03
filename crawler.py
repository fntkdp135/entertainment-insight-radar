"""
엔터·미디어 회계 인사이트 레이더 — 네이버 뉴스 수집기

감사 실무에서 판단이 걸리는 주제(자산화, 수익인식, 우발부채, 지배구조)를
카테고리별 키워드로 수집해 data/news_log.csv에 누적한다.
최초 실행일(화) 이후로는 매주 일요일 GitHub Actions가 실행되며, 같은 사건을
다룬 중복 기사는 묶어서 대표 기사 몇 건만 남긴다.
"""

import os
import re
import csv
import html
import sys
import difflib
import urllib.parse
import urllib.request
import json
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")
API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG_PATH = os.path.join(DATA_DIR, "news_log.csv")

# 계속기업가정 등 라이브 이슈로 번질 수 있는 주제는 제외하고,
# 엔터·미디어 업종에서 반복적으로 나타나는 "회계처리 판단" 이슈로 한정한다.
CATEGORIES = {
    "콘텐츠 자산화·상각": ["콘텐츠 제작비 자산화", "무형자산 손상차손", "판권 무형자산"],
    "수익인식": ["OTT 매출 인식", "콘텐츠 매출인식 기준", "광고매출 인식시점"],
    "우발부채·소송": ["정산 분쟁 엔터", "출연료 정산 소송"],
    "지배구조·합병": ["엔터 M&A 합병", "콘텐츠사 지분인수"],
    "정책·지원제도": ["콘텐츠산업 지원정책", "저작권법 개정 콘텐츠"],
}

# 검색어가 느슨하게 매칭돼도(예: "손상차손" 검색인데 본문엔 없는 경우), 회계·법률
# 핵심 용어가 실제로 본문에 있어야만 통과시키는 필수조건.
# 값은 "그룹의 리스트" — 그룹끼리는 AND, 그룹 내 단어끼리는 OR.
# 예: [["무형자산"], ["손상차손"]] → "무형자산"과 "손상차손"이 각각 하나씩(둘 다) 있어야 통과.
ANCHOR_TERMS = {
    "콘텐츠 제작비 자산화": [["자산화", "무형자산"]],
    "무형자산 손상차손": [["무형자산"], ["손상차손"]],
    "판권 무형자산": [["무형자산", "판권"]],
    "OTT 매출 인식": [["매출인식", "매출 인식", "수익인식"]],
    "콘텐츠 매출인식 기준": [["매출인식", "매출 인식", "수익인식"]],
    "광고매출 인식시점": [["광고매출", "인식시점"]],
    "정산 분쟁 엔터": [["정산"]],
    "출연료 정산 소송": [["정산", "소송"]],
    "엔터 M&A 합병": [["합병", "M&A", "인수"]],
    "콘텐츠사 지분인수": [["지분인수", "지분 인수"]],
    "콘텐츠산업 지원정책": [["지원정책", "지원 정책"]],
    "저작권법 개정 콘텐츠": [["저작권법"]],
}

# 관련도(sim) 정렬이 최신순(date)보다 더 적합한 키워드.
# "무형자산 손상차손"처럼 검색량이 적고 정확도가 중요한 주제에 적용.
SORT_OVERRIDE = {
    "무형자산 손상차손": "sim",
}

# 회계 이슈와 무관한 인적 스캔들·가십성 기사를 걸러내기 위한 제외어.
# (예: "정산 분쟁 엔터" 키워드가 결별·재혼 등 사생활 기사까지 끌어오는 문제 대응)
GOSSIP_EXCLUDE_WORDS = [
    "열애", "결별", "재혼", "이혼", "불륜", "웨딩", "임신", "출산",
    "화보", "생일", "탈퇴설", "캐스팅", "럽스타그램", "공개연애",
    # 개인 신변·폭로성 사연 기사 — "정산"·"소송" 등이 배경 설명으로 한 줄 끼어있을 뿐
    # 실제 핵심 내용은 회계와 무관한 인적 사건인 경우를 배제
    "폭로", "구타", "폭행", "우울증", "생활고", "갑질", "왕따", "자살",
]

# "영상", "콘텐츠"처럼 범용 단어만으로는 무관한 기사(대학 행사, 가전제품 등)까지
# 걸리므로, 아래 중 하나라도 본문에 있어야 엔터·미디어 업계 기사로 인정한다.
# (완벽한 정밀 필터링은 불가능하지만 명백한 오탐은 크게 줄일 수 있음)
# 특정 기업/플랫폼명 (모호하지 않은 고유명사) — "합병"처럼 anchor가 약한 키워드는
# 이 목록에 있는 실제 회사명이 나와야만 통과시켜 정밀도를 높인다.
ENTERTAINMENT_COMPANY_NAMES = [
    "하이브", "HYBE", "SM엔터", "JYP", "YG엔터", "CJ ENM", "CJENM",
    "넷플릭스", "웨이브", "티빙", "왓챠", "쿠팡플레이", "디즈니플러스",
    "카카오엔터", "네이버웹툰", "스튜디오드래곤", "콘텐트리", "제이콘텐트리",
    "빅히트", "플레디스", "큐브엔터", "IHQ", "키이스트", "판타지오",
    "에이스토리", "래몽래인", "삼화네트웍스", "팬엔터테인먼트", "초록뱀미디어",
    "위지윅스튜디오",
    # 웹툰/게임 퍼블리셔 — 콘텐츠 IP 자산화·손상 이슈가 함께 다뤄지는 경우가 많아 포함
    "넥슨", "크래프톤", "엔씨소프트", "카카오게임즈", "펄어비스", "위메이드",
    "네오위즈", "컴투스", "카카오웹툰", "레진코믹스", "탑툰",
]

# 업계 고유 용어 (일반 명사와 혼동될 소지가 적은 것) — 회사명보다는 느슨한 문맥 신호
GENERIC_INDUSTRY_TERMS = [
    "엔터테인먼트", "연예기획사", "소속사", "웹툰", "웹소설", "아이돌",
    "케이팝", "K-POP", "드라마 제작사", "예능 제작사", "음원 유통",
    "공연기획사", "방송사",
]

ENTERTAINMENT_CONTEXT_TERMS = ENTERTAINMENT_COMPANY_NAMES + GENERIC_INDUSTRY_TERMS

# "합병/M&A/인수"처럼 그 자체로는 산업 불문 통용어라 오탐이 잦은 키워드는,
# 범용 업계 단어가 아니라 실제 회사명이 본문에 있어야만 통과시킨다.
STRICT_COMPANY_ONLY_KEYWORDS = {"엔터 M&A 합병", "콘텐츠사 지분인수"}

# 같은 사건을 다룬 기사 묶음에서 우선적으로 남길 주요 언론사(도메인 기준)
MAJOR_OUTLET_DOMAINS = [
    "yna.co.kr", "chosun.com", "joongang.co.kr", "hankyung.com", "mk.co.kr",
    "hani.co.kr", "donga.com", "sbs.co.kr", "imbc.com", "kbs.co.kr",
    "newsis.com", "edaily.co.kr", "yonhapnewstv.co.kr",
]

DUP_TOKEN_JACCARD_THRESHOLD = 0.5  # 제목 핵심단어 중복비율 — 통신사 재배포 기사 탐지용
KEEP_PER_CLUSTER = 3
MAX_PER_KEYWORD = 8  # 이슈가 아무리 커도 키워드당 최종 상한

KST = timezone(timedelta(hours=9))

STOPWORDS = {"단독", "종합", "속보", "포토", "영상", "뉴스"}


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def is_major_outlet(link: str) -> int:
    """주요 언론사면 0(우선), 아니면 1을 반환 — 정렬 키로 사용."""
    return 0 if any(domain in link for domain in MAJOR_OUTLET_DOMAINS) else 1


def is_gossip(title: str, description: str) -> bool:
    text = title + " " + description
    return any(word in text for word in GOSSIP_EXCLUDE_WORDS)


def has_industry_context(keyword: str, title: str, description: str) -> bool:
    text = title + " " + description
    terms = ENTERTAINMENT_COMPANY_NAMES if keyword in STRICT_COMPANY_ONLY_KEYWORDS else ENTERTAINMENT_CONTEXT_TERMS
    return any(term in text for term in terms)


def has_anchor_term(keyword: str, title: str, description: str) -> bool:
    """그룹끼리는 AND, 그룹 내 단어끼리는 OR로 판정."""
    groups = ANCHOR_TERMS.get(keyword)
    if not groups:
        return True
    text = title + " " + description
    return all(any(term in text for term in group) for group in groups)


def parse_pub_date(pub_date: str):
    try:
        return parsedate_to_datetime(pub_date)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def title_tokens(title: str) -> set:
    cleaned = re.sub(r"[^\w\s]", " ", title)
    return {t for t in cleaned.split() if len(t) >= 2 and t not in STOPWORDS}


def title_similarity(a: str, b: str) -> float:
    """핵심단어 자카드 유사도와 문자열 유사도 중 더 엄격한(=더 확실한 중복만 인정) 쪽을 기준으로 삼는다."""
    tokens_a, tokens_b = title_tokens(a), title_tokens(b)
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b) if (tokens_a or tokens_b) else 0
    char_ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return max(jaccard, char_ratio)


def cluster_and_trim(items, keep=KEEP_PER_CLUSTER, threshold=DUP_TOKEN_JACCARD_THRESHOLD, max_total=MAX_PER_KEYWORD):
    """제목 유사도로 같은 사건(통신사 재배포 등)을 묶고, 묶음마다 주요언론사·최신순으로 상위 N건만 남긴다.
    이후에도 남은 총량이 많으면(=진짜로 이슈가 큰 사건) max_total으로 한 번 더 상한을 둔다."""
    clusters = []
    for item in items:
        matched = None
        for cluster in clusters:
            if title_similarity(item["title"], cluster[0]["title"]) >= threshold:
                matched = cluster
                break
        if matched is not None:
            matched.append(item)
        else:
            clusters.append([item])

    trimmed = []
    for cluster in clusters:
        cluster.sort(key=lambda x: (is_major_outlet(x["originallink"]), -parse_pub_date(x["pubDate"]).timestamp()))
        trimmed.extend(cluster[:keep])

    trimmed.sort(key=lambda x: (is_major_outlet(x["originallink"]), -parse_pub_date(x["pubDate"]).timestamp()))
    return trimmed[:max_total]


def fetch_news(query: str, display: int = 30):
    sort = SORT_OVERRIDE.get(query, "date")
    params = urllib.parse.urlencode(
        {"query": query, "display": display, "sort": sort, "format": "json"}
    )
    req = urllib.request.Request(
        f"{API_URL}?{params}",
        headers={
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        },
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)["items"]


def load_existing_links(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return {row["link"] for row in reader}


def main():
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 필요합니다.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(DATA_DIR, exist_ok=True)
    existing_links = load_existing_links(LOG_PATH)
    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    new_rows = []
    for category, keywords in CATEGORIES.items():
        for keyword in keywords:
            try:
                raw_items = fetch_news(keyword)
            except Exception as e:
                print(f"[WARN] '{keyword}' 수집 실패: {e}", file=sys.stderr)
                continue

            cleaned_items = [
                {**item, "title": strip_html(item["title"]), "description": strip_html(item["description"])}
                for item in raw_items
            ]
            cleaned_items = [
                item for item in cleaned_items
                if not is_gossip(item["title"], item["description"])
                and has_industry_context(keyword, item["title"], item["description"])
                and has_anchor_term(keyword, item["title"], item["description"])
            ]
            deduped_items = cluster_and_trim(cleaned_items)

            for item in deduped_items:
                link = item["link"]
                if link in existing_links:
                    continue
                existing_links.add(link)
                new_rows.append(
                    {
                        "collected_at": collected_at,
                        "category": category,
                        "keyword": keyword,
                        "title": item["title"],
                        "description": item["description"],
                        "pub_date": item["pubDate"],
                        "link": link,
                    }
                )

    file_exists = os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "collected_at",
                "category",
                "keyword",
                "title",
                "description",
                "pub_date",
                "link",
            ],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"신규 {len(new_rows)}건 저장 완료 ({collected_at})")


if __name__ == "__main__":
    main()
