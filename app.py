# -*- coding: utf-8 -*-
import os
import re
import base64
from datetime import datetime, timedelta
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
# 1) 유틸 함수
# =============================
def get_image_as_base64(path: str):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

def _pure_url(v: str) -> str:
    if not isinstance(v, str):
        return v
    v = v.strip()
    m = re.match(r"^\[[^\]]+\]\((https?://[^)]+)\)$", v)
    return m.group(1) if m else v

def normalize_sa_info(sa: dict) -> dict:
    sa = dict(sa or {})
    sa["auth_uri"]  = _pure_url(sa.get("auth_uri", "")) or "https://accounts.google.com/o/oauth2/auth"
    sa["token_uri"] = _pure_url(sa.get("token_uri", "")) or "https://oauth2.googleapis.com/token"
    sa["auth_provider_x509_cert_url"] = _pure_url(sa.get("auth_provider_x509_cert_url", "")) or "https://www.googleapis.com/oauth2/v1/certs"
    client_email = sa.get("client_email", "")
    if client_email:
        sa["client_x509_cert_url"] = f"https://www.googleapis.com/robot/v1/metadata/x509/{_urlquote(client_email)}"
    pk = sa.get("private_key")
    if isinstance(pk, str) and "\\n" in pk and "\n" not in pk:
        sa["private_key"] = pk.replace("\\n", "\n")
    return sa

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
        is_logged_in = bool(getattr(st.user, "is_logged_in", False))
    except Exception:
        is_logged_in = False
    if not is_logged_in:
        st.title("🔐 로그인 필요")
        st.info("Google 계정으로 로그인 후 이용할 수 있습니다.")
        st.button("Google 계정으로 로그인", on_click=st.login, use_container_width=True)
        st.stop()

def current_user():
    def _g(obj, key, default=""):
        try:
            return getattr(obj, key, default) or default
        except Exception:
            return default
    return {
        "name": _g(st.user, "name", ""),
        "email": _g(st.user, "email", ""),
    }

# =============================
# 3) Google Sheets 연결
# =============================
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    try:
        sa_raw = st.secrets["gcp_service_account"]
        sa = normalize_sa_info(sa_raw)
        creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
    except Exception:
        SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "service_account.json")
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    return gspread.authorize(creds)

def open_sheet(spreadsheet_id):
    gc = get_gspread_client()
    return gc.open_by_key(spreadsheet_id)

@st.cache_data(ttl=60)
def fetch_users_table(spreadsheet_id):
    ss = open_sheet(spreadsheet_id)
    try:
        ws = ss.worksheet("user_management")
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title="user_management", rows=1000, cols=20)
        ws.append_row(["email", "name", "request_date", "status", "approved_date"])
    rows = ws.get_all_records()
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["email","name","request_date","status","approved_date"])

def is_approved(df, email):
    if df.empty:
        return False
    row = df.loc[df["email"].str.lower() == (email or "").lower()]
    return (not row.empty) and (row.iloc[0]["status"] == "approved")

# =============================
# 4) VOC 데이터 로딩
# =============================
L2_TO_L1_MAPPING = {
    '로그인/인증': '계정', '정보 관리': '계정', '기술 오류': '시스템/환경',
    '결제 오류/미지급': '재화/결제', '환불/청약철회': '재화/결제', '재화 소실/오류': '재화/결제',
    '광고/무료충전소': '이벤트/혜택', '이벤트': '이벤트/혜택', '비매너/욕설 신고': '불량 이용자',
    '제재 문의': '불량 이용자', '콘텐츠/시스템 건의': '정책/건의 (VOC)',
    '운영/정책 건의': '정책/건의 (VOC)', '단순 문의/미분류': '기타'
}

def classify_game(cat):
    if pd.isna(cat): return "기타"
    c = re.sub(r'[^a-z0-9가-힣]', '', str(cat).lower())
    if "맞고" in c: return "뉴맞고"
    if "섯다" in c: return "섯다"
    if "포커" in c: return "포커"
    if "홀덤" in c: return "쇼다운홀덤"
    if "베가스" in c: return "뉴베가스"
    return "기타"

def classify_platform(cat):
    if pd.isna(cat): return "기타"
    c = re.sub(r'[^a-z0-9가-힣]', '', str(cat).lower())
    if "forkakao" in c: return "for kakao"
    if "mob" in c: return "MOB"
    if "pc" in c: return "PC"
    return "기타"

@st.cache_data(ttl=600)
def load_voc_data(spreadsheet_id):
    ss = open_sheet(spreadsheet_id)
    all_data = []
    for ws in ss.worksheets():
        title = ws.title.strip()
        if re.match(r'^\d{2,4}[-_]\d{2}$', title):  # 월별
            rows = ws.get_all_records()
            all_data.extend(rows)
        elif re.match(r'^\d{6,8}$', title):  # 일별
            rows = ws.get_all_records()
            for r in rows:
                r["날짜"] = title
            all_data.extend(rows)
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    if "날짜" not in df.columns:
        return pd.DataFrame()
    df = df.rename(columns={"taglist": "L2 태그"})
    df["게임"] = df["접수 카테고리"].apply(classify_game)
    df["플랫폼"] = df["접수 카테고리"].apply(classify_platform)
    df["L1 태그"] = df["L2 태그"].map(L2_TO_L1_MAPPING).fillna("기타")
    df["날짜_dt"] = pd.to_datetime(df["날짜"], errors="coerce")
    df["날짜_dt"] = df["날짜_dt"].dt.tz_localize("UTC").dt.tz_convert(KST)
    return df.dropna(subset=["날짜_dt"])

