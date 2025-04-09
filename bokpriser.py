from flask import Flask, request, render_template_string, send_file, redirect, url_for, session, jsonify
from bs4 import BeautifulSoup
import csv
from datetime import datetime, date
import os
import matplotlib.pyplot as plt
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import time
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from playwright.sync_api import sync_playwright
import requests
from googlesearch import search
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import re

app = Flask(__name__)
app.secret_key = "bokpris2025"

# ---- Definiera studentrabatter ----
DISCOUNTS = {
    "bokus": 0.05,  # Normal studentrabatt
    "akademibokhandeln": 0.10
}

# ---- Definiera temporära rabatter ----
TEMPORARY_DISCOUNTS = {
    "bokus": {
        "discount": 0.10,  # APRIL25 rabatt
        "start_date": "2025-04-01",
        "end_date": "2025-04-30",
        "name": "APRIL25"
    }
}

FREE_SHIPPING_THRESHOLD = {
    "bokus": 249,
    "akademibokhandeln": 300
}

SHIPPING_COST = 29
BEVAKNINGSFIL = "bevakade_isbn.txt"
PRISCV = "prishistorik.csv"
GRAF_MAPP = "static/grafer"

os.makedirs(GRAF_MAPP, exist_ok=True)

store_links = {
    "adlibris_campus": None,
    "studentapan": None,
    "bokus": None,
    "akademibokhandeln": None
}

# Cache för grafer
graf_cache = {}

# ---- Lägg till ISBN i bevakningslista ----
def lägg_till_i_bevakning(isbn):
    os.makedirs(os.path.dirname(BEVAKNINGSFIL), exist_ok=True) if os.path.dirname(BEVAKNINGSFIL) else None
    if not os.path.exists(BEVAKNINGSFIL):
        with open(BEVAKNINGSFIL, "w", encoding="utf-8") as f:
            f.write(isbn + "\n")
    else:
        with open(BEVAKNINGSFIL, "r+", encoding="utf-8") as f:
            alla = set(line.strip() for line in f.readlines())
            if isbn not in alla:
                f.write(isbn + "\n")

