# -*- coding: utf-8 -*-
import os
import re
import base64
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
# 기본 설정
# =============================
LOGO_IMAGE = "images/pmang_logo.png"
st.set_page_config(page_title="웹보드 VOC 대시보드", page_icon=LOGO_IMAGE, layout="wide")
KST = ZoneInfo("Asia/Seoul")

# =============================
# 유틸 함수
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

def get_sheet_id() -> str:
    sid = st.secrets.get("SHEET_ID", "")
    if not sid:
        sid = st.secrets.get("gcp_service_account", {}).get("SHEET_ID", "")
    return sid

# =============================
# 로그인 및 권한
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
        "sub": _g(st.user, "sub", ""),
    }

# =============================
# Google Sheets 클라이언트
# =============================
@st.cache_resource
def get_gspread_client():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = None
        try:
            sa_raw = st.secrets["gcp_service_account"]
            sa = normalize_sa_info(sa_raw)
            creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
        except Exception:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            f = os.path.join(script_dir, "service_account.json")
            creds = service_account.Credentials.from_service_account_file(f, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        st.error("Google 인증 실패")
        st.exception(e)
        return None

def open_sheet(spreadsheet_id: str):
    gc = get_gspread_client()
    try:
        return gc.open_by_key(spreadsheet_id)
    except Exception:
        st.error("스프레드시트를 열 수 없습니다.")
        return None

def get_or_create_user_mgmt_worksheet(ss):
    try:
        ws = ss.worksheet("user_management")
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title="user_management", rows=1000, cols=20)
        ws.append_row(["email","name","request_date","status","approved_date"])
    return ws

@st.cache_data(ttl=60)
def fetch_users_table(spreadsheet_id):
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return pd.DataFrame(columns=["email","name","request_date","status","approved_date"])
    ws = get_or_create_user_mgmt_worksheet(ss)
    rows = ws.get_all_records()
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["email","name","request_date","status","approved_date"])

def submit_access_request(spreadsheet_id, email, name):
    ss = open_sheet(spreadsheet_id)
    ws = get_or_create_user_mgmt_worksheet(ss)
    df = fetch_users_table(spreadsheet_id)
    if (df["email"].str.lower() == email.lower()).any():
        st.info("이미 요청된 이메일입니다.")
        return
    ws.append_row([email, name, now_kst_str(), "pending", ""])
    st.success("접근 요청 완료! 관리자의 승인을 기다려주세요.")
    st.cache_data.clear()

def approve_user(spreadsheet_id, email):
    ss = open_sheet(spreadsheet_id)
    ws = get_or_create_user_mgmt_worksheet(ss)
    cell = ws.find(email)
    ws.update_cell(cell.row, 4, "approved")
    ws.update_cell(cell.row, 5, now_kst_str())
    st.toast(f"{email} 승인 완료")
    st.cache_data.clear()
    st.rerun()

def revoke_user(spreadsheet_id, email):
    ss = open_sheet(spreadsheet_id)
    ws = get_or_create_user_mgmt_worksheet(ss)
    cell = ws.find(email)
    ws.delete_rows(cell.row)
    st.toast(f"{email} 권한 삭제 완료")
    st.cache_data.clear()
    st.rerun()

def is_approved(df, email):
    if df.empty:
        return False
    row = df.loc[df["email"].str.lower() == (email or "").lower()]
    return (not row.empty) and (row.iloc[0]["status"] == "approved")

# =============================
# VOC 데이터 로드
# =============================
L2_TO_L1_MAPPING = {
    '로그인/인증': '계정', '정보 관리': '계정', '기술 오류': '시스템/환경',
    '결제 오류/미지급': '재화/결제', '환불/청약철회': '재화/결제', '재화 소실/오류': '재화/결제',
    '클래스/구독 상품': '재화/결제', '재화 정책/한도': '재화/결제',
    '밸런스/불만 (패몰림)': '게임 플레이', '콘텐츠 오류/문의': '게임 플레이', '토너먼트/대회': '게임 플레이',
    '점령전/거점전': '게임 플레이', '랭킹페스타': '게임 플레이', '연승챌린지': '게임 플레이', '패밀리게임': '게임 플레이',
    '광고/무료충전소': '이벤트/혜택', '이벤트': '이벤트/혜택',
    '비매너/욕설 신고': '불량 이용자', '제재 문의': '불량 이용자',
    '콘텐츠/시스템 건의': '정책/건의 (VOC)', '운영/정책 건의': '정책/건의 (VOC)', '단순 문의/미분류': '기타'
}

@st.cache_data(ttl=600)
def load_voc_data(spreadsheet_id):
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return pd.DataFrame()
    all_data = []
    for ws in ss.worksheets():
        if not re.match(r'^\d{2,4}[-_]\d{2}$', ws.title):  # YY-MM or YYYY-MM
            continue
        rows = ws.get_all_records()
        if rows:
            all_data.extend(rows)
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    if "날짜" not in df.columns:
        return pd.DataFrame()
    df["날짜_dt"] = pd.to_datetime(df["날짜"], format="%y%m%d", errors="coerce")
    df = df.dropna(subset=["날짜_dt"])
    df["날짜_dt"] = df["날짜_dt"].dt.tz_localize("UTC").dt.tz_convert(KST)
    df["L2 태그"] = df["taglist"]
    df["L1 태그"] = df["L2 태그"].map(L2_TO_L1_MAPPING).fillna("기타")
    return df

