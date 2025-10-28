# -*- coding: utf-8 -*-
import os, re, base64
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from urllib.parse import quote as _urlquote

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import gspread
from google.oauth2 import service_account
from wordcloud import WordCloud
import matplotlib.pyplot as plt

# =============================
# 0) 기본 설정
# =============================
LOGO_IMAGE = "images/pmang_logo.png"
st.set_page_config(page_title="웹보드 VOC 대시보드", page_icon=LOGO_IMAGE, layout="wide")

KST = ZoneInfo("Asia/Seoul")

# =============================
# 1) 유틸
# =============================
def normalize_sa_info(sa: dict):
    sa = dict(sa or {})
    if isinstance(sa.get("private_key"), str) and "\\n" in sa["private_key"]:
        sa["private_key"] = sa["private_key"].replace("\\n", "\n")
    return sa

def get_image_as_base64(path: str):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

def now_kst_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def get_sheet_id():
    sid = st.secrets.get("SHEET_ID", "")
    if not sid:
        sid = st.secrets.get("gcp_service_account", {}).get("SHEET_ID", "")
    return sid

# =============================
# 2) 로그인
# =============================
def require_login():
    try:
        if not getattr(st.user, "is_logged_in", False):
            st.title("🔐 로그인 필요")
            st.info("Google 계정으로 로그인 후 이용해 주세요.")
            st.button("Google 로그인", on_click=st.login)
            st.stop()
    except Exception:
        pass

def current_user():
    return {
        "name": getattr(st.user, "name", ""),
        "email": getattr(st.user, "email", ""),
        "sub": getattr(st.user, "sub", "")
    }

