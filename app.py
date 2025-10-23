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
# 1) 유틸 (이미지, URL/키 정규화)
# =============================
def get_image_as_base64(path: str):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

def _pure_url(v: str) -> str:
    """[text](https://...) 형식이면 실제 URL만 추출."""
    if not isinstance(v, str):
        return v
    v = v.strip()
    m = re.match(r"^\[[^\]]+\]\((https?://[^)]+)\)$", v)
    return m.group(1) if m else v

def normalize_sa_info(sa: dict) -> dict:
    """Secrets의 서비스계정 JSON을 정규화(마크다운 링크/줄바꿈)하여 반환."""
    sa = dict(sa or {})
    # URL 정규화
    sa["auth_uri"]  = _pure_url(sa.get("auth_uri", "")) or "https://accounts.google.com/o/oauth2/auth"
    sa["token_uri"] = _pure_url(sa.get("token_uri", "")) or "https://oauth2.googleapis.com/token"
    sa["auth_provider_x509_cert_url"] = _pure_url(sa.get("auth_provider_x509_cert_url", "")) or "https://www.googleapis.com/oauth2/v1/certs"

    # client_x509_cert_url 재생성
    client_email = sa.get("client_email", "")
    if client_email:
        sa["client_x509_cert_url"] = f"https://www.googleapis.com/robot/v1/metadata/x509/{_urlquote(client_email)}"

    # private_key 줄바꿈 처리 (Streamlit Cloud 이중 인코딩 대응)
    pk = sa.get("private_key")
    if isinstance(pk, str):
        sa["private_key"] = (
            pk.replace("\\\\n", "\n")   # 2중 이스케이프 (\\\\n) → 실제 줄바꿈
              .replace("\\n", "\n")     # 일반 이스케이프 (\n) → 줄바꿈
              .replace("\r\n", "\n")    # CRLF 정규화
              .strip()
        )
    return sa

def now_kst_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def get_sheet_id() -> str:
    """Secrets 루트(SHEET_ID) 또는 [gcp_service_account].SHEET_ID에서 읽음."""
    sid = st.secrets.get("SHEET_ID", "")
    if not sid:
        sid = st.secrets.get("gcp_service_account", {}).get("SHEET_ID", "")
    return sid

# =============================
# 2) 로그인(OIDC) & 권한
# =============================
def require_login():
    """Streamlit Cloud OIDC 사용."""
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
# 3) Google Sheets 클라이언트
# =============================
@st.cache_resource
def get_gspread_client():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        sa_raw = st.secrets["gcp_service_account"]
        sa = normalize_sa_info(sa_raw)
        creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        st.error("Google 인증 실패: Secrets의 gcp_service_account 구성을 확인하세요.")
        st.exception(e)
        return None

def open_sheet(spreadsheet_id: str):
    gc = get_gspread_client()
    if not gc:
        return None
    try:
        return gc.open_by_key(spreadsheet_id)
    except Exception as e:
        st.error("스프레드시트를 열 수 없습니다. (권한/ID 확인)")
        st.exception(e)
        return None

# =============================
# 이하의 로직은 기존과 동일
# =============================
# (fetch_users_table, approve_user, revoke_user, load_voc_data 등)
# ✳️ 나머지는 기존 코드 그대로 두세요 — 수정 불필요
# 단, 위 normalize_sa_info 부분이 핵심입니다.
