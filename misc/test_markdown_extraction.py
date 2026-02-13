#!/usr/bin/env python3
"""
Test script to compare get_text_from_soup vs get_markdown_from_soup
"""

import asyncio
import hashlib
from pathlib import Path
from bs4 import BeautifulSoup
import copy

# Import our utility functions and scraper
import sys

if not __package__:
    sys.path.append(str(Path(__file__).parent.parent / "src"))

from agent_web_context.utils import (
    get_text_from_soup,
    get_markdown_from_soup,
    clean_soup,
    replace_images_with_alt_text,
    get_relevant_images,
    extract_title,
)
from agent_web_context.scraper import Scraper


async def fetch_html_with_scraper(url: str) -> str:
    """Fetch HTML from URL using the Scraper with caching to test_data"""
    # Create test_data directory if it doesn't exist
    test_data_dir = Path("test_data")
    test_data_dir.mkdir(exist_ok=True, parents=True)

    # Create cache filename based on URL hash
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cache_file = test_data_dir / f"cached_html_{url_hash}.html"

    # Check if cached HTML exists
    if cache_file.exists():
        print(f"Loading cached HTML from {cache_file}")
        html_content = cache_file.read_text(encoding="utf-8")
        print(f"Loaded cached HTML content ({len(html_content)} characters)")
        return html_content

    # Fetch HTML using scraper if not cached
    print(f"Fetching HTML from {url} using Scraper")
    scraper = Scraper(url)
    html_content, _, _ = await scraper.scrape_async(output_format="html")

    # Cache the HTML content
    cache_file.write_text(html_content, encoding="utf-8")
    print(f"Cached HTML to {cache_file}")
    print(f"Fetched HTML content ({len(html_content)} characters)")
    return html_content


async def main():
    url = "https://books.toscrape.com/"
    test_dir = Path("test_data")

    # Fetch HTML using scraper
    html_content = await fetch_html_with_scraper(url)

    # Parse with BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    title = extract_title(soup)
    cleaned_soup = clean_soup(soup)
    get_relevant_images(cleaned_soup, url, title, decompose_irrelevant=True)

    # Extract text using both methods
    print("\n" + "=" * 50)
    print("EXTRACTING TEXT WITH ORIGINAL METHOD")
    print("=" * 50)

    original_text = get_text_from_soup(cleaned_soup)

    print("\n" + "=" * 50)
    print("EXTRACTING TEXT WITH MARKDOWN METHOD")
    print("=" * 50)

    markdown_text = get_markdown_from_soup(cleaned_soup)

    # Save results to files for comparison
    output_dir = test_dir / "output"
    output_dir.mkdir(exist_ok=True, parents=True)

    original_file = output_dir / "original_text.txt"
    markdown_file = output_dir / "markdown_text.md"

    original_file.write_text(original_text, encoding="utf-8")
    markdown_file.write_text(markdown_text, encoding="utf-8")

    print(f"\n✅ Results saved:")
    print(f"   Original text: {original_file}")
    print(f"   Markdown text: {markdown_file}")

    # Show comparison stats
    print(f"\n📊 Comparison:")
    print(f"   Original text length: {len(original_text)} chars")
    print(f"   Markdown text length: {len(markdown_text)} chars")


if __name__ == "__main__":
    asyncio.run(main())
