import os

import pandas as pd
import plotly.express as px
import streamlit as st

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "news_log.csv")

st.set_page_config(page_title="엔터·미디어 회계 인사이트 레이더", page_icon="🎬", layout="wide")

st.markdown(
    """
    <style>
    div[data-testid="stMetric"] {
        background: rgba(127,127,127,0.07);
        border: 1px solid rgba(127,127,127,0.18);
        border-radius: 12px;
        padding: 14px 18px;
    }
    div[data-testid="stExpander"] {
        border-radius: 10px;
    }
    .app-tagline {
        font-size: 0.95rem;
        opacity: 0.8;
        line-height: 1.5;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

CATEGORY_COLORS = {
    "콘텐츠 자산화·상각": "#4C6EF5",
    "수익인식": "#F59F00",
    "우발부채·소송": "#E64980",
    "지배구조·합병": "#12B886",
    "정책·지원제도": "#7048E8",
}

# crawler.py의 MAJOR_OUTLET_DOMAINS와 동일 — 표시 시 "굵직한 기사" 우선순위 판단용
MAJOR_OUTLET_DOMAINS = [
    "yna.co.kr", "chosun.com", "joongang.co.kr", "hankyung.com", "mk.co.kr",
    "hani.co.kr", "donga.com", "sbs.co.kr", "imbc.com", "kbs.co.kr",
    "newsis.com", "edaily.co.kr", "yonhapnewstv.co.kr",
]


def is_major_outlet(link: str) -> int:
    return 0 if any(domain in str(link) for domain in MAJOR_OUTLET_DOMAINS) else 1


@st.cache_data(ttl=3600)
def load_data():
    df = pd.read_csv(DATA_PATH, encoding="utf-8-sig")
    df["collected_at"] = pd.to_datetime(df["collected_at"])
    df["pub_date"] = pd.to_datetime(df["pub_date"], format="%a, %d %b %Y %H:%M:%S %z", errors="coerce")
    df["collected_date"] = df["collected_at"].dt.date
    df["week"] = df["collected_at"].dt.to_period("W").apply(lambda p: p.start_time.date())
    df["major_rank"] = df["link"].apply(is_major_outlet)
    return df.sort_values("collected_at", ascending=False)


df = load_data()

st.title("🎬 엔터·미디어 회계 인사이트 레이더")
st.markdown(
    '<p class="app-tagline">엔터·미디어 산업에서 감사 실무상 판단이 걸리는 5가지 주제(콘텐츠 자산화·상각 / '
    "수익인식 / 우발부채·소송 / 지배구조·합병 / 정책·지원제도)의 뉴스를 매주 일요일 자동 수집해 누적하는 "
    "개인 인사이트 관리용 대시보드임. 같은 사건의 중복 보도는 주요언론사·최신순 기준으로 묶어 정리하고, "
    "회계 이슈와 무관한 가십성 기사는 제외함. 결론을 내리지 않고 산업 신호를 놓치지 않기 위한 "
    '트렌드 추적용임.</p>',
    unsafe_allow_html=True,
)

# ---- KPI ----
k1, k2, k3 = st.columns(3)
k1.metric("누적 수집 건수", f"{len(df):,}건")
last_run = df["collected_at"].max()
k2.metric("최근 수집 시각", last_run.strftime("%Y-%m-%d %H:%M") if pd.notna(last_run) else "-")
k3.metric("추적 카테고리 수", f"{len(CATEGORY_COLORS)}개")

st.divider()

# ---- 트렌드 차트 ----
st.subheader("주간 카테고리별 언급 추이")
weekly = df.groupby(["week", "category"]).size().reset_index(name="count")
if weekly.empty:
    st.info("아직 데이터 없음.")
else:
    fig = px.bar(
        weekly,
        x="week",
        y="count",
        color="category",
        barmode="group",
        color_discrete_map=CATEGORY_COLORS,
        labels={"week": "주", "count": "건수", "category": "카테고리"},
    )
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend_title_text="",
        margin=dict(t=10, l=10, r=10, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)
    if df["week"].nunique() <= 1:
        st.caption("※ 아직 1주차 데이터뿐임. 매주 일요일 자동수집이 쌓이면 주차별 그룹 막대로 추이가 나타남.")

st.divider()

# ---- 카테고리 탭 → 키워드별 접이식 섹션 ----
st.subheader("카테고리별 기사 (키워드 세션)")
tabs = st.tabs(list(CATEGORY_COLORS.keys()))

for tab, category in zip(tabs, CATEGORY_COLORS.keys()):
    with tab:
        cat_df = df[df["category"] == category]
        if cat_df.empty:
            st.info("아직 수집된 기사 없음.")
            continue

        keyword_counts = cat_df["keyword"].value_counts()
        for keyword in keyword_counts.index:
            kw_df = cat_df[cat_df["keyword"] == keyword]
            with st.expander(f"🔎 {keyword}  ·  {len(kw_df)}건"):
                for collected_date, day_df in sorted(
                    kw_df.groupby("collected_date"), key=lambda x: x[0], reverse=True
                ):
                    st.markdown(f"**{collected_date} 수집분**")
                    day_df = day_df.sort_values(["major_rank", "pub_date"], ascending=[True, False])
                    for _, row in day_df.iterrows():
                        st.markdown(f"- [{row['title']}]({row['link']}) · {row['pub_date']}")
                    st.markdown("")

st.divider()
st.subheader("ℹ️ 수집·필터링 방법론")
with st.container(border=True):
    st.markdown(
        """
**선정 기준** — 네이버 뉴스 검색 API로 카테고리별 키워드를 수집하되, 감사 판단이 걸리는 회계·법률
이슈(자산화, 손상차손, 매출인식, 정산, 합병 등)로 범위를 한정함.

**관련성을 높인 방법** — 단순 키워드 매칭은 오탐이 많음(예: 대학 행사, 가전제품 기사가 "영상"·"콘텐츠"
같은 범용 단어만으로 걸림). 이를 줄이기 위해 3단계 필터를 적용함.
1. 가십·스캔들 제외어로 인적 사생활 기사 배제
2. 엔터·미디어·게임/웹툰 업계 고유명사·용어가 본문에 있어야 통과
3. 키워드별 회계·법률 핵심 용어(예: "손상차손", "매출인식")가 실제로 본문에 있어야 통과 — 특히
   "합병"·"M&A"처럼 산업 불문 통용어인 키워드는 범용 업계 단어로는 부족하고 **실제 회사명**이
   본문에 있어야만 인정하도록 더 엄격하게 적용함.

같은 사건을 다룬 중복 보도는 제목 유사도로 묶어 주요언론사·최신순 상위 3건만 남기고, 키워드당 최종
8건으로 상한을 둠.

**한계** — 키워드 기반 필터링은 완벽할 수 없음. 실제로 정밀도를 높이는 과정에서 일부 키워드는
회사명 요건 때문에 진짜 관련 기사도 함께 걸러져 수집 건수가 줄어드는 트레이드오프가 존재함(예:
"엔터 M&A 합병"). 이 대시보드는 결론이나 감사 의견을 제시하지 않으며, 산업 신호를 놓치지 않기 위한
개인용 트렌드 추적 도구임.
"""
    )
