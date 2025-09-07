from playwright.sync_api import sync_playwright
from playwright.async_api import async_playwright
from dotenv import load_dotenv
import os
import time
import logging
from datetime import datetime
import time
from urllib.parse import urljoin
import argparse
import pandas as pd
from google.oauth2.service_account import Credentials
import gspread

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# get some env vars.
load_dotenv()
EMAIL = os.getenv("EMAIL")
PW = os.getenv("PW")
SITE = os.getenv("SITE")
LOGIN_URL = os.getenv("LOGIN_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")          # the long ID from the sheet URL after /d/
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME")                # or whatever tab name you want
SERVICE_ACCOUNT_JSON = os.getenv("SERVICE_ACCOUNT_JSON")  # path to your service account JSON file
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # keep scopes in code
HEADERS = [
    "sku","title","description","brand","upc","upc_inner",
    "gtin_case","country","pallet_pattern","image_url",
    "price_measure","case_description","inners_description",
    "sales_per_box","boxes_per_case","stock_status_text",
    "url","category"
]

def get_main_category_links(page, SITE):
    """Extract all category names and URLs from the <nav> as a dict."""
    nav = page.locator("nav.navigation")
    if not nav.count():
        return {}

    anchors = nav.locator("a.level-top")
    categories = {}

    for a in anchors.all():
        href = (a.get_attribute("href") or "").strip()
        name = (a.inner_text() or "").strip()
        if not href or not name:
            continue

        # normalize href
        if href.startswith("http"):
            if SITE not in href:
                continue
            url = href
        else:
            url = urljoin(SITE, href)

        categories[name] = url

    return categories

def login(page, EMAIL, PW):
    page.fill("input[name='login[username]']", EMAIL)
    page.fill("input[name='login[password]']", PW)
    page.click('#send2')
    page.wait_for_load_state('networkidle', timeout=15000)

    # Check if login was successful
    current_url = page.url
    if 'account/login' not in current_url or 'account' in current_url:
        logger.info("Login appears successful")
    else:
        logger.error("Login failed - still on login page")

def get_product_links_on_page(page, SITE):
    """
    Get product SKUs and URLs from the current listing page.
    Returns a dict: {sku: absolute_url}
    """
    products = {}
    items = page.locator("ol.products li.product")

    for i in range(items.count()):
        item = items.nth(i)

        # Extract SKU text
        sku_loc = item.locator(".product.sku .sku > span")
        sku = sku_loc.first.inner_text().strip() if sku_loc.count() else None

        # Extract product link
        link_loc = item.locator("a.product-item-link")
        href = link_loc.get_attribute("href") if link_loc.count() else None

        if sku and href:
            url = href if href.startswith("http") else urljoin(SITE, href)
            products[sku] = url

    return products

def click_next(page) -> bool:
    """Go to next listing page; return False if there's no next page."""
    nxt = page.locator("a.action.next")
    if not nxt.count():
        return False
    nxt.first.click()
    page.wait_for_load_state("domcontentloaded")
    return True


def get_all_product_links(page, SITE):
    """Get all product SKUs and URLs across all listing pages."""
    all_products = {}
    page_idx = 0

    while True:
        page_idx += 1
        products = get_product_links_on_page(page, SITE)
        logger.info(f"Page {page_idx}: {len(products)} products found")
        all_products.update(products)

        if not click_next(page):
            break

    return all_products

def _txt_first(page_or_loc, selector):
    loc = page_or_loc.locator(selector)
    if not loc.count():
        return None
    t = loc.first.text_content(timeout=5000)
    return t.strip() if t else None

def _attr_first(page_or_loc, selector, attr):
    loc = page_or_loc.locator(selector)
    if not loc.count():
        return None
    v = loc.first.get_attribute(attr)
    return v.strip() if v else None

def _more_info(page):
    data = {}
    rows = page.locator("#product-attribute-specs-table tbody tr")
    for i in range(rows.count()):
        row = rows.nth(i)
        label = _txt_first(row, "th")
        value = _txt_first(row, "td")
        if label and value:
            data[label] = value
    return data

def _pack_info(page):
    """Reads the small two-row table under .product-pack-info."""
    result = {"sales_per_box": None, "boxes_per_case": None}
    tb = page.locator(".product-pack-info table tbody tr")
    for i in range(tb.count()):
        r = tb.nth(i)
        k = _txt_first(r, "th") or ""
        v = _txt_first(r, "td")
        if not v:
            continue
        if "SALES PER BOX" in k.upper():
            result["sales_per_box"] = v
        elif "BOXES PER CASE" in k.upper():
            result["boxes_per_case"] = v
    return result

def scrape_product_info(page, url):
    # Core fields
    title = _txt_first(page, "h1.page-title .base, h1[ itemprop='name' ], h1.product-title")
    sku   = _txt_first(page, ".product.attribute.sku .value, [itemprop='sku']")
    desc  = _txt_first(page, ".product.attribute.overview .value")
    image = _attr_first(page, ".gallery-placeholder img, .fotorama__stage__frame img", "src")
    if not image:
        image = _attr_first(page, "meta[property='og:image']", "content")

    # “More Information” table
    more = _more_info(page)
    brand          = more.get("Brand")
    upc            = more.get("UPC")
    upc_inner      = more.get("UPC (inner)")
    gtin_case      = more.get("GTIN (case)") or more.get("GTIN")
    country        = more.get("Country of Manufacture")
    pallet_pattern = more.get("Pallet Pattern")

    # Pricing & stock (visible after login)
    price_measure      = _txt_first(page, ".nassau.api-prices .nassau.price-measure")
    case_description   = _txt_first(page, "label[for='qty-case']")
    inners_description = _txt_first(page, "label[for='qty-inner']")  # may be None if not sellable
    stock_status_text  = _txt_first(page, ".stock-status.stock-status--ready, .stock-status.stock-status--limited, .stock-status")

    # Small pack info table above
    pack = _pack_info(page)  # sales_per_box, boxes_per_case

    return {
        "sku": sku,
        "title": title,
        "description": desc,
        "brand": brand,
        "upc": upc,
        "upc_inner": upc_inner,
        "gtin_case": gtin_case,
        "country": country,
        "pallet_pattern": pallet_pattern,
        "image_url": image,
        "price_measure": price_measure,
        "case_description": case_description,
        "inners_description": inners_description,
        "sales_per_box": pack.get("sales_per_box"),
        "boxes_per_case": pack.get("boxes_per_case"),
        "stock_status_text": stock_status_text,
        "url": url,
    }

