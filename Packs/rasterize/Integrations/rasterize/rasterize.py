import demistomock as demisto  # noqa: F401
from CommonServerPython import *  # noqa: F401

import base64
import os
import pychrome
# import re
import subprocess
import tempfile
import threading
import time
import traceback
# from collections.abc import Callable
from enum import Enum
from io import BytesIO
# from pathlib import Path
from threading import Event

import numpy as np
from pdf2image import convert_from_path
from PIL import Image
from PyPDF2 import PdfReader
# from selenium import webdriver
# from pyvirtualdisplay import Display
# from selenium.common.exceptions import (InvalidArgumentException,
#                                         NoSuchElementException,
#                                         TimeoutException)

# Chrome respects proxy env params
handle_proxy()
# Make sure our python code doesn't go through a proxy when communicating with chrome webdriver
os.environ['no_proxy'] = 'localhost,127.0.0.1'
# Needed for cases that rasterize is running with non-root user (docker hardening)
os.environ['HOME'] = tempfile.gettempdir()

# TODO: pass this to the start chrome shell script
CHROME_EXE = os.getenv('CHROME_EXE', '/opt/google/chrome/google-chrome')

# TODO: decide on a return error strategy (see return error or warning method) basically "should we fail silently"
WITH_ERRORS = demisto.params().get('with_error', True)

# The default wait time before taking a screenshot
DEFAULT_WAIT_TIME = max(int(demisto.params().get('wait_time', 0)), 0)
DEFAULT_PAGE_LOAD_TIME = int(demisto.params().get('max_page_load_time', 180))

# TODO: decide if we want to reuse it in several places
DEFAULT_RETRIES_COUNT = 4
DEFAULT_RETRY_WAIT_IN_SECONDS = 1

# Consts for custom width and height
MAX_FULLSCREEN_WIDTH = 8000
MAX_FULLSCREEN_HEIGHT = 8000
DEFAULT_WIDTH, DEFAULT_HEIGHT = '600', '800'

PAGES_LIMITATION = 20

LOCAL_CHROME_URL = "http://127.0.0.1:9222"


class RasterizeType(Enum):
    PNG = 'png'
    PDF = 'pdf'
    # TODO: handle JSON and selenium functions
    JSON = 'json'


class TabLifecycleManager:
    def __init__(self, browser):
        self.browser = browser
        self.tab = None

    def __enter__(self):
        self.tab = self.browser.new_tab()
        self.tab.start()
        self.tab.Page.stopLoading()
        self.tab.Page.enable()
        return self.tab

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.tab:
            self.tab.stop()
            self.browser.close_tab(self.tab.id)


class PychromeEventHandler:
    screen_lock = threading.Lock()

    def __init__(self, browser, tab, tab_ready):
        self.browser = browser
        self.tab = tab
        self.tab_ready = tab_ready
        self.start_frame = None

    def frame_started_loading(self, frameId):
        if not self.start_frame:
            self.start_frame = frameId

    def frame_stopped_loading(self, frameId):
        demisto.debug('frame_stopped_loading')
        if self.start_frame == frameId:
            try:
                self.tab.Page.stopLoading()

                with self.screen_lock:
                    # must activate current tab
                    demisto.debug(self.browser.activate_tab(self.tab.id))
                    self.tab_ready.set()
                    demisto.debug('frame_stopped_loading, Sent tab_ready.set')
            except Exception as e:  # pragma: no cover
                demisto.error(f'Failed stop loading the page: {self.tab=}, {frameId=}, {e=}')


def get_running_chrome_processes() -> list[str]:
    try:
        processes = subprocess.check_output(['ps', 'auxww'],
                                            stderr=subprocess.STDOUT,
                                            text=True).splitlines()

        chrome_identifiers = ["chrom", "headless", "--remote-debugging-port=9222"]
        chrome_processes = [process for process in processes
                            if all(identifier in process for identifier in chrome_identifiers)]

        demisto.debug(f'Detected {len(chrome_processes)} Chrome processes running')
        return chrome_processes

    except subprocess.CalledProcessError as e:
        demisto.info(f'Error fetching process list: {e.output}')
        return []
    except Exception as e:
        demisto.info(f'Unexpected error: {e}')
        return []


def get_active_chrome_processes_count():
    try:
        return len(get_running_chrome_processes())
    except Exception as ex:
        demisto.info(f'Error getting Chrome processes: {ex}')
        return 0