# =============================
# 5) 시각화 유틸
# =============================
def create_trend_chart(data, date_range, title):
    start, end = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
    all_days = pd.date_range(start=start, end=end, freq="D")
    base = pd.DataFrame(all_days, columns=["날짜_dt"])
    daily = data.groupby(data["날짜_dt"].dt.date).size().reset_index(name="건수")
    daily["날짜_dt"] = pd.to_datetime(daily["날짜_dt"])
    merged = pd.merge(base, daily, on="날짜_dt", how="left").fillna(0)
    fig = px.line(merged, x="날짜_dt", y="건수", markers=True, text="건수", title=f"<b>{title}</b>")
    fig.update_traces(textposition="top center")
    fig.update_layout(height=300)
    return fig

def create_donut_chart(data, title, group_by='L2 태그'):
    counts = data[group_by].value_counts()
    if len(counts) > 5:
        top4 = counts.nlargest(4)
        others = counts.iloc[4:].sum()
        chart_data = pd.concat([top4, pd.Series([others], index=["기타"])])
    else:
        chart_data = counts
    fig = go.Figure(data=[go.Pie(labels=chart_data.index, values=chart_data.values, hole=.6)])
    fig.update_layout(title_text=f"<b>{title}</b>", height=300)
    return fig

# =============================
# 6) MAIN
# =============================
def main():
    require_login()
    me = current_user()
    admin_email = st.secrets.get("app", {}).get("admin_email", "")
    is_admin = (me["email"].lower() == admin_email.lower())

    logo_b64 = get_image_as_base64(LOGO_IMAGE)
    st.sidebar.success(f"로그인: {me['name']} ({me['email']})")

    spreadsheet_id = get_sheet_id()
    users_df = fetch_users_table(spreadsheet_id)
    if not (is_admin or is_approved(users_df, me["email"])):
        st.warning("이 페이지 접근 권한이 없습니다.")
        st.stop()

    voc_df = load_voc_data(spreadsheet_id)
    if voc_df.empty:
        st.warning("VOC 데이터가 없습니다.")
        st.stop()

    # ✅ 안전한 날짜 초기화
    min_dt = voc_df["날짜_dt"].min()
    max_dt = voc_df["날짜_dt"].max()
    if pd.isna(min_dt) or pd.isna(max_dt):
        st.error("유효한 날짜 데이터가 없습니다.")
        st.stop()
    min_d, max_d = min_dt.date(), max_dt.date()
    if "date_range" not in st.session_state:
        st.session_state.date_range = (max(min_d, max_d - timedelta(days=6)), max_d)
    start, end = st.session_state.date_range
    if isinstance(start, pd.Timestamp): start = start.date()
    if isinstance(end, pd.Timestamp): end = end.date()
    if start < min_d: start = min_d
    if end > max_d: end = max_d
    if start > end: start, end = min_d, max_d
    st.session_state.date_range = (start, end)
    date_range = st.date_input("조회 기간", value=st.session_state.date_range, min_value=min_d, max_value=max_d)

    tabs = ["📊 카테고리 분석", "🔍 키워드 검색", "💳 결제/인증 리포트"]
    tab_main, tab_search, tab_payment = st.tabs(tabs)

    # --- 📊 카테고리 분석
    with tab_main:
        st.header("📊 VOC 카테고리 분석")
        st.plotly_chart(create_trend_chart(voc_df, date_range, "일자별 VOC 발생 추이"))
        st.plotly_chart(create_donut_chart(voc_df, "주요 L1 태그", group_by='L1 태그'))

    # --- 🔍 키워드 검색
    with tab_search:
        st.header("🔍 키워드 검색")
        keyword = st.text_input("검색 키워드", value="")
        if keyword:
            r = voc_df[
                voc_df["상담제목"].str.contains(keyword, na=False, case=False)
                | voc_df["문의내용"].str.contains(keyword, na=False, case=False)
            ]
            if r.empty:
                st.warning(f"'{keyword}' 관련 VOC가 없습니다.")
            else:
                st.success(f"{len(r)}건 검색됨")
                st.dataframe(r[["날짜","게임","L1 태그","L2 태그","상담제목"]])

    # --- 💳 결제/인증 리포트
    with tab_payment:
        st.header("💳 결제/인증 리포트")
        target = voc_df[voc_df["L1 태그"].isin(["계정", "재화/결제"])]
        st.plotly_chart(create_trend_chart(target, date_range, "결제/인증 관련 추이"))

    # --- 🛡️ 어드민 멤버 관리 (하단 고정)
    if is_admin:
        st.markdown("---")
        st.header("🛡️ 어드민 멤버 관리")
        pending = users_df[users_df["status"] == "pending"]
        approved = users_df[users_df["status"] == "approved"]

        with st.expander("⏳ 접근 요청 목록", expanded=True):
            if pending.empty:
                st.info("대기 중인 요청이 없습니다.")
            else:
                for _, r in pending.iterrows():
                    st.write(f"- {r['email']} ({r['name']})")

        with st.expander("✅ 승인된 멤버 목록", expanded=True):
            if approved.empty:
                st.info("승인된 멤버가 없습니다.")
            else:
                for _, r in approved.iterrows():
                    st.write(f"- {r['email']} ({r['name']})")

if __name__ == "__main__":
    main()
