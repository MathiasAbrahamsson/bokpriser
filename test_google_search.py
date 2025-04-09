from googlesearch import search

isbn = "9789139117926"  # Exempel-ISBN för Skattelagstiftning
query = f"{isbn} site:adlibriscampus.com"

# Hämta första resultatet från Google
results = list(search(query, num_results=1))
if results:
    print("🔗 Första träffen:", results[0])
else:
    print("❌ Ingen träff hittades.")