# ---- Spara historik till CSV ----
def save_price_history(isbn, butik, pris):
    datum = datetime.now().strftime("%Y-%m-%d")
    pris_siffra = pris.replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", ".").split("–")[0].strip()
    
    try:
        file_path = PRISCV
        file_exists = os.path.exists(file_path)
        
        existing_data = []
        if file_exists:
            with open(file_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                existing_data = list(reader)
        
        dagens_data_exists = any(
            row[0] == datum and row[1] == isbn and row[2] == butik
            for row in existing_data
        )
        
        if not dagens_data_exists:
            with open(file_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["datum", "isbn", "butik", "pris"])
                writer.writerow([datum, isbn, butik, pris_siffra])
            print(f"Debug - Sparar ny prispunkt för {butik}: {pris_siffra} kr ({datum})")
        else:
            print(f"Debug - Hoppar över duplicerad prispunkt för {butik} ({datum})")
            
    except Exception as e:
        print(f"Debug - Fel vid sparande av prishistorik: {str(e)}")

# ---- Schemaläggare för automatisk uppdatering ----
scheduler = BackgroundScheduler()
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ---- Hämta alla bevakade ISBN med senaste uppdatering ----
def get_watched_isbns():
    try:
        if not os.path.exists(PRISCV):
            return {}
            
        with open(PRISCV, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)
            isbn_dates = {}
            for row in reader:
                datum, isbn = row[0], row[1]
                if isbn not in isbn_dates or datum > isbn_dates[isbn]:
                    isbn_dates[isbn] = datum
            return isbn_dates
    except Exception as e:
        print(f"Debug - Fel vid hämtning av bevakade ISBN: {str(e)}")
        return {}

# ---- Automatisk uppdatering av priser ----
def update_all_prices():
    isbn_dates = get_watched_isbns()
    today = datetime.now().strftime("%Y-%m-%d")
    
    needs_update = [
        isbn for isbn, last_update in isbn_dates.items()
        if last_update < today
    ]
    
    if not needs_update:
        print("Debug - Inga ISBN behöver uppdateras")
        return
        
    print(f"Debug - Uppdaterar priser för {len(needs_update)} ISBN")
    
    for isbn in needs_update:
        print(f"Debug - Uppdaterar priser för ISBN: {isbn}")
        get_adlibris_info(isbn)
        get_price_studentapan(isbn)
        get_price_bokus(isbn)
        get_price_akademibokhandeln(isbn)

# ---- Schemalagd automatisk skanning ----
def schemalagd_skanning():
    print("Debug - Startar schemalagd skanning")
    update_all_prices()

# Schemalägg daglig uppdatering kl 00:01
scheduler.add_job(
    schemalagd_skanning,
    'cron',
    hour=0,
    minute=1,
    id='daily_price_update'
)

# ---- Hjälpfunktioner ----
def format_price(price_text):
    if not price_text:
        return "0 kr"
    price = price_text.replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        price_float = float(price)
        return f"{price_float:.2f} kr"
    except ValueError:
        return "0 kr"

def calculate_discounted_price(base_price, discount_rate, shipping_cost, free_shipping_threshold, store_name):
    try:
        base_price_float = float(base_price.replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", "."))
        
        current_date = date.today()
        temp_discount = None
        
        if store_name.lower() in TEMPORARY_DISCOUNTS:
            temp_discount_info = TEMPORARY_DISCOUNTS[store_name.lower()]
            start_date = datetime.strptime(temp_discount_info["start_date"], "%Y-%m-%d").date()
            end_date = datetime.strptime(temp_discount_info["end_date"], "%Y-%m-%d").date()
            
            if start_date <= current_date <= end_date:
                temp_discount = temp_discount_info["discount"]
        
        effective_discount = temp_discount if temp_discount is not None else discount_rate
        discounted = base_price_float * (1 - effective_discount)
        shipping_added = False
        
        if discounted < free_shipping_threshold:
            discounted += shipping_cost
            shipping_added = True
            
        price_text = f"{discounted:.2f} kr"
        
        discount_percent = int(effective_discount * 100)
        if temp_discount is not None:
            discount_name = TEMPORARY_DISCOUNTS[store_name.lower()]["name"]
            if shipping_added:
                price_text += f" (inkl. {discount_percent}% {discount_name}-rabatt + {shipping_cost} kr frakt)"
            else:
                price_text += f" (inkl. {discount_percent}% {discount_name}-rabatt, fri frakt)"
        else:
            if shipping_added:
                price_text += f" (inkl. {discount_percent}% studentrabatt + {shipping_cost} kr frakt)"
            else:
                price_text += f" (inkl. {discount_percent}% studentrabatt, fri frakt)"
            
        return price_text
    except ValueError:
        return "0 kr"

def get_soup(url, headers=None):
    session = requests.Session()
    if not headers:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "sv-SE,sv;q=0.8,en-US;q=0.5,en;q=0.3"
        }
    try:
        response = session.get(url, headers=headers)
        
        if 'Content-Encoding' in response.headers:
            if response.headers['Content-Encoding'] == 'br':
                content = response.content
            else:
                content = response.text
        else:
            content = response.text
            
        try:
            soup = BeautifulSoup(content, "html.parser")
            return soup
        except Exception as e:
            try:
                soup = BeautifulSoup(response.content, "html.parser")
                return soup
            except Exception as e2:
                return None
                
    except Exception as e:
        return None

def get_book_title_from_isbn(isbn):
    """Hämtar bokens titel från ISBN med hjälp av Google Books API"""
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        response = requests.get(url)
        data = response.json()
        
        if data.get('items'):
            title = data['items'][0]['volumeInfo']['title']
            # Formatera titeln till URL-vänligt format
            formatted_title = re.sub(r'[^a-zA-Z0-9\s-]', '', title)
            formatted_title = formatted_title.lower().replace(' ', '-')
            return formatted_title
        return None
    except Exception as e:
        print(f"Debug - Fel vid hämtning av boktitel: {str(e)}")
        return None

def get_adlibris_info(isbn):
    try:
        print("\n=== DEBUG: Adlibris Campus Process Start ===")
        isbn_clean = isbn.replace("-", "")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            )
            page = context.new_page()
            
            print("Debug - Går till huvudsidan...")
            page.goto("https://adlibriscampus.com")
            page.wait_for_load_state("networkidle", timeout=10000)
            
            try:
                print("Debug - Försöker hantera cookie-popup på huvudsidan...")
                page.wait_for_selector("button:has-text('Godkänn samtliga cookies')", timeout=10000)
                page.click("button:has-text('Godkänn samtliga cookies')")
                print("Debug - Cookie-popup på huvudsidan hanterad")
            except Exception as e:
                print(f"Debug - Ingen cookie-popup hittad på huvudsidan eller kunde inte hanteras: {str(e)}")
                try:
                    page.wait_for_selector("button[data-testid='didomi-notice-agree-button']", timeout=5000)
                    page.click("button[data-testid='didomi-notice-agree-button']")
                    print("Debug - Hittade alternativ cookie-knapp på huvudsidan")
                except Exception as e2:
                    print(f"Debug - Kunde inte hitta alternativa cookie-knappar: {str(e2)}")
            
            page.wait_for_load_state("networkidle", timeout=10000)
            
            print("Debug - Söker efter sökrutan...")
            search_box = page.wait_for_selector("input[type='search']", timeout=5000)
            if search_box:
                print("Debug - Hittade sökrutan, skriver in ISBN...")
                search_box.fill(isbn_clean)
                search_box.press("Enter")
                
                print("Debug - Väntar på sökresultat...")
                page.wait_for_load_state("networkidle", timeout=10000)
                
                current_url = page.url
                print(f"Debug - Nuvarande URL: {current_url}")
                
                if f"/b/{isbn_clean}" in current_url:
                    print("Debug - Redan på rätt produktsida, fortsätter direkt...")
                else:
                    print("Debug - Försöker hitta första sökresultatet...")
                    first_result = page.wait_for_selector("a[href*='/b/']", timeout=5000)
                    if first_result:
                        print("Debug - Hittade första sökresultatet, klickar...")
                        first_result.click()
                        print("Debug - Väntar på att produktsidan ska laddas...")
                        page.wait_for_load_state("networkidle", timeout=10000)
                    else:
                        print("Debug - Kunde inte hitta några sökresultat")
                        browser.close()
                        return "Inga sökresultat hittades", "0 kr"
            else:
                print("Debug - Kunde inte hitta sökrutan")
                browser.close()
                return "Fel vid hämtning från Adlibris Campus", "0 kr"
            
            title_element = page.query_selector("h1.heading-default-styling")
            if title_element:
                title = title_element.inner_text().strip()
                print(f"Debug - Hittade titel: {title}")
            else:
                print("Debug - Kunde inte hitta titel")
                title = "Okänd titel"
            
            current_url = page.url
            if current_url and "/b/" in current_url:
                store_links["adlibris_campus"] = current_url
                print(f"Debug - Sparat produktlänk: {current_url}")
            else:
                print("Debug - Kunde inte hitta produktlänk")
            
            price_element = page.query_selector("div.text-xl.font-bold.leading-tight.text-content-sale")
            if price_element:
                price = price_element.inner_text().strip()
                print(f"Debug - Hittade pris: {price}")
                
                clean_price = price.replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
                clean_price = ''.join(c for c in clean_price if c.isdigit() or c == '.')
                formatted_price = format_price(clean_price)
                
                try:
                    numeric_price = float(clean_price)
                    save_price_history(isbn, "adlibris_campus", str(numeric_price))
                    print(f"Debug - Sparat numeriskt pris i CSV: {numeric_price}")
                except ValueError as e:
                    print(f"Debug - Kunde inte konvertera pris till numeriskt värde: {str(e)}")
                    save_price_history(isbn, "adlibris_campus", "0")
                
                print(f"Debug - Slutligt pris: {formatted_price}")
                print("=== DEBUG: Adlibris Campus Process End ===\n")
                browser.close()
                return title, formatted_price
            else:
                print("Debug - Kunde inte hitta pris")
                browser.close()
                return title, "0 kr"
            
    except Exception as e:
        print(f"Debug - Fel vid hämtning från Adlibris Campus: {str(e)}")
        return "Fel vid hämtning från Adlibris Campus", "0 kr"

