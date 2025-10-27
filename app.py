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
    sa["auth_uri"]  = _pure_url(sa.get("auth_uri", "")) or "https://accounts.google.com/o/oauth2/auth"
    sa["token_uri"] = _pure_url(sa.get("token_uri", "")) or "https://oauth2.googleapis.com/token"
    sa["auth_provider_x509_cert_url"] = _pure_url(sa.get("auth_provider_x509_cert_url", "")) or "https://www.goo
