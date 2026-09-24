import logging
import re
from html import unescape
from bs4 import BeautifulSoup
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import os

# opendataloader writes the html_page_separator HTML-escaped as plain text
# (e.g. &lt;div data-type='page' ...&gt;&lt;/div&gt;). Match those strings so we
# can turn them back into real <div> elements before nesting the page content.
PAGE_SEPARATOR_RE = re.compile(
    r"^\s*<div[^>]*data-type=[\"']page[\"'][^>]*>\s*</div>\s*$",
    re.IGNORECASE,
)


def _promote_page_separators(soup) -> None:
    """Replace escaped page-separator text nodes with real page div elements."""
    for text in soup.body.find_all(string=True):
        if not text.strip() or not PAGE_SEPARATOR_RE.match(text):
            continue
        real_div = BeautifulSoup(unescape(text.strip()), "html.parser").div
        if real_div is not None:
            text.replace_with(real_div)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    retry=retry_if_exception_type(RuntimeError),
    before_sleep=lambda retry_state: logging.warning(
        f"Attempt {retry_state.attempt_number} failed — retrying in {retry_state.next_action.sleep:.1f}s"
    )
)
def format_html(file_path: Path, output_path: Path) -> Path:
    """
    Restructures an HTML file so that all content between page-divider elements
    is nested inside its corresponding page div.

    The PDF converter (opendataloader) writes the html_page_separator escaped as
    plain text, so this first promotes those escaped strings back into empty
    <div data-type="page"> elements acting as page separators, with content
    sitting as siblings after each one. Then it moves that sibling content
    inside the preceding page div.

    Args:
        file_path:   Path to the source HTML file.
        output_path: Path where the restructured HTML will be written.
                     Output contains no <html>, <head>, or <body> tags.
    """    
    
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Input path is not a file: {file_path}")
    if not os.access(file_path, os.R_OK):
        raise PermissionError(f"Input file is not readable: {file_path}")
    if not output_path.parent.exists():
        raise ValueError(f"Output directory does not exist: {output_path.parent}")
    if not os.access(output_path.parent, os.W_OK):
        raise PermissionError(f"Output directory is not writable: {output_path.parent}")
    
    soup = BeautifulSoup(file_path.read_text(encoding='utf-8'), 'html.parser')

    # The converter escapes the separator strings, so promote them back to
    # real <div data-type="page"> elements before splitting the content.
    _promote_page_separators(soup)

    for div_page in soup.body.find_all('div', attrs={'data-type': 'page'}):
        # Start from the first sibling after the page div
        brother = div_page.next_sibling
        while brother:
            # Stop when the next page separator is reached
            if brother.name == 'div' and brother.get('data-type') == 'page':
                break
            
            # Save next sibling before appending, since append() detaches the
            # node from its current position, making next_sibling unreliable
            next_ = brother.next_sibling
            div_page.append(brother)
            brother = next_
            
    # decode_contents() serializes only the inner HTML of <body>,
    # stripping the <html>, <head>, and <body> wrapper tags
    output_path.write_text(soup.body.decode_contents(), encoding='utf-8')
    
    return output_path
    