def start_chrome_headless():
    try:
        subprocess.run(['bash', '/start_chrome_headless.sh'],
                       text=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        demisto.debug('Chrome headless started')
    except Exception as ex:
        demisto.info(f'Error starting Chrome headless: {ex}')


def kill_all_chrome_processes():
    try:
        chrome_processes = get_running_chrome_processes()
        for process in chrome_processes:
            pid = process.split()[1]  # Assuming second element is the PID
            subprocess.run(['kill', '-9', pid], capture_output=True, text=True)
            demisto.debug(f'Killed Chrome process with PID: {pid}')
    except Exception as ex:
        demisto.info(f'Error killing Chrome processes: {ex}')


def ensure_chrome_running():  # pragma: no cover
    for _ in range(DEFAULT_RETRIES_COUNT):
        count = get_active_chrome_processes_count()

        if count == 1:
            demisto.debug('One Chrome instance running. Returning True.')
            return True
        elif count == 0:
            start_chrome_headless()
        else:  # clean environment in case more than one browser is active
            kill_all_chrome_processes()

        time.sleep(DEFAULT_RETRY_WAIT_IN_SECONDS)  # pylint: disable=E9003

    demisto.info(f'Max retries ({DEFAULT_RETRIES_COUNT}) reached, Chrome headless is not running correctly')
    return False


def setup_tab_event(browser, tab):
    tab_ready_event = Event()
    tab_event_handler = PychromeEventHandler(browser, tab, tab_ready_event)
    tab.Page.frameStartedLoading = tab_event_handler.frame_started_loading
    tab.Page.frameStoppedLoading = tab_event_handler.frame_stopped_loading

    return tab_ready_event


def navigate_to_path(browser, tab, path, wait_time, navigation_timeout):  # pragma: no cover
    tab_ready_event = setup_tab_event(browser, tab)

    try:
        demisto.debug('Preparing tab for navigation')

        demisto.debug(f'Starting tab navigation to given path: {path}')

        if navigation_timeout > 0:
            tab.Page.navigate(url=path, _timeout=navigation_timeout)
        else:
            tab.Page.navigate(url=path)

        success_flag = tab_ready_event.wait(navigation_timeout)

        if not success_flag:
            message = f'Timeout of {navigation_timeout} seconds reached while waiting for {path}'
            demisto.error(message)
            return_error(message)

        time.sleep(wait_time)  # pylint: disable=E9003

    except Exception as ex:
        message = f'Unhandled exception: {ex} thrown while trying to navigate to {path}'
        demisto.error(message)
        return_error(message)


def screenshot_image(browser, tab, path, wait_time, timeout):  # pragma: no cover
    navigate_to_path(browser, tab, path, wait_time, timeout)
    ret_value = base64.b64decode(tab.Page.captureScreenshot()['data'])
    # TODO: should we try and kill zombie chrome processes (see 'pychrome_reap_children' in rasterize)
    return ret_value


def screenshot_pdf(browser, tab, path, wait_time, timeout, include_url):  # pragma: no cover
    navigate_to_path(browser, tab, path, wait_time, timeout)
    header_template = ''
    if include_url:
        header_template = "<span class=url></span>"
    ret_value = base64.b64decode(tab.Page.printToPDF(headerTemplate=header_template)['data'])
    # TODO: should we try and kill zombie chrome processes (see 'pychrome_reap_children' in rasterize)
    return ret_value


# TODO: support width and height
def rasterize(path: str,
              rasterize_type: RasterizeType = RasterizeType.PNG,
              wait_time: int = DEFAULT_WAIT_TIME,
              offline_mode: bool = False,
              timeout: int = DEFAULT_PAGE_LOAD_TIME,
              include_url: bool = False,
              width=DEFAULT_WIDTH,
              height=DEFAULT_HEIGHT,
              ):
    """
    Capturing a snapshot of a path (url/file), using Chrome Driver
    :param offline_mode: when set to True, will block any outgoing communication
    :param path: file path, or website url
    :param rasterize_type: result type: .png/.pdf
    :param wait_time: time in seconds to wait before taking a screenshot
    :param timeout: amount of time to wait for a page load to complete before throwing an error
    :param include_url: should the URL be included in the output image/PDF
    """

    if ensure_chrome_running():
        if offline_mode:
            # TODO: handle offline mode
            pass

        browser = pychrome.Browser(url=LOCAL_CHROME_URL)
        with TabLifecycleManager(browser) as tab:
            if rasterize_type == RasterizeType.PNG:
                return screenshot_image(browser, tab, path, wait_time=wait_time, timeout=timeout)

            if rasterize_type == RasterizeType.PDF:
                return screenshot_pdf(browser, tab, path, wait_time=wait_time, timeout=timeout, include_url=include_url)

    else:
        message = f'Could not use local Chrome for rasterize command'
        demisto.error(message)
        return_error(message)


def check_width_and_height(width: int, height: int) -> tuple[int, int]:
    """
    Verifies that the width and height are not greater than the safeguard limit.
    Args:
        width: The given width.
        height: The given height.

    Returns: The checked width and height values - [width, height]
    """
    w = min(width, MAX_FULLSCREEN_WIDTH)
    h = min(height, MAX_FULLSCREEN_HEIGHT)

    return w, h


def return_err_or_warn(msg):  # pragma: no cover
    return_error(msg) if WITH_ERRORS else return_warning(msg, exit=True)


# region CommandHandlers
def rasterize_image_command():
    args = demisto.args()
    entry_id = args.get('EntryID')
    width, height = get_common_args(demisto.args())
    width, height = check_width_and_height(width, height)  # Check that the width and height meet the safeguard limit

    file_name = args.get('file_name', entry_id)

    file_path = demisto.getFilePath(entry_id).get('path')
    file_name = f'{file_name}.pdf'

    with open(file_path, 'rb') as f:
        output = rasterize(path=f'file://{os.path.realpath(f.name)}', width=width, height=height,
                           rasterize_type=RasterizeType.PDF)
        res = fileResult(filename=file_name, data=output, file_type=entryTypes['entryInfoFile'])
        demisto.results(res)


def rasterize_email_command():  # pragma: no cover
    html_body = demisto.args().get('htmlBody')
    width, height = get_common_args(demisto.args())
    width, height = check_width_and_height(width, height)  # Check that the width and height meet the safeguard limit

    offline = demisto.args().get('offline', 'false') == 'true'
    rasterize_type = RasterizeType(demisto.args().get('type', 'png').lower())
    file_name = demisto.args().get('file_name', 'email')
    html_load = int(demisto.args().get('max_page_load_time', DEFAULT_PAGE_LOAD_TIME))

    file_name = f'{file_name}.{"pdf" if rasterize_type == RasterizeType.PDF else "png"}'  # type: ignore
    with open('htmlBody.html', 'w', encoding='utf-8-sig') as f:
        f.write(f'<html style="background:white";>{html_body}</html>')
    path = f'file://{os.path.realpath(f.name)}'

    output = rasterize(path=path, rasterize_type=rasterize_type, width=width, height=height, offline_mode=offline,
                       timeout=html_load)
    res = fileResult(filename=file_name, data=output)
    if rasterize_type == RasterizeType.PNG:
        res['Type'] = entryTypes['image']

    demisto.results(res)


def convert_pdf_to_jpeg(path: str, max_pages: str, password: str, horizontal: bool = False):
    """
    Converts a PDF file into a jpeg image
    :param path: file's path
    :param max_pages: max pages to render,
    :param password: PDF password
    :param horizontal: if True, will combine the pages horizontally
    :return: A list of stream of combined images
    """
    demisto.debug(f'Loading file at Path: {path}')
    input_pdf = PdfReader(open(path, "rb"), strict=False, password=password)
    pages = len(input_pdf.pages) if max_pages == "*" else min(int(max_pages), len(input_pdf.pages))

    with tempfile.TemporaryDirectory() as output_folder:
        demisto.debug('Converting PDF')
        convert_from_path(
            pdf_path=path,
            fmt='jpeg',
            first_page=1,
            last_page=pages,
            output_folder=output_folder,
            userpw=password,
            output_file='converted_pdf_'
        )
        demisto.debug('Converting PDF - COMPLETED')

        demisto.debug('Combining all pages')
        images = []
        for page in sorted(os.listdir(output_folder)):
            if os.path.isfile(os.path.join(output_folder, page)) and 'converted_pdf_' in page:
                images.append(Image.open(os.path.join(output_folder, page)))
        min_shape = min([(np.sum(page_.size), page_.size) for page_ in images])[1]  # get the minimal width

        # Divide the list of images into separate lists with constant length (20),
        # due to the limitation of images in jpeg format (max size ~65,000 pixels).
        # Create a list of lists (length == 20) of images to combine each list (20 images) to one image
        images_matrix = [images[i:i + PAGES_LIMITATION] for i in range(0, len(images), PAGES_LIMITATION)]

        outputs = []
        for images_list in images_matrix:
            if horizontal:
                # this line takes a ton of memory and doesnt release all of it
                imgs_comb = np.hstack([np.asarray(image.resize(min_shape)) for image in images_list])
            else:
                imgs_comb = np.vstack([np.asarray(image.resize(min_shape)) for image in images_list])

            imgs_comb = Image.fromarray(imgs_comb)
            output = BytesIO()
            imgs_comb.save(output, 'JPEG')  # type: ignore
            demisto.debug('Combining all pages - COMPLETED')
            outputs.append(output.getvalue())

        return outputs


def rasterize_pdf_command():  # pragma: no cover
    entry_id = demisto.args().get('EntryID')
    password = demisto.args().get('pdfPassword')
    max_pages = demisto.args().get('maxPages', 30)
    horizontal = demisto.args().get('horizontal', 'false') == 'true'
    file_name = demisto.args().get('file_name', 'image')

    file_path = demisto.getFilePath(entry_id).get('path')

    file_name = f'{file_name}.jpeg'  # type: ignore

    with open(file_path, 'rb') as f:
        images = convert_pdf_to_jpeg(path=os.path.realpath(f.name), max_pages=max_pages, password=password,
                                     horizontal=horizontal)
        results = []
        for image in images:
            res = fileResult(filename=file_name, data=image)
            res['Type'] = entryTypes['image']
            results.append(res)

        demisto.results(results)


def rasterize_html_command():
    args = demisto.args()
    entry_id = args.get('EntryID')
    width, height = get_common_args(demisto.args())
    width, height = check_width_and_height(width, height)  # Check that the width and height meet the safeguard limit
    rasterize_type = args.get('type', 'png')

    file_name = args.get('file_name', 'email')
    wait_time = int(args.get('wait_time', 0))

    file_name = f'{file_name}.{"pdf" if rasterize_type.lower() == "pdf" else "png"}'  # type: ignore
    file_path = demisto.getFilePath(entry_id).get('path')
    os.rename(f'./{file_path}', 'file.html')

    output = rasterize(path=f"file://{os.path.realpath('file.html')}", width=width, height=height,
                       rasterize_type=rasterize_type, wait_time=wait_time)

    res = fileResult(filename=file_name, data=output)
    if rasterize_type == 'png':
        res['Type'] = entryTypes['image']
    return_results(res)


def module_test():  # pragma: no cover
    # setting up a mock email file
    with tempfile.NamedTemporaryFile('w+') as test_file:
        test_file.write('<html><head><meta http-equiv=\"Content-Type\" content=\"text/html;charset=utf-8\">'
                        '</head><body><br>---------- TEST FILE ----------<br></body></html>')
        test_file.flush()
        file_path = f'file://{os.path.realpath(test_file.name)}'

        # rasterizing the file
        rasterize(path=file_path)

    demisto.results('ok')


def rasterize_command():  # pragma: no cover
    url = demisto.getArg('url')
    width, height = get_common_args(demisto.args())
    width, height = check_width_and_height(width, height)  # Check that the width and height meet the safeguard limit
    rasterize_type = RasterizeType(demisto.args().get('type', 'png').lower())
    wait_time = int(demisto.args().get('wait_time', 0))
    page_load = int(demisto.args().get('max_page_load_time', DEFAULT_PAGE_LOAD_TIME))
    file_name = demisto.args().get('file_name', 'url')
    include_url = argToBoolean(demisto.args().get('include_url', False))

    if not (url.startswith('http')):
        url = f'http://{url}'
    file_name = f'{file_name}.{"pdf" if rasterize_type == RasterizeType.PDF else "png"}'  # type: ignore

    output = rasterize(path=url, rasterize_type=rasterize_type, wait_time=wait_time, timeout=page_load, include_url=include_url)

    if rasterize_type == RasterizeType.JSON:
        return_results(CommandResults(raw_response=output, readable_output="Successfully rasterize url: " + url))
        return

    res = fileResult(filename=file_name, data=output)
    if rasterize_type == RasterizeType.PNG:
        res['Type'] = entryTypes['image']

    demisto.results(res)

# endregion


def get_common_args(args: dict):
    """
    Get commomn args.
    :param args: dict to get args from
    :return: width, height, rasterize mode
    """
    width = int(args.get('width', DEFAULT_WIDTH).rstrip('px'))
    height = int(args.get('height', DEFAULT_HEIGHT).rstrip('px'))
    return width, height


def main():  # pragma: no cover
    try:
        if demisto.command() == 'test-module':
            module_test()

        elif demisto.command() == 'rasterize-image':
            rasterize_image_command()

        elif demisto.command() == 'rasterize-email':
            rasterize_email_command()

        elif demisto.command() == 'rasterize-pdf':
            rasterize_pdf_command()

        elif demisto.command() == 'rasterize-html':
            rasterize_html_command()

        elif demisto.command() == 'rasterize':
            rasterize_command()

        else:
            return_error('Unrecognized command')

    except Exception as ex:
        return_err_or_warn(f'Unexpected exception: {ex}\nTrace:{traceback.format_exc()}')


if __name__ in ["__builtin__", "builtins", '__main__']:
    main()
