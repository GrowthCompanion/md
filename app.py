import streamlit as st
import requests
import os
from dotenv import load_dotenv
from groq import Groq
import json
import re
import sqlite3
from fuzzywuzzy import fuzz

# Load API keys from .env file
load_dotenv()
nanonets_api_key = os.getenv("NANONETS_API_KEY")
groq_api_key = os.getenv("GROQ_API_KEY")

# Initialize Groq client
groq_client = Groq(api_key=groq_api_key)

# Setup session state
if "chat_stage" not in st.session_state:
    st.session_state.chat_stage = "init"
if "uploaded_file" not in st.session_state:
    st.session_state.uploaded_file = None

# Database functions
def get_all_customers(db_name="customers.db"):
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    cursor.execute("SELECT name, phone_number, email_address, company FROM customers")
    customers = [
        {"name": row[0], "phone_number": row[1], "email_address": row[2], "company": row[3]}
        for row in cursor.fetchall()
    ]
    conn.close()
    return customers

def insert_customer(db_name, customer):
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO customers (name, phone_number, email_address, company)
        VALUES (?, ?, ?, ?)
    ''', (
        customer["name"], customer["phone_number"], customer["email_address"], customer["company"]
    ))
    conn.commit()
    conn.close()

def fuzzy_match_customer(extracted, customers, thresholds={
    "name": 80,
    "phone_number": 90,
    "email_address": 90,
    "company": 80
}):
    for customer in customers:
        name_score = fuzz.token_sort_ratio(extracted.get("name", "") or "", customer.get("name", "") or "")
        phone_score = fuzz.ratio(extracted.get("phone_number", "") or "", customer.get("phone_number", "") or "")
        email_score = fuzz.ratio(extracted.get("email_address", "") or "", customer.get("email_address", "") or "")
        company_score = fuzz.token_sort_ratio(extracted.get("company", "") or "", customer.get("company", "") or "")

        if (email_score >= thresholds["email_address"] or
            phone_score >= thresholds["phone_number"] or
            (name_score >= thresholds["name"] and company_score >= thresholds["company"])):
            return True, customer, {
                "name_score": name_score,
                "phone_score": phone_score,
                "email_score": email_score,
                "company_score": company_score
            }
    return False, None, None

def extract_text_from_image(uploaded_file):
    nanonets_url = "https://app.nanonets.com/api/v2/OCR/FullText"
    files = {'file': (uploaded_file.name, uploaded_file.getvalue(), 'image/jpeg')}
    response = requests.post(
        nanonets_url,
        auth=requests.auth.HTTPBasicAuth(nanonets_api_key, ''),
        files=files
    )
    if response.status_code != 200:
        return ""
    result = response.json()
    raw_text = ""
    def find_raw_text(obj):
        if isinstance(obj, dict):
            if "raw_text" in obj:
                return obj["raw_text"]
            for value in obj.values():
                result = find_raw_text(value)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = find_raw_text(item)
                if result:
                    return result
        return None
    return find_raw_text(result) or ""

def extract_entities_with_groq(raw_text):
    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": (
                    "You are a helpful assistant skilled at extracting structured information from raw text. "
                    "Extract name, phone_number, email_address, and company. Return JSON only."
                )},
                {"role": "user", "content": raw_text}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.5,
            max_completion_tokens=1024
        )
        result = chat_completion.choices[0].message.content
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            json_match = re.search(r'\{.*?\}', result, re.DOTALL)
            return json.loads(json_match.group(0)) if json_match else None
    except Exception as e:
        return None

# UI Title
st.title("📇 Customer Info Chatbot")

# Chatbot flow
if st.session_state.chat_stage == "init":
    user_input = st.chat_input("Type 'hi' to begin")
    if user_input:
        if user_input.lower().strip() == "hi":
            st.session_state.chat_stage = "waiting_for_choice"
            st.experimental_rerun()
        else:
            with st.chat_message("bot"):
                st.markdown("❓ Please type 'hi' to get started.")

elif st.session_state.chat_stage == "waiting_for_choice":
    with st.chat_message("bot"):
        st.markdown("Hi! Do you want to upload a document or enter customer details manually?")
    user_input = st.chat_input("Type 'upload' or 'enter'")
    if user_input:
        if "upload" in user_input.lower():
            st.session_state.chat_stage = "show_upload"
            st.experimental_rerun()
        elif "enter" in user_input.lower():
            st.session_state.chat_stage = "manual_entry"
            st.experimental_rerun()

elif st.session_state.chat_stage == "show_upload":
    uploaded_file = st.file_uploader("Upload a document", type=["jpg", "jpeg", "png"])
    if uploaded_file:
        st.session_state.uploaded_file = uploaded_file
        st.session_state.chat_stage = "process_upload"
        st.experimental_rerun()

elif st.session_state.chat_stage == "process_upload":
    uploaded_file = st.session_state.uploaded_file
    with st.chat_message("bot"):
        st.image(uploaded_file, caption="Uploaded image", use_column_width=True)
        st.markdown("🔍 Extracting text from image...")
    raw_text = extract_text_from_image(uploaded_file)
    with st.chat_message("bot"):
        st.markdown("🧠 Running LLM to format extracted text...")
        st.text(raw_text)
    extracted_data = extract_entities_with_groq(raw_text)
    with st.chat_message("bot"):
        if extracted_data:
            st.markdown("✅ Here's the extracted customer data:")
            st.json(extracted_data)
            customers = get_all_customers()
            is_match, matched, scores = fuzzy_match_customer(extracted_data, customers)
            if is_match:
                st.info("✅ Customer already exists:")
                st.json(matched)
                st.write("**Match Scores:**")
                st.json(scores)
            else:
                st.warning("🆕 This is a new customer.")
                st.json(extracted_data)
                if st.button("✅ Add to database"):
                    insert_customer("customers.db", extracted_data)
                    st.success("Customer added successfully!")
        else:
            st.error("❌ Could not extract structured data from image.")
    with st.chat_message("bot"):
        st.markdown("📥 If you'd like to process another customer, please type 'upload' or 'enter'.")
    st.session_state.chat_stage = "waiting_for_choice"
    st.session_state.uploaded_file = None

elif st.session_state.chat_stage == "manual_entry":
    with st.chat_message("bot"):
        st.markdown("Please enter the customer details, I’ll look it up for you...")
    user_input = st.chat_input("Type details here")
    if user_input:
        extracted_data = extract_entities_with_groq(user_input)
        with st.chat_message("bot"):
            if extracted_data:
                st.markdown("✅ Extracted customer details:")
                st.json(extracted_data)
                customers = get_all_customers()
                is_match, matched, scores = fuzzy_match_customer(extracted_data, customers)
                if is_match:
                    st.info("✅ Customer already exists:")
                    st.json(matched)
                    st.write("**Match Scores:**")
                    st.json(scores)
                else:
                    st.warning("🆕 This is a new customer.")
                    st.json(extracted_data)
                    if st.button("✅ Add to database"):
                        insert_customer("customers.db", extracted_data)
                        st.success("Customer added successfully!")
            else:
                st.error("❌ Could not extract structured data.")
        with st.chat_message("bot"):
            st.markdown("📥 If you'd like to process another customer, please type 'upload' or 'enter'.")
        st.session_state.chat_stage = "waiting_for_choice"
