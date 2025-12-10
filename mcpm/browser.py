# mcpm/browser.py
import time
import tempfile
import shutil
from pathlib import Path
from rich.console import Console
import logging

# Отключаем лишний шум от selenium/webdriver-manager
logging.getLogger('WDM').setLevel(logging.WARNING)

console = Console()

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.firefox.service import Service as FirefoxService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.firefox import GeckoDriverManager

    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False


def download_with_browser(download_url: str, dest_path: Path):
    """
    Открывает браузер для прохождения CAPTCHA и скачивает файл напрямую.
    """
    if not HAS_SELENIUM:
        console.print("[red]Selenium/Webdriver not installed. Run: pip install selenium webdriver-manager[/]")
        return False

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        console.print("[yellow]Launching browser for Cloudflare check...[/]")

        driver = None
        try:
            # --- Попытка Chrome ---
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
            except Exception:
                console.print("[dim]Chrome not found or failed, trying Firefox...[/]")
                # --- Попытка Firefox ---
                ff_options = FirefoxOptions()
                ff_options.set_preference("browser.download.folderList", 2)
                ff_options.set_preference("browser.download.dir", str(temp_path))
                ff_options.set_preference("browser.helperApps.neverAsk.saveToDisk",
                                          "application/java-archive, application/x-java-archive, application/octet-stream")

                service = FirefoxService(GeckoDriverManager().install())
                driver = webdriver.Firefox(service=service, options=ff_options)

            # Сначала идем на главную страницу, чтобы пройти капчу там, это надежнее
            driver.get("https://www.spigotmc.org/")

            console.print("[bold green]Browser opened![/] Please solve any CAPTCHA if it appears.")
            console.print("Waiting for you to pass the check (max 60s)...")

            # Простой цикл ожидания, пока пользователь не пройдет проверку
            max_wait = 60
            start_time = time.time()
            while "Just a moment" in driver.title or "Cloudflare" in driver.title:
                time.sleep(1)
                if time.time() - start_time > max_wait:
                    console.print("[red]Timeout waiting for CAPTCHA.[/]")
                    driver.quit()
                    return False

            console.print("[green]Cloudflare passed! Initiating download...[/]")
            driver.get(download_url)

            # Ждем завершения скачивания
            download_wait_start = time.time()
            while True:
                # Ищем .jar или временный .crdownload файл
                files = list(temp_path.glob('*.jar'))
                tmp_files = list(temp_path.glob('*.crdownload'))

                if files:
                    # Проверяем, что размер файла не меняется, значит скачивание завершено
                    latest_file = files[0]
                    last_size = latest_file.stat().st_size
                    time.sleep(1)
                    if last_size > 0 and latest_file.stat().st_size == last_size:
                        console.print(f"[green]Download complete: {latest_file.name}[/]")
                        shutil.move(latest_file, dest_path)
                        driver.quit()
                        return True

                if not tmp_files and time.time() - download_wait_start > 10 and not files:
                    console.print("[red]Download did not start or failed.[/]")
                    break

                if time.time() - download_wait_start > 120:  # 2 минуты таймаут на скачивание
                    console.print("[red]Download timeout.[/]")
                    break
                time.sleep(1)

        except Exception as e:
            console.print(f"[red]Browser operation failed: {e}[/]")
        finally:
            if driver:
                driver.quit()

    return False