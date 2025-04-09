import requests
from bs4 import BeautifulSoup

isbn = "9789144156798"
url = f"https://www.studentapan.se/kurslitteratur/macroeconomics-global-edition-{isbn}"

print(f"🔍 Testar att hämta {url}")

try:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    print(f"🧪 Statuskod: {response.status_code}")
except Exception as e:
    print(f"❌ Fel vid GET: {e}")
    exit()

if response.status_code == 200:
    soup = BeautifulSoup(response.text, "html.parser")

    with open("studentapan_debug.html", "w", encoding="utf-8") as f:
        f.write(soup.prettify())

    print("✅ HTML sparad till studentapan_debug.html")
else:
    print("❌ Kunde inte ladda sidan")
