"""FoodFlation price scanner.

Looks up grocery stores near a ZIP code and records the current price of a
list of products at each one, appending the results to a CSV so prices can
be tracked over time.

Prices come from the Kroger Public API, which covers all Kroger-owned chains
(Kroger, Dillons, Gerbes, Fred Meyer, King Soopers, Ralphs, Smith's, Fry's,
Harris Teeter, ...). Get free credentials at https://developer.kroger.com
(create an app with the "product.compact" scope), then:

    export KROGER_CLIENT_ID=...
    export KROGER_CLIENT_SECRET=...
    python3 webscraping.py 65201
    python3 webscraping.py 65201 --radius 15 --products "milk" "eggs" "bread"
"""

import argparse
import base64
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

KROGER_API = "https://api.kroger.com/v1"

DEFAULT_PRODUCTS = [
    "whole milk gallon",
    "large eggs dozen",
    "white bread",
    "butter",
    "ground beef",
    "chicken breast",
    "bananas",
    "russet potatoes",
    "white rice",
    "peanut butter",
]

CSV_FIELDS = [
    "timestamp", "store", "chain", "store_id", "address", "search_term",
    "product", "brand", "size", "regular_price", "promo_price",
]


def http_json(url, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from None


class KrogerClient:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None
        self._token_expires = 0

    def _auth_header(self):
        if time.time() >= self._token_expires:
            creds = f"{self.client_id}:{self.client_secret}".encode()
            resp = http_json(
                f"{KROGER_API}/connect/oauth2/token",
                headers={
                    "Authorization": "Basic " + base64.b64encode(creds).decode(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=urllib.parse.urlencode({
                    "grant_type": "client_credentials",
                    "scope": "product.compact",
                }).encode(),
            )
            self._token = resp["access_token"]
            self._token_expires = time.time() + resp.get("expires_in", 1800) - 60
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _get(self, path, params):
        url = f"{KROGER_API}/{path}?{urllib.parse.urlencode(params)}"
        return http_json(url, headers=self._auth_header()).get("data", [])

    def stores_near(self, zip_code, radius_miles, limit):
        stores = self._get("locations", {
            "filter.zipCode.near": zip_code,
            "filter.radiusInMiles": radius_miles,
            "filter.limit": limit,
        })
        return [
            {
                "store_id": s["locationId"],
                "store": s.get("name", ""),
                "chain": s.get("chain", ""),
                "address": "{addressLine1}, {city}, {state} {zipCode}".format(**s["address"]),
            }
            for s in stores
        ]

    def search_prices(self, store_id, term, limit):
        products = self._get("products", {
            "filter.term": term,
            "filter.locationId": store_id,
            "filter.limit": limit,
        })
        results = []
        for p in products:
            for item in p.get("items", []):
                price = item.get("price")
                if not price:  # not sold at this store
                    continue
                results.append({
                    "product": p.get("description", ""),
                    "brand": p.get("brand", ""),
                    "size": item.get("size", ""),
                    "regular_price": price.get("regular") or "",
                    "promo_price": price.get("promo") or "",
                })
        return results


def scan(client, zip_code, radius, max_stores, products, per_product):
    stores = client.stores_near(zip_code, radius, max_stores)
    if not stores:
        print(f"No stores found within {radius} miles of {zip_code}.", file=sys.stderr)
        return []

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for store in stores:
        print(f"\n== {store['store']} ({store['chain']}) - {store['address']}")
        for term in products:
            matches = client.search_prices(store["store_id"], term, per_product)
            if not matches:
                print(f"  {term:<22} no priced results")
            for m in matches:
                promo = f"  (sale ${m['promo_price']})" if m["promo_price"] else ""
                print(f"  {term:<22} ${m['regular_price']:<7} {m['product']} [{m['size']}]{promo}")
                rows.append({"timestamp": timestamp, **store, "search_term": term, **m})
    return rows


def append_csv(path, rows):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Scan grocery prices at stores near a ZIP code.")
    parser.add_argument("zip", help="ZIP code to search near")
    parser.add_argument("--radius", type=int, default=10, help="search radius in miles (default 10)")
    parser.add_argument("--stores", type=int, default=5, help="max number of stores (default 5)")
    parser.add_argument("--products", nargs="+", default=DEFAULT_PRODUCTS, help="search terms")
    parser.add_argument("--per-product", type=int, default=1,
                        help="matches to record per search term (default 1)")
    parser.add_argument("--out", default="prices.csv", help="CSV file to append to")
    args = parser.parse_args()

    client_id = os.environ.get("KROGER_CLIENT_ID")
    client_secret = os.environ.get("KROGER_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set KROGER_CLIENT_ID and KROGER_CLIENT_SECRET (free at https://developer.kroger.com).")

    client = KrogerClient(client_id, client_secret)
    rows = scan(client, args.zip, args.radius, args.stores, args.products, args.per_product)
    if rows:
        append_csv(args.out, rows)
        print(f"\nSaved {len(rows)} prices to {args.out}")


if __name__ == "__main__":
    main()