# =============================
# 3) Google Sheets
# =============================
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    sa = normalize_sa_info(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
    return gspread.authorize(creds)

def open_sheet(spreadsheet_id):
    gc = get_gspread_client()
    return gc.open_by_key(spreadsheet_id)

@st.cache_data(ttl=600)
def load_voc_data(spreadsheet_id):
    ss = open_sheet(spreadsheet_id)
    data = []
    for ws in ss.worksheets():
        title = ws.title.strip()
        if not re.match(r"^\d{2}-\d{2}$", title):
            continue
        rows = ws.get_all_records()
        data.extend(rows)
    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    if "날짜" not in df.columns:
        return pd.DataFrame()

    df["날짜_dt"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df.dropna(subset=["날짜_dt"])
    df["날짜_dt"] = df["날짜_dt"].dt.tz_localize("UTC").dt.tz_convert(KST)
    return df

# =============================
# 4) 차트 유틸
# =============================
def create_trend_chart(data, date_range, title):
    start, end = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
    all_days = pd.date_range(start=start, end=end, freq="D")
    base = pd.DataFrame(all_days, columns=["날짜_dt"])
    grouped = data.groupby(data["날짜_dt"].dt.date).size().reset_index(name="건수")
    grouped["날짜_dt"] = pd.to_datetime(grouped["날짜_dt"])
    merged = pd.merge(base, grouped, on="날짜_dt", how="left").fillna(0)
    fig = px.line(merged, x="날짜_dt", y="건수", title=f"<b>{title}</b>", markers=True, text="건수")
    fig.update_traces(textposition="top center")
    fig.update_layout(height=300)
    return fig

def create_donut_chart(data, title, group_by):
    counts = data[group_by].value_counts()
    if len(counts) > 5:
        top4 = counts.nlargest(4)
        others = counts.iloc[4:].sum()
        counts = top4._append(pd.Series([others], index=["기타"]))
    fig = go.Figure(data=[go.Pie(labels=counts.index, values=counts.values, hole=0.6)])
    fig.update_layout(title_text=f"<b>{title}</b>", height=300)
    return fig

# =============================
# 5) MAIN
# =============================
def main():
    require_login()
    me = current_user()
    st.sidebar.success(f"로그인: {me['email']}")

    st.title("📊 웹보드 VOC 대시보드")

    spreadsheet_id = get_sheet_id()
    voc_df = load_voc_data(spreadsheet_id)

    if voc_df.empty:
        st.warning("VOC 데이터가 없습니다.")
        st.stop()

    # --- 사이드바 필터 ---
    st.sidebar.header("🕹️ 게임 및 플랫폼 선택")

    game_filters = {
        "뉴맞고": ["뉴맞고 (전체)", "뉴맞고 MOB", "뉴맞고 PC", "뉴맞고 for kakao"],
        "섯다": ["섯다 (전체)", "섯다 MOB", "섯다 PC", "섯다 for kakao"],
        "포커": ["포커 (전체)", "포커 MOB", "포커 PC", "포커 for kakao"],
        "쇼다운홀덤": ["쇼다운홀덤 (전체)", "쇼다운홀덤 MOB", "쇼다운홀덤 PC"],
        "뉴베가스": ["뉴베가스 (전체)", "뉴베가스 MOB", "뉴베가스 PC"],
        "기타": ["기타"]
    }
    all_options = [opt for sub in game_filters.values() for opt in sub]

    if 'filters_initialized' not in st.session_state or not st.session_state.get("select_all", False):
        for opt in all_options:
            st.session_state[opt] = True
        st.session_state.select_all = True
        st.session_state.filters_initialized = True

    def master_toggle():
        val = st.session_state.get("select_all", True)
        for opt in all_options:
            st.session_state[opt] = val

    st.sidebar.checkbox("전체", key="select_all", on_change=master_toggle)
    for game, opts in game_filters.items():
        with st.sidebar.expander(game, expanded=True):
            for opt in opts:
                st.checkbox(opt, key=opt)

    selected = [opt for opt in all_options if st.session_state.get(opt, False)]
    if not selected:
        st.warning("전체 해제 상태입니다. 표시할 데이터가 없습니다.")
        st.stop()

    filtered = voc_df.copy()
    if filtered.empty or "날짜_dt" not in filtered.columns:
        st.warning("전체 해제 상태입니다. 표시할 데이터가 없습니다.")
        st.stop()

    # --- 날짜 범위 ---
    st.sidebar.subheader("📅 기간 선택")
    min_d = filtered["날짜_dt"].min().date()
    max_d = filtered["날짜_dt"].max().date()
    default_start = max(min_d, max_d - timedelta(days=6))
    default_range = (default_start, max_d)
    date_range = st.sidebar.date_input("조회 기간", value=default_range, min_value=min_d, max_value=max_d)
    if isinstance(date_range, date):
        date_range = (date_range, date_range)

    # --- 필터 적용 ---
    start_dt, end_dt = pd.to_datetime(date_range[0]).date(), pd.to_datetime(date_range[1]).date()
    view_df = filtered[(filtered["날짜_dt"].dt.date >= start_dt) & (filtered["날짜_dt"].dt.date <= end_dt)].copy()
    if view_df.empty:
        st.warning("선택된 조건에 해당하는 데이터가 없습니다.")
        st.stop()

    # --- 대시보드 표시 ---
    st.subheader(f"📆 VOC 현황 ({start_dt} ~ {end_dt})")

    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(create_trend_chart(view_df, date_range, "일자별 VOC 추이"), use_container_width=True)
    with col2:
        st.plotly_chart(create_donut_chart(view_df, "카테고리 분포", "taglist"), use_container_width=True)

    st.markdown("---")
    st.subheader("📋 VOC 데이터 미리보기")

    safe_df = view_df.copy()
    for c in safe_df.columns:
        try:
            safe_df[c] = safe_df[c].astype(str)
        except Exception:
            safe_df[c] = safe_df[c].apply(lambda x: str(x) if not pd.isna(x) else "")

    st.dataframe(safe_df.head(200), use_container_width=True, height=500)


if __name__ == "__main__":
    main()