# =============================
# 시각화 함수
# =============================
def create_trend_chart(df, dr, title):
    start, end = dr
    all_days = pd.date_range(start=start, end=end)
    daily = df.groupby(df["날짜_dt"].dt.date).size().reset_index(name="건수")
    daily["날짜_dt"] = pd.to_datetime(daily["날짜_dt"])
    merged = pd.merge(pd.DataFrame(all_days, columns=["날짜_dt"]), daily, on="날짜_dt", how="left").fillna(0)
    fig = px.line(merged, x="날짜_dt", y="건수", title=f"<b>{title}</b>", markers=True)
    fig.update_layout(height=300)
    return fig

def create_donut_chart(df, title, group_by="L2 태그"):
    counts = df[group_by].value_counts()
    if len(counts) > 5:
        top4 = counts.nlargest(4)
        counts = pd.concat([top4, pd.Series([counts.iloc[4:].sum()], index=["기타"])])
    fig = go.Figure(data=[go.Pie(labels=counts.index, values=counts.values, hole=.6)])
    fig.update_layout(title_text=f"<b>{title}</b>", height=300)
    return fig

# =============================
# 메인
# =============================
def main():
    require_login()
    me = current_user()
    spreadsheet_id = get_sheet_id()
    admin_email = st.secrets.get("app", {}).get("admin_email", "")
    is_admin = me["email"].lower() == admin_email.lower()

    users_df = fetch_users_table(spreadsheet_id)
    if not (is_admin or is_approved(users_df, me["email"])):
        st.warning("권한이 없습니다. 접근 요청을 해주세요.")
        if st.button("접근 요청"):
            submit_access_request(spreadsheet_id, me["email"], me["name"])
        st.stop()

    voc_df = load_voc_data(spreadsheet_id)

    # --- 사이드바 ---
    with st.sidebar:
        st.subheader("📅 기간 선택")
        if voc_df.empty:
            st.warning("VOC 데이터 없음")
            st.stop()
        min_d = voc_df["날짜_dt"].min().date()
        max_d = voc_df["날짜_dt"].max().date()
        default_start = max_d - timedelta(days=6)
        if "date_range" not in st.session_state:
            st.session_state.date_range = (default_start, max_d)
        start, end = st.session_state["date_range"]
        start = max(start, min_d)
        end = min(end, max_d)
        if start > end:
            start, end = default_start, max_d
        safe_value = (start, end)
        st.session_state.date_range = safe_value
        date_range = st.date_input("조회 기간", value=safe_value, min_value=min_d, max_value=max_d)

    start, end = date_range
    filtered = voc_df[(voc_df["날짜_dt"].dt.date >= start) & (voc_df["날짜_dt"].dt.date <= end)]
    if filtered.empty:
        st.warning("표시할 데이터가 없습니다. 필터/기간을 조정하세요.")
        st.stop()

    # --- 탭 ---
    tab_main, tab_search, tab_payment = st.tabs(["📊 카테고리 분석", "🔍 키워드 검색", "💳 결제/인증 리포트"])

    with tab_main:
        st.plotly_chart(create_trend_chart(filtered, (start, end), "일자별 VOC 발생 추이"))
        st.plotly_chart(create_donut_chart(filtered, "주요 L1 카테고리", group_by="L1 태그"))

    with tab_search:
        st.header("🔍 키워드 검색")
        keyword = st.text_input("검색어를 입력하세요", "")
        if keyword:
            r = filtered[filtered["taglist"].str.contains(keyword, na=False)]
            st.write(f"{len(r)}건 검색됨")
            st.dataframe(r)

    with tab_payment:
        st.header("💳 결제/인증 VOC")
        df_pay = filtered[filtered["L1 태그"].isin(["계정", "재화/결제"])]
        st.plotly_chart(create_trend_chart(df_pay, (start, end), "결제/인증 관련 VOC 추이"))

    # --- 어드민 하단 ---
    if is_admin:
        st.markdown("---")
        st.subheader("🛡️ 어드민 멤버 관리")
        df_users = fetch_users_table(spreadsheet_id)
        tab_req, tab_mem = st.tabs(["요청 목록", "승인된 멤버"])
        with tab_req:
            pending = df_users[df_users["status"] == "pending"]
            for _, r in pending.iterrows():
                c1, c2, c3, c4 = st.columns([3,2,2,2])
                c1.write(r["email"])
                c2.write(r["name"])
                if c4.button("승인", key=r["email"]):
                    approve_user(spreadsheet_id, r["email"])
        with tab_mem:
            approved = df_users[df_users["status"] == "approved"]
            for _, r in approved.iterrows():
                c1, c2, c3, c4 = st.columns([3,2,2,2])
                c1.write(r["email"])
                c2.write(r["name"])
                if c4.button("삭제", key="del"+r["email"]):
                    revoke_user(spreadsheet_id, r["email"])

if __name__ == "__main__":
    main()
