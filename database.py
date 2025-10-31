import streamlit as st
from sqlalchemy import create_engine, text

DB_URL = st.secrets["DB_URL"]

engine = create_engine(DB_URL)
