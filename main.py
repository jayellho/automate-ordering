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

def main(selected_categories=None):

    with sync_playwright() as playwright:
        # open browser and navigate to login page.
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(LOGIN_URL)

        # login
        login(page, EMAIL, PW)
                
        # get dictionary of categories. each item: <category name>: <category site>
        categories_dict = get_main_category_links(page, SITE)

        if not categories_dict:
            logger.error(f"No categories found!")
            return

        # visit categories
        ## process all categories if selected categories not specified.
        all_results = {}
        if not selected_categories:
            for category_name, category_link in categories_dict.items():
                category_page = browser.new_page()
                category_page.goto(category_link)
                product_links = get_all_product_links(category_page, SITE)
                all_results[category_name] = product_links
                category_page.close()
        else:
            for category_name in selected_categories:
                if category_name not in categories_dict:
                    logger.error(f"Category {category_name} is not a valid category and will be skipped. Please pick from: {list(categories_dict.keys())}")
                    continue
                category_link = categories_dict[category_name]
                category_page = browser.new_page()
                category_page.goto(category_link)
                product_links = get_all_product_links(category_page, SITE)
                all_results[category_name] = product_links
                category_page.close()

        print(all_results)
        browser.close()



if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--categories", nargs="*", help="Names of categories to scrape")
    args = parser.parse_args()
    main(selected_categories=args.categories if args.categories else None)