# ---- Funktion: Hämta pris från Studentapan ----
def get_price_studentapan(isbn):
    try:
        url = f"https://www.studentapan.se/kurslitteratur/macroeconomics-global-edition-{isbn}"
        store_links["studentapan"] = url
        
        soup = get_soup(url)
        if not soup:
            return "0 kr"
            
        price_element = soup.select_one(".Sidebar_conditionValue__Dmogn")
        if not price_element:
            return "0 kr"
            
        price = format_price(price_element.get_text(strip=True))
        save_price_history(isbn, "studentapan", price)
        return price
        
    except Exception as e:
        print(f"Debug - Fel vid hämtning från Studentapan: {str(e)}")
        return "0 kr"

# ---- Funktion: Hämta pris från Bokus (studentrabatt + kampanj + frakt) ----
def get_price_bokus(isbn):
    try:
        url = f"https://www.bokus.com/cgi-bin/product_search.cgi?ac_used=no&search_word={isbn}"
        store_links["bokus"] = url
        
        soup = get_soup(url)
        if not soup:
            return "0 kr"
            
        price_element = soup.select_one("span.pricing__price")
        if not price_element:
            return "0 kr"
            
        base_price = price_element.get_text(strip=True)
        # Spara priset utan "kr" och andra tecken
        clean_price = base_price.replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
        save_price_history(isbn, "bokus", clean_price)
        
        # Beräkna och returnera det rabatterade priset för visning
        price = calculate_discounted_price(
            base_price,
            DISCOUNTS["bokus"],
            SHIPPING_COST,
            FREE_SHIPPING_THRESHOLD["bokus"],
            "bokus"
        )
        return price
        
    except Exception as e:
        print(f"Debug - Fel vid hämtning från Bokus: {str(e)}")
        return "0 kr"