HEADERS = [
    "sku","title","description","brand","upc","upc_inner",
    "gtin_case","country","pallet_pattern","image_url",
    "price_measure","case_description","inners_description",
    "sales_per_box","boxes_per_case","stock_status_text",
    "url","category"
]

def open_sheet():
    if not SPREADSHEET_ID:
        raise RuntimeError("SPREADSHEET_ID missing")
    if not SERVICE_ACCOUNT_JSON:
        raise RuntimeError("SERVICE_ACCOUNT_JSON missing")
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_JSON, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(WORKSHEET_NAME or "master")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME or "master", rows=200, cols=30)
    # ensure headers
    existing = ws.row_values(1)
    if not existing:
        ws.update([HEADERS], range_name="A1") 
        existing = HEADERS
    # extend headers if you add new fields later
    merged = existing[:]
    for h in HEADERS:
        if h not in merged:
            merged.append(h)
    if merged != existing:
        ws.update([merged], range_name="A1") 
    # return worksheet and a header->col map
    header_map = {h: i+1 for i, h in enumerate(ws.row_values(1))}
    return ws, header_map

def upsert_product(ws, header_map, record, key_field="sku"):
    """
    Insert or update a row keyed by SKU (fallback to URL if SKU missing).
    """
    key = (record.get(key_field) or record.get("url") or "").strip()
    key_field_eff = key_field if record.get(key_field) else "url"
    if not key:
        logger.warning("Skipping: no SKU or URL key present.")
        return "skipped"

    # find target row by key match in its column
    key_col = header_map[key_field_eff]
    col_vals = ws.col_values(key_col)  # includes header
    target_row = None
    for r, val in enumerate(col_vals[1:], start=2):
        if (val or "").strip() == key:
            target_row = r
            break

    # align row to current headers
    ordered_headers = ws.row_values(1)
    row_values = [record.get(h, "") or "" for h in ordered_headers]

    if target_row:
        ws.update([row_values], range_name=f"A{target_row}")
        return "updated"
    else:
        ws.append_row(row_values, value_input_option="USER_ENTERED")
        return "inserted"
    
def main(selected_categories=None):

    with sync_playwright() as playwright:
        # open browser and navigate to login page.
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL)

        # login
        login(page, EMAIL, PW)
        
        # open google sheet once.
        ws, header_map = open_sheet()

        # get dictionary of categories. each item: <category name>: <category site>
        categories_dict = get_main_category_links(page, SITE)

        if not categories_dict:
            logger.error(f"No categories found!")
            return

        all_results = {}

        # get categories to process.
        categories_to_process = {}
        if not selected_categories: # process all if no categories specified.
            categories_to_process = categories_dict
        else:
            categories_w_err = []
            for category_name in selected_categories:
                # check validity of specified categories; skip if invalid.
                if category_name not in categories_dict:
                    logger.error(f"Category {category_name} is not a valid category and will be skipped. Please pick from: {list(categories_dict.keys())}")
                    categories_w_err.append(category_name)
                    continue
                category_link = categories_dict[category_name]
                categories_to_process[category_name] = category_link
            if categories_w_err:
                logger.info(f"These user-specified categories were not found/ invalid: {categories_w_err}")
        
        rows = []

        try:
            # get product links for all categories selected for processing.
            for category_name, category_link in categories_to_process.items():
                category_page = context.new_page()
                category_page.goto(category_link)
                product_links = get_all_product_links(category_page, SITE)
                all_results[category_name] = product_links
                category_page.close()
            
            # visit each product page and extract details.
            for category_name, product_links in all_results.items():
                total = len(product_links)
                done = 0
                for sku, product_link in product_links.items():
                    product_page = context.new_page()
                    product_page.goto(product_link)
                    product_data = scrape_product_info(product_page, product_link)
                    if product_data:
                        product_data["category"] = category_name
                        rows.append(product_data)

                        # upsert to google sheet.
                        result = upsert_product(ws, header_map, product_data)
                        done += 1
                        logger.info(
                            f"[{done}/{total}] {product_data.get('sku') or product_data.get('url')} -> {result}"
                        )
                    product_page.close()

        finally:
            if rows:
                df = pd.DataFrame(rows)
                datestamp = datetime.now().strftime("%Y%m%d")
                folder = "backup_scrape"
                os.makedirs(folder, exist_ok=True)
                filename = os.path.join(folder, f"scraped_products_{datestamp}.xlsx")
                df.to_excel(filename, index=False)
                logger.info(f"Saved {len(df)} rows to {filename}")
            else:
                logger.info("No rows collected; nothing to save.")

        browser.close()

if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--categories", nargs="*", help="Names of categories to scrape")
    args = parser.parse_args()
    main(selected_categories=args.categories if args.categories else None)