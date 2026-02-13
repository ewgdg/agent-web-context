import logging
import re
from typing import Any, cast
import bs4
from bs4 import BeautifulSoup
import difflib
from urllib.parse import urljoin, urlparse
from markdownify import MarkdownConverter


def text_similarity(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def get_relevant_images(
    soup: BeautifulSoup,
    url: str,
    page_title: str,
    min_relevance_score: float = 2.0,
    decompose_irrelevant: bool = False,
) -> list[dict[str, Any]]:
    """Extract relevant images from the page

    Args:
        soup: BeautifulSoup object of the page
        url: Base URL for resolving relative image paths
        page_title: Page title for alt text similarity scoring
        min_relevance_score: Minimum score threshold to include images (default: 2.0)
        decompose_irrelevant: Whether to remove irrelevant images from soup object (default: False)

    Returns:
        List of image dictionaries with url, score, and desc fields
    """
    image_info_list: list[tuple[bs4.Tag, dict]] = []

    relevant_classes = set(
        (
            "header",
            "featured",
            "hero",
            "thumbnail",
            "main",
            "content",
        )
    )

    def parse_dimension(value: str) -> float:
        """Parse dimension value, handling px units"""
        if value.lower().endswith("px"):
            value = value[:-2]  # Remove 'px' suffix
        try:
            return float(value)  # Convert to float first to handle decimal values
        except ValueError as e:
            print(f"Error parsing dimension value {value}: {e}")
            return 0

    try:
        # Find all img tags with src attribute
        all_images = soup.find_all("img", src=True)
        seen = set()
        for img in all_images:
            if isinstance(img, bs4.Tag):
                img_src = img.get("src", None)
            else:
                continue
            if img_src is None:
                continue
            img_src = str(img_src)
            # urljoin will handle the case when img_src is is_absolute_url
            img_src = urljoin(url, img_src)
            if not img_src.startswith(("http://", "https://")):
                continue

            if img_src in seen:
                continue
            seen.add(img_src)

            score = 0
            # Check for relevant classes

            img_classes = cast(list[str], img.get("class") or [])

            if any(img_cls in relevant_classes for img_cls in img_classes):
                score += 2  # Higher score

            # Check for relevant alt text
            alt_text = ""
            if img.get("alt"):
                alt_text = img["alt"]
                similarity = text_similarity(alt_text, page_title)
                score += 5 * similarity

            # Check for size attributes
            if img.get("width") and img.get("height"):
                width = parse_dimension(str(img["width"]))
                height = parse_dimension(str(img["height"]))
                if width and height:
                    if width >= 2000 and height >= 1000:
                        score += 3  # Medium score (very large images)
                    elif width >= 1600 or height >= 800:
                        score += 2  # Lower score
                    elif width >= 800 or height >= 500:
                        score += 1  # Lowest score
                    elif width >= 500 or height >= 300:
                        score += 0  # Lowest score
                    else:
                        continue  # Skip small images

            if score < min_relevance_score:
                continue

            image_info_list.append(
                (img, {"url": img_src, "score": score, "desc": alt_text})
            )

        # Sort images by score (highest first)
        sorted_images = sorted(
            image_info_list, key=lambda x: x[1]["score"], reverse=True
        )
        sorted_images = sorted_images[:5]  # Return top 5 images

        image_tags_to_keep = {img_info[0] for img_info in sorted_images}
        # Decompose irrelevant images from soup if requested
        if decompose_irrelevant:
            for img in all_images:
                if img not in image_tags_to_keep:
                    img.decompose()

        return [img_info[1] for img_info in sorted_images]

    except Exception as e:
        logging.error(f"Error in get_relevant_images: {e}")
        return []


def clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    """Clean the soup by removing unwanted tags"""
    for tag in soup.find_all(
        [
            "script",
            "style",
            "footer",
            "header",
            "nav",
            "menu",
            "sidebar",
            "svg",
            "button",
        ]
    ):
        tag.decompose()

    disallowed_class_set = {"nav", "menu", "sidebar", "footer"}

    # clean tags with certain classes
    def does_tag_have_disallowed_class(elem) -> bool:
        if not isinstance(elem, bs4.Tag):
            return False

        return any(
            cls_name in disallowed_class_set for cls_name in (elem.get("class") or [])
        )

    # for tag in soup.find_all(does_tag_have_disallowed_class):
    #     tag.decompose()

    return soup


def extract_title(soup: BeautifulSoup) -> str:
    """Extract the title from the BeautifulSoup object"""
    title = soup.title
    if title is None:
        title = soup.find("h1")

    if title is None:
        return ""
    else:
        return title.text


def replace_images_with_alt_text(soup: BeautifulSoup) -> BeautifulSoup:
    """Replace img tags with their alt text for better LLM processing"""
    try:
        for img in soup.find_all("img"):
            if isinstance(img, bs4.Tag):
                alt_text = img.get("alt", "")
                if isinstance(alt_text, str):
                    alt_text = alt_text.strip()
                    if alt_text:
                        # Create a new text node to replace the img tag
                        replacement_text = soup.new_string(f"[Image: {alt_text}]")
                        img.replace_with(replacement_text)
                    else:
                        # Remove img if no alt text
                        img.decompose()
                else:
                    # Remove img if alt is not a string
                    img.decompose()
        return soup
    except Exception as e:
        logging.error(f"Error replacing images with alt text: {e}")
        return soup


def get_text_from_soup(soup: BeautifulSoup) -> str:
    """Get the relevant text from the soup with improved filtering"""
    text = soup.get_text(strip=True, separator="\n")
    # Remove excess whitespace
    text = re.sub(r"\s{2,}", " ", text)
    return text


def get_markdown_from_soup(soup: BeautifulSoup, strip_img=False) -> str:
    """Convert BeautifulSoup to markdown using MarkdownConverter with content cleaning"""
    # Strip navigation, ads, and other unwanted elements
    strip = [
        "nav",
        "header",
        "footer",
        "aside",
        "script",
        "style",
        "svg",
        "iframe",
        "form",
        "button",
        # strip links
        "a",
    ]
    if strip_img:
        strip.append("img")
    try:
        converter = MarkdownConverter(
            strip=strip,
            # Clean heading style
            heading_style="ATX",  # Use # ## ### instead of underlines
            escape_asterisks=False,
            escape_underscores=False,
        )

        markdown_content = converter.convert_soup(soup)

        # Clean up excessive blank lines - limit to max one blank line between content
        markdown_content = re.sub(r"(?:\s*\n\s*){3,}", "\n\n", markdown_content)

        return markdown_content.strip()
    except Exception as e:
        logging.error(f"Error converting HTML to markdown: {e}")
        return get_text_from_soup(soup)