# ---- Funktion: Hämta pris från Akademibokhandeln (studentrabatt + frakt) ----
def get_price_akademibokhandeln(isbn):
    try:
        isbn_clean = isbn.replace("-", "")
        url = f"https://www.akademibokhandeln.se/bok/isbn/{isbn_clean}"
        store_links["akademibokhandeln"] = url
        
        soup = get_soup(url)
        if not soup:
            return "0 kr"
            
        price_element = soup.find("meta", {"property": "product:price:amount"})
        if not price_element or not price_element.get("content"):
            return "0 kr"
            
        base_price = price_element["content"]
        # Spara priset utan "kr" och andra tecken
        clean_price = str(base_price).replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
        save_price_history(isbn, "akademibokhandeln", clean_price)
        
        # Beräkna och returnera det rabatterade priset för visning
        price = calculate_discounted_price(
            base_price,
            DISCOUNTS["akademibokhandeln"],
            SHIPPING_COST,
            FREE_SHIPPING_THRESHOLD["akademibokhandeln"],
            "akademibokhandeln"
        )
        return price
        
    except Exception as e:
        print(f"Debug - Fel vid hämtning från Akademibokhandeln: {str(e)}")
        return "0 kr"

# ---- Visa försäljningsgraf (endast Studentapan och Adlibris) ----
@app.route("/forsaljning")
def visa_forsaljning():
    isbn = request.args.get("isbn")
    if not isbn:
        return "ISBN krävs i URL:en", 400

    try:
        df = pd.read_csv(PRISCV, dtype={"isbn": str})
        df = df[df["isbn"] == isbn]
        df = df[df["butik"].isin(["adlibris_campus", "studentapan"])]
        if df.empty:
            return "Ingen data för försäljning hittad för detta ISBN", 404
        df["datum"] = pd.to_datetime(df["datum"])
        df["pris"] = pd.to_numeric(df["pris"], errors='coerce')
        df = df.dropna()
        plt.figure(figsize=(10, 6))
        for butik in df["butik"].unique():
            butik_df = df[df["butik"] == butik]
            plt.plot(butik_df["datum"], butik_df["pris"], marker='o', label=butik.title())
        plt.title(f"Försäljningspris för ISBN {isbn}")
        plt.xlabel("Datum")
        plt.ylabel("Pris (kr)")
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        filename = os.path.join(GRAF_MAPP, f"forsaljning_{isbn}.png")
        plt.savefig(filename)
        plt.close()
        return send_file(filename, mimetype='image/png')
    except Exception as e:
        return f"Fel vid generering av försäljningsgraf: {e}", 500

