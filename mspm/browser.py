from __future__ import annotations

__all__ = ('download_with_browser', 'get_cookies_via_browser')

import logging
import shutil
import tempfile
import time
from pathlib import Path

from rich.console import Console

# Suppress log outputs from selenium and webdriver-manager to maintain clean terminal output
logging.getLogger('WDM').setLevel(logging.WARNING)
console = Console()

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from selenium.webdriver.firefox.service import Service as FirefoxService
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.firefox import GeckoDriverManager

    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False


def download_with_browser(download_url: str, dest_path: Path) -> bool:
    """
    Opens a browser instance to pass Cloudflare or CAPTCHA challenges
    and downloads the requested plugin file directly.
    """
    if not HAS_SELENIUM:
        console.print(
            "[red]Selenium or Webdriver is not installed. Install via: pip install selenium webdriver-manager[/]"
        )
        return False

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        console.print("[yellow]Launching browser for Cloudflare verification...[/]")
        driver = None
        try:
            try:
                chrome_options = ChromeOptions()
                prefs = {
                    "download.default_directory": str(temp_path),
                    "download.prompt_for_download": False,
                    "download.directory_upgrade": True,
                    "safebrowsing.enabled": True
                }
                chrome_options.add_experimental_option("prefs", prefs)
                chrome_options.add_argument("--disable-blink-features=AutomationControlled")
                service = ChromeService(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=chrome_options)
            except Exception as e:
                console.print(f"[dim]Chrome initialization failed ({e}). Attempting Firefox fallback...[/]")
                ff_options = FirefoxOptions()
                ff_options.set_preference("browser.download.folderList", 2)
                ff_options.set_preference("browser.download.dir", str(temp_path))
                ff_options.set_preference(
                    "browser.helperApps.neverAsk.saveToDisk",
                    "application/java-archive, application/x-java-archive, application/octet-stream"
                )
                service = FirefoxService(GeckoDriverManager().install())
                driver = webdriver.Firefox(service=service, options=ff_options)

            driver.get("https://www.spigotmc.org/")
            console.print("[bold green]Browser opened successfully.[/] Solve the CAPTCHA if prompted.")
            console.print("Waiting for verification (timeout: 60s)...")

            max_wait = 60
            start_time = time.time()
            while "Just a moment" in driver.title or "Cloudflare" in driver.title:
                time.sleep(1)
                if time.time() - start_time > max_wait:
                    console.print("[red]Verification timeout exceeded.[/]")
                    driver.quit()
                    return False

            console.print("[green]Verification passed. Initiating download...[/]")
            driver.get(download_url)

            download_wait_start = time.time()
            while True:
                files = list(temp_path.glob('*.jar'))
                tmp_files = list(temp_path.glob('*.crdownload'))
                if files:
                    latest_file = files[0]
                    last_size = latest_file.stat().st_size
                    time.sleep(1)
                    if 0 < last_size == latest_file.stat().st_size:
                        console.print(f"[green]Download completed: {latest_file.name}[/]")
                        shutil.move(latest_file, dest_path)
                        driver.quit()
                        return True
                if not tmp_files and time.time() - download_wait_start > 10 and not files:
                    console.print("[red]Download failed to start.[/]")
                    break
                if time.time() - download_wait_start > 120:
                    console.print("[red]Download timeout exceeded.[/]")
                    break
                time.sleep(1)
        except Exception as e:
            console.print(f"[red]Browser operation failed: {e}[/]")
        finally:
            if driver:
                driver.quit()
    return False


def get_cookies_via_browser(url: str):
    """
    Launches a browser instance to retrieve active Cloudflare session cookies and
    the corresponding User-Agent header string.
    """
    if not HAS_SELENIUM:
        console.print(
            "[red]Selenium or Webdriver is not installed. Install via: pip install selenium webdriver-manager[/]"
        )
        return {}, None
    driver = None
    try:
        try:
            chrome_options = ChromeOptions()
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as e:
            console.print(f"[dim]Chrome initialization failed ({e}). Attempting Firefox fallback...[/]")
            ff_options = FirefoxOptions()
            service = FirefoxService(GeckoDriverManager().install())
            driver = webdriver.Firefox(service=service, options=ff_options)

        driver.get(url)
        console.print("[bold green]Browser opened successfully.[/] Solve the CAPTCHA if prompted.")

        max_wait = 60
        start_time = time.time()
        while "Just a moment" in driver.title or "Cloudflare" in driver.title:
            time.sleep(1)
            if time.time() - start_time > max_wait:
                console.print("[red]Verification timeout exceeded.[/]")
                return {}, None

        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        ua = driver.execute_script("return navigator.userAgent")
        return cookies, ua
    except Exception as e:
        console.print(f"[red]Failed to retrieve cookies: {e}[/]")
        return {}, None
    finally:
        if driver:
            driver.quit()
