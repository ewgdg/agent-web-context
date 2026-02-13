import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from patchright.async_api import Page

# Add the src directory to the path so we can import the scraper
if not __package__:
    sys.path.append(str(Path(__file__).parent.parent / "src"))

from agent_web_context.scraper import Scraper


async def main() -> None:
    async def get_browserscan_bot_detection_results(page: Page) -> str:
        try:
            # Wait for the results to load
            await page.wait_for_selector("text=Test Results:", timeout=10000)
            await asyncio.sleep(1)
            # Get the result text - this is simplified as Playwright API is different
            result_element = await page.query_selector("text=Test Results:")
            if result_element:
                # Prefer the element's own textContent. If empty, try the parent's last child.
                # Use evaluate to access parentElement/lastElementChild in a single DOM call.
                text = await result_element.evaluate(
                    "el => {"
                    "  const p = el.parentElement;"
                    "  if (!p) return null;"
                    "  const last = p.lastElementChild;"
                    "  return last && last.textContent ? last.textContent.trim() : null;"
                    "}"
                )
                return text or "Unknown"
            return "Results not found"
        except Exception as e:
            print(f"Error getting results: {e}")
            return "Error getting results"

    print("Patchright Docker demo")

    print("Starting browser...")
    scraper = Scraper()
    async with scraper.get_context(headless=False) as context:
        print("Browser successfully started!")

        print("Visiting https://www.browserscan.net/bot-detection")
        page = await context.new_page()
        await page.goto("https://www.browserscan.net/bot-detection")

        print("Getting test results...\n")
        result = await get_browserscan_bot_detection_results(page)
        if result == "Normal":
            print(f"Test passed! Result: {result}")
        else:
            print(
                f"Test failed! ({result=}) Check browser window with VNC viewer to see what happened."
            )

        # Ensure logs directory exists and use absolute path
        logs_dir = Path("./logs")
        logs_dir.mkdir(exist_ok=True)
        screenshot_path = logs_dir / "screenshot.png"
        await page.screenshot(path=str(screenshot_path))

        print(
            (
                "\nDemo complete.\n"
                "- Try using a VNC viewer to visit the Docker container's built-in VNC server at http://localhost:5910.\n"
                "- VNC allows for easy debugging and inspection of the browser window.\n"
                "- For some tasks which may not be fully possible to automate, it can also be used to manually interact with the browser.\n\n"
                "When you are done, press Ctrl+C to exit the demo."
            )
        )

        try:
            await asyncio.Future()  # wait forever
        finally:
            # Close the page before releasing the browser
            if page:
                await page.close()

    await scraper.cleanup_on_exit()


if __name__ == "__main__":
    asyncio.run(main())