# ---- Visa inköpsgraf (ALLA butiker) ----
@app.route("/inkop")
def visa_inkopsgraf():
    isbn = request.args.get("isbn")
    if not isbn:
        return "ISBN krävs i URL:en", 400
    try:
        df = pd.read_csv(PRISCV, dtype={"isbn": str})
        df = df[df["isbn"] == isbn]
        if df.empty:
            return "Ingen data hittad för detta ISBN", 404
        df["datum"] = pd.to_datetime(df["datum"])
        df["pris"] = pd.to_numeric(df["pris"], errors='coerce')
        df = df.dropna()
        plt.figure(figsize=(10, 6))
        for butik in df["butik"].unique():
            butik_df = df[df["butik"] == butik]
            plt.plot(butik_df["datum"], butik_df["pris"], marker='o', label=butik.title())
        plt.title(f"Inköpspris för ISBN {isbn}")
        plt.xlabel("Datum")
        plt.ylabel("Pris (kr)")
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        filename = os.path.join(GRAF_MAPP, f"forsaljning_{isbn}.png")
        plt.savefig(filename)
        plt.close()
        return send_file(filename, mimetype='image/png')
    except Exception as e:
        return f"Fel vid generering av inköpsgraf: {e}", 500


# ---- HTML-länkar till grafer ----
def grafikon(isbn):
    return f"""
    <div style="margin-top: 40px; display: flex; justify-content: space-between;">
        <a href="/forsaljning?isbn={isbn}" target="_blank" title="Visa försäljningsgraf"
           style="min-width: 180px; background-color: #167d37; color: white; font-weight: bold;
                  padding: 10px 20px; border-radius: 8px; text-decoration: none; display: inline-block;">📊 Försäljningsgraf</a>
        <a href="/inkop?isbn={isbn}" target="_blank" title="Visa prisgraf"
           style="min-width: 180px; background-color: #167d37; color: white; font-weight: bold;
                  padding: 10px 20px; border-radius: 8px; text-decoration: none; display: inline-block;">📈 Inköpspris</a>
    </div>
    """

# Inbäddad försäljningsgraf
@app.route("/forsaljning-embed")
def visa_forsaljning_embed():
    isbn = request.args.get("isbn")
    if not isbn:
        return "", 400
    try:
        df = pd.read_csv(PRISCV, dtype={"isbn": str})
        df["butik"] = df["butik"].astype(str).str.strip().str.lower()
        df = df[df["isbn"] == isbn]
        df = df[df["butik"].isin(["adlibris_campus", "studentapan"])]
        df["datum"] = pd.to_datetime(df["datum"])
        df["pris"] = pd.to_numeric(df["pris"], errors='coerce')
        df = df.dropna()
        if df.empty:
            return "", 204
        return generera_interaktiv_graf(df, isbn, typ="forsaljning")
    except Exception as e:
        return f"Fel: {e}", 500

# Inbäddad inköpsgraf
@app.route("/inkop-embed")
def visa_inkop_embed():
    isbn = request.args.get("isbn")
    if not isbn:
        return "", 400
    try:
        df = pd.read_csv(PRISCV, dtype={"isbn": str})
        df["butik"] = df["butik"].astype(str).str.strip().str.lower()
        df = df[df["isbn"] == isbn]
        df["datum"] = pd.to_datetime(df["datum"])
        df["pris"] = pd.to_numeric(df["pris"], errors='coerce')
        df = df.dropna()
        if df.empty:
            return "", 204
        return generera_interaktiv_graf(df, isbn, typ="inkop")
    except Exception as e:
        return f"Fel: {e}", 500

# Skanna enskild
@app.route("/skanna-enskild", methods=["POST", "GET"])
def skanna_enskild():
    if request.method == "POST":
        isbn = request.form.get("isbn")
        session["last_isbn"] = isbn
        return redirect(url_for("skanna_enskild"))

    isbn = session.get("last_isbn")
    if not isbn:
        return redirect("/")

    titel, adlibris_price = get_adlibris_info(isbn)
    studentapan_price = get_price_studentapan(isbn)
    bokus_price = get_price_bokus(isbn)
    akademibok_price = get_price_akademibokhandeln(isbn)

    def text_to_float(text):
        try:
            return float(text.replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", ".").split("(")[0].strip())
        except:
            return 0.0

    studentapan_total = text_to_float(studentapan_price)
    adlibris_total = text_to_float(adlibris_price)

    fixed_fee_studentapan = 72
    fixed_fee_adlibris = 49

    vinst_studentapan = (studentapan_total - fixed_fee_studentapan) / 1.19 if studentapan_total > 0 else 0
    vinst_adlibris = adlibris_total * 0.82 - fixed_fee_adlibris if adlibris_total > 0 else 0

    # 🔥 Nu snabbare direktanrop till graferna
    forsaljning_html = visa_forsaljning_embed_direct(isbn)
    inkop_html = visa_inkop_embed_direct(isbn)

    html = f"""
    <html>
    <head>
        <title>📘 Prisjämförelse</title>
        <style>
            body {{
                font-family: 'Segoe UI', sans-serif;
                background-color: #0d4954;
                color: #333;
                margin: 0;
                padding: 0;
                height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                overflow-x: auto;
                min-width: 1600px;
            }}
            .layout-wrapper {{
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                flex-wrap: wrap;
                gap: 20px;
                padding: 250px;
                margin: 0 auto;
                width: 100%;
            }}
            .container {{
                background-color: white;
                padding: 30px;
                border-radius: 16px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                flex: 1 1 auto;
                min-width: 320px;
                max-width: 500px;
                text-align: center;
            }}
            .graf-container {{
                background-color: white;
                padding: 35px;
                border-radius: 16px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                flex: 0 0 auto;
                min-width: 320px;
                max-width: 500px;
                align-self: center;
                text-align: center;
                width: 80%;
                height: 350px;
            }}
            h1 {{ color: #2c3e50; }}
            ul {{
                list-style-type: none;
                padding: 0;
                margin-top: 30px;
            }}
            li {{
                background-color: #f0f0f0;
                margin: 10px 0;
                padding: 15px;
                border-radius: 10px;
                font-size: 18px;
            }}
            .price {{ font-weight: bold; }}
            .vinst {{ color: green; font-weight: bold; }}
            .back-button {{
                text-decoration: none;
                color: white;
                background-color: #167d37;
                padding: 10px 15px;
                border-radius: 8px;
                font-weight: bold;
                display: inline-block;
                margin-top: 30px;
                transition: background-color 0.3s;
            }}
            .back-button:hover {{
                background-color: #2980b9;
            }}
        </style>
    </head>
    <body>
        <div class="layout-wrapper">
            <div class="graf-container">
                <h3>📊 Försäljningsgraf</h3>
                <canvas id="forsaljningChart"></canvas>
            </div>
            <div class="container">
                <h1>📘 Prisjämförelse för<br>{titel} <br><span style="font-size:16px; color:#777e;">(ISBN: {isbn})</span></h1>
                <ul>
                    <li>
                        <a href="{store_links['adlibris_campus']}" target="_blank"><strong>📕 Adlibris Campus</strong></a><br>
                        <span class="price">{adlibris_price}</span><br>
                        Försäljningspris: <span class="vinst">{vinst_adlibris:.2f} kr</span>
                    </li>
                    <li>
                        <a href="{store_links['studentapan']}" target="_blank"><strong>📘 Studentapan</strong></a><br>
                        <span class="price">{studentapan_price}</span><br>
                        Försäljningspris: <span class="vinst">{vinst_studentapan:.2f} kr</span>
                    </li>
                    <li>
                        <a href="{store_links['bokus']}" target="_blank"><strong>📗 Bokus</strong></a><br>
                        <span class="price">{bokus_price}</span><br>
                        Försäljningspris: <span class="vinst">Ej tillgänglig</span>
                    </li>
                    <li>
                        <a href="{store_links['akademibokhandeln']}" target="_blank"><strong>🏫 Akademibokhandeln</strong></a><br>
                        <span class="price">{akademibok_price}</span><br>
                        Försäljningspris: <span class="vinst">Ej tillgänglig</span>
                    </li>
                </ul>
                <a href="/" class="back-button">⬅️ Skanna en annan bok</a>
            </div>
            <div class="graf-container">
                <h3>📈 Inköpsgraf</h3>
                <canvas id="inkopChart"></canvas>
            </div>
        </div>

        <!-- Chart.js + datahämtning -->
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script>
            async function hämtaData(url) {{
                const res = await fetch(url);
                const data = await res.json();
                return data;
            }}

            function ritaGraf(canvasId, data, titel) {{
                const ctx = document.getElementById(canvasId).getContext('2d');
                const datasets = Object.keys(data).map(butik => {{
                    return {{
                        label: butik.charAt(0).toUpperCase() + butik.slice(1),
                        data: data[butik].pris,
                        borderWidth: 2,
                        fill: false,
                        tension: 0.3,
                        pointRadius: 4,
                        pointHoverRadius: 6,
                        borderColor: (() => {{
                            if (butik === "adlibris_campus") return "#007bff";
                            if (butik === "studentapan") return "#28a745";
                            if (butik === "bokus") return "#ff6f00";
                            return "#6c757d";
                        }})()
                    }};
                }});

                new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        labels: data[Object.keys(data)[0]].datum,
                        datasets: datasets
                    }},
                    options: {{
                        responsive: true,
                        plugins: {{
                            legend: {{ position: 'top' }},
                            title: {{
                                display: true,
                                text: titel
                            }}
                        }},
                        scales: {{
                            y: {{
                                title: {{
                                    display: true,
                                    text: 'Pris (kr)'
                                }}
                            }},
                            x: {{
                                title: {{
                                    display: true,
                                    text: 'Datum'
                                }}
                            }}
                        }}
                    }}
                }});
            }}

            const isbn = "{isbn}";

            hämtaData(`/graf-data/forsaljning?isbn=${{isbn}}`)
                .then(data => ritaGraf("forsaljningChart", data, "Försäljningspris"));

            hämtaData(`/graf-data/inkop?isbn=${{isbn}}`)
                .then(data => ritaGraf("inkopChart", data, "Inköpspris"));
        </script>
    </body>
    </html>
    """
    return html


# ---- Startformulär ----
@app.route("/", methods=["GET"])
def index():
    return render_template_string("""
    <html>
    <head>
        <title>📘 Bokprisskanner</title>
        <style>
            body {
                font-family: 'Segoe UI', sans-serif;
                background-color: #ecf0f1;
                padding: 60px;
                text-align: center;
            }
            .form-container {
                background: white;
                padding: 40px;
                border-radius: 16px;
                max-width: 500px;
                margin: auto;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }
            input {
                padding: 10px;
                width: 80%;
                font-size: 16px;
                border: 1px solid #ccc;
                border-radius: 6px;
            }
            input[type="submit"] {
                margin-top: 20px;
                background-color: #27ae60;
                color: white;
                border: none;
                cursor: pointer;
                width: auto;
                padding: 12px 25px;
            }
            input[type="submit"]:hover {
                background-color: #1e8449;
            }
        </style>
    </head>
    <body>
        <div class="form-container">
            <h1>📘 Skanna bok med ISBN</h1>
            <form method="post" action="/skanna-enskild">
                <input name="isbn" placeholder="Skriv in ISBN..." required><br>
                <input type="submit" value="🔍 Skanna ISBN">
            </form>
        </div>
    </body>
    </html>
    """)

import plotly.graph_objs as go

# Grafgenerering
def generera_interaktiv_graf(df, isbn, typ="forsaljning"):
    title_map = {
        "forsaljning": "Försäljningspris",
        "inkop": "Inköpspris"
    }

    fig = go.Figure()

    for butik in df["butik"].unique():
        butik_df = df[df["butik"] == butik]
        fig.add_trace(go.Scatter(
            x=butik_df["datum"],
            y=butik_df["pris"],
            mode="lines+markers",
            name=butik.title()
        ))

    fig.update_layout(
        title=f"{title_map.get(typ, typ)} – ISBN {isbn}",
        xaxis_title="Datum",
        yaxis_title="Pris (kr)",
        template="plotly_white",
        height=500
    )

    return fig.to_html(full_html=False)

def visa_forsaljning_embed_direct(isbn):
    cache_key = f"forsaljning_{isbn}"
    now = time.time()
    if cache_key in graf_cache and now - graf_cache[cache_key]["timestamp"] < 1800:
        return graf_cache[cache_key]["html"]

    try:
        df = pd.read_csv(PRISCV, dtype={"isbn": str})
        df["butik"] = df["butik"].astype(str).str.strip().str.lower()
        df = df[df["isbn"] == isbn]
        df = df[df["butik"].isin(["adlibris_campus", "studentapan"])]
        df["datum"] = pd.to_datetime(df["datum"])
        df["pris"] = pd.to_numeric(df["pris"], errors='coerce')
        df = df.dropna()
        if df.empty:
            return ""
        html = generera_interaktiv_graf(df, isbn, typ="forsaljning")
        graf_cache[cache_key] = {"html": html, "timestamp": now}
        return html
    except Exception as e:
        return f"<p style='color:red;'>Fel: {e}</p>"

def visa_inkop_embed_direct(isbn):
    cache_key = f"inkop_{isbn}"
    now = time.time()
    if cache_key in graf_cache and now - graf_cache[cache_key]["timestamp"] < 1800:
        return graf_cache[cache_key]["html"]

    try:
        df = pd.read_csv(PRISCV, dtype={"isbn": str})
        df["butik"] = df["butik"].astype(str).str.strip().str.lower()
        df = df[df["isbn"] == isbn]
        df["datum"] = pd.to_datetime(df["datum"])
        df["pris"] = pd.to_numeric(df["pris"], errors='coerce')
        df = df.dropna()
        if df.empty:
            return ""
        html = generera_interaktiv_graf(df, isbn, typ="inkop")
        graf_cache[cache_key] = {"html": html, "timestamp": now}
        return html
    except Exception as e:
        return f"<p style='color:red;'>Fel: {e}</p>"

@app.route("/graf-data/forsaljning")
def grafdata_forsaljning():
    isbn = request.args.get("isbn")
    if not isbn:
        return jsonify({"error": "Missing ISBN"}), 400

    try:
        df = pd.read_csv(PRISCV, dtype={"isbn": str})
        df["butik"] = df["butik"].astype(str).str.strip().str.lower()
        df = df[df["isbn"] == isbn]
        df = df[df["butik"].isin(["adlibris_campus", "studentapan"])]
        df["datum"] = pd.to_datetime(df["datum"])
        df["pris"] = pd.to_numeric(df["pris"], errors="coerce")
        
        # Beräkna försäljningspris
        df.loc[df["butik"] == "adlibris_campus", "pris"] = df.loc[df["butik"] == "adlibris_campus", "pris"] * 0.82 - 49
        df.loc[df["butik"] == "studentapan", "pris"] = (df.loc[df["butik"] == "studentapan", "pris"] - 72) / 1.19
        
        df = df.dropna()

        data = {}
        for butik in df["butik"].unique():
            butik_df = df[df["butik"] == butik].sort_values("datum")
            data[butik] = {
                "datum": butik_df["datum"].dt.strftime("%Y-%m-%d").tolist(),
                "pris": butik_df["pris"].tolist()
            }

        return jsonify(data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/graf-data/inkop")
def grafdata_inkop():
    isbn = request.args.get("isbn")
    if not isbn:
        return jsonify({"error": "Missing ISBN"}), 400

    try:
        print("Debug - Läser prishistorik för inköpsgraf")
        df = pd.read_csv(PRISCV, dtype={"isbn": str, "pris": str})  # Läs pris som sträng först
        df["butik"] = df["butik"].astype(str).str.strip().str.lower()
        print(f"Debug - Unika butiker i data: {df['butik'].unique()}")
        
        df = df[df["isbn"] == isbn]
        # Ta bort "adlibris" från data
        df = df[df["butik"] != "adlibris"]
        print(f"Debug - Antal rader för ISBN {isbn}: {len(df)}")
        print(f"Debug - Butiker för detta ISBN: {df['butik'].unique()}")
        print("Debug - Priser före konvertering:")
        print(df[["butik", "pris"]])
        
        df["datum"] = pd.to_datetime(df["datum"])
        # Rensa och konvertera priser mer försiktigt
        df["pris"] = df["pris"].apply(lambda x: str(x).replace("kr", "").replace(" ", "").replace(",", ".").split("(")[0].strip())
        df["pris"] = pd.to_numeric(df["pris"], errors="coerce")
        
        print("Debug - Priser efter konvertering:")
        print(df[["butik", "pris"]])
        
        df = df.dropna()
        
        print(f"Debug - Slutliga butiker efter databehandling: {df['butik'].unique()}")
        print(f"Debug - Slutligt antal rader: {len(df)}")

        data = {}
        for butik in df["butik"].unique():
            butik_df = df[df["butik"] == butik].sort_values("datum")
            data[butik] = {
                "datum": butik_df["datum"].dt.strftime("%Y-%m-%d").tolist(),
                "pris": butik_df["pris"].tolist()
            }
            print(f"Debug - Data för {butik}: {len(data[butik]['datum'])} datapunkter")

        return jsonify(data)

    except Exception as e:
        print(f"Debug - Fel i grafdata_inkop: {str(e)}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)

