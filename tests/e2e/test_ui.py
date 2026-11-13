"""Browser end-to-end tests. Run: .venv/bin/python -m pytest -m e2e tests/e2e"""
import re

import pytest

from tests.e2e.conftest import BROWSERS, LiveServer

pytestmark = pytest.mark.e2e

CHROMIUM_ONLY = pytest.mark.parametrize("browser", ["chromium"])
ALL = pytest.mark.parametrize("browser", BROWSERS)


def answer_text(p):
    return p.page.inner_text("#a-caption")


# ----------------------------------------------------------------------------- load
@ALL
def test_loads_cleanly_with_fonts_and_chips(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    assert "Yash Rajput" in page.title()
    assert page.locator(".chip").count() == 8
    assert page.inner_text(".talk-label") == "Tap to talk"
    assert page.evaluate("document.fonts.ready.then(() => document.fonts.check('500 1em Newsreader') && document.fonts.check('1em \"Instrument Sans\"'))")
    assert p.errors == []


@ALL
def test_reduced_motion_and_dark_mode_load_cleanly(make_page, browser):
    for kw in ({"reduced_motion": "reduce"}, {"color_scheme": "dark"}):
        p = make_page(browser, **kw)
        p.goto()
        assert p.errors == []


# ----------------------------------------------------------------------------- text flow
@ALL
def test_chip_answers_and_follow_up_carries_history(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    page.locator(".chip").nth(0).click()
    page.wait_for_selector("#turn-a:not([hidden])")
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)
    assert "life story" in page.inner_text("#q-caption")
    p.wait_state("idle", 30000)
    page.locator(".chip").nth(1).click()
    page.wait_for_function("document.querySelector('#q-caption').textContent.includes('superpower')")
    bodies = p.ask_bodies()
    assert bodies[0]["history"] == []
    assert [m["role"] for m in bodies[1]["history"]] == ["user", "assistant"]
    assert "life story" in bodies[1]["history"][0]["content"]
    assert p.errors == []


@ALL
def test_typed_question_and_empty_submit(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    page.fill("#text-input", "   ")
    page.press("#text-input", "Enter")
    page.wait_for_timeout(300)
    assert p.ask_bodies() == []
    page.fill("#text-input", "Why 100x?")
    page.press("#text-input", "Enter")
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('Why 100x')", timeout=15000)
    assert page.input_value("#text-input") == ""


# ----------------------------------------------------------------------------- voice (Chromium has a fake mic)
@CHROMIUM_ONLY
def test_voice_question_end_to_end_and_voice_line_reacts(make_page, browser):
    p = make_page(browser)
    page = p.goto()

    def extent():  # tallest coloured column of the voice line, in px
        return page.evaluate("""() => {
          const c = document.getElementById('voice-line'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height);
          let top = c.height, bottom = 0;
          for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x += 2) if (d.data[(y * c.width + x) * 4 + 3] > 40) { top = Math.min(top, y); bottom = Math.max(bottom, y); }
          return Math.max(0, bottom - top) / (window.devicePixelRatio || 1);
        }""")

    idle = extent()
    page.click("#talk")
    p.wait_state("recording", 8000)
    assert page.inner_text(".talk-label") == "Tap when you're done"
    page.wait_for_timeout(1200)
    assert extent() > idle * 3 + 10, "voice line should react to the microphone"
    page.click("#talk")
    page.wait_for_function("document.querySelector('#q-caption').textContent.includes('number one superpower')", timeout=10000)
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)
    p.wait_state("idle", 30000)
    assert any(r.url.endswith("/api/transcribe") for r in p.requests)
    assert p.errors == []


@CHROMIUM_ONLY
def test_too_short_recording_is_ignored(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    page.click("#talk")
    p.wait_state("recording", 8000)
    page.click("#talk")  # stops within ~100 ms
    p.wait_state("idle", 5000)
    assert not any(r.url.endswith("/api/transcribe") for r in p.requests)


@CHROMIUM_ONLY
def test_nothing_heard_guides_to_typing(make_page, server, browser):
    server.behaviour.stt_none = True
    p = make_page(browser)
    page = p.goto()
    page.click("#talk")
    p.wait_state("recording", 8000)
    page.wait_for_timeout(1000)
    page.click("#talk")
    p.wait_state("error", 10000)
    assert "type your question" in page.inner_text("#status").lower()
    assert page.evaluate("document.activeElement.id") == "text-input"


@CHROMIUM_ONLY
def test_space_bar_toggles_recording_but_not_while_typing(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    page.focus("#text-input")
    page.keyboard.type("a b")
    assert p.state() == "idle" and page.input_value("#text-input") == "a b"
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("Space")
    p.wait_state("recording", 8000)
    page.wait_for_timeout(600)
    page.keyboard.press("Space")
    # the fake speech-to-text is instant, so "transcribing" can flash past; just require recording to end
    page.wait_for_function("document.querySelector('#talk').dataset.state !== 'recording'", timeout=5000)


# ----------------------------------------------------------------------------- stop / interrupt
@ALL
def test_stop_button_silences_the_answer(make_page, server, browser):
    server.behaviour.tts_delay = 0.2
    p = make_page(browser)
    page = p.goto()
    page.locator(".chip").nth(0).click()
    page.wait_for_selector("#turn-a:not([hidden])")
    page.wait_for_selector("#stop:not([hidden])")
    page.click("#stop")
    p.wait_state("idle", 3000)
    page.wait_for_timeout(1500)
    assert p.state() == "idle"
    assert page.is_hidden("#stop")


@ALL
def test_new_question_interrupts_the_old_answer(make_page, server, browser):
    server.behaviour.token_delay = 0.05
    p = make_page(browser)
    page = p.goto()
    page.locator(".chip").nth(0).click()
    page.wait_for_function("document.querySelector('#a-caption').textContent.length > 10")
    page.locator(".chip").nth(1).click()
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=30000)
    text = answer_text(p)
    assert "superpower" in text and "life story" not in text, text
    assert len(p.ask_bodies()) == 2
    # the first answer must not have been added to the conversation history
    p.wait_state("idle", 30000)
    page.locator(".chip").nth(2).click()
    page.wait_for_function("document.querySelector('#q-caption').textContent.includes('grow')")
    assert [m["role"] for m in p.ask_bodies()[2]["history"]] == ["user", "assistant"]
    assert "superpower" in p.ask_bodies()[2]["history"][0]["content"]


# ----------------------------------------------------------------------------- failures
@ALL
def test_llm_outage_shows_message_and_recovers(make_page, server, browser):
    server.behaviour.fail_llm = True
    p = make_page(browser)
    page = p.goto()
    page.locator(".chip").nth(0).click()
    page.wait_for_selector("#toast:not([hidden])", timeout=15000)
    assert "unavailable" in page.inner_text("#toast").lower()
    p.wait_state("idle", 20000)
    server.behaviour.fail_llm = False
    page.locator(".chip").nth(1).click()
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)


@ALL
def test_rate_limit_shows_friendly_message(make_page, browser):
    srv = LiveServer(rate_limit_ask="1/minute")
    try:
        p = make_page(browser, srv=srv)
        page = p.goto()
        page.locator(".chip").nth(0).click()
        p.wait_state("idle", 30000)
        page.locator(".chip").nth(1).click()
        p.wait_state("error", 10000)
        assert "little fast" in page.inner_text("#status")
    finally:
        srv.stop()


@ALL
def test_dropped_stream_offers_retry(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    partial = 'event: meta\ndata: {"request_id": "r_x"}\n\nevent: text\ndata: {"delta": "Half an answer "}\n\n'
    page.route("**/api/ask", lambda route: route.fulfill(status=200, content_type="text/event-stream", body=partial))
    page.locator(".chip").nth(0).click()
    page.wait_for_selector("#retry:not([hidden])", timeout=10000)
    assert "Half an answer" in answer_text(p)
    page.unroute("**/api/ask")
    page.click("#retry")
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)
    assert page.is_hidden("#retry")


@ALL
def test_mic_denied_falls_back_to_typing(make_page, browser):
    p = make_page(browser)
    p.page.add_init_script("Object.defineProperty(MediaDevices.prototype, 'getUserMedia', {configurable: true, value: () => Promise.reject(new DOMException('denied', 'NotAllowedError'))})")
    page = p.goto()
    page.click("#talk")
    p.wait_state("error", 5000)
    assert "type your question" in page.inner_text("#status").lower()
    assert page.is_visible("#inapp-hint")
    assert page.evaluate("document.activeElement.id") == "text-input"
    page.fill("#text-input", "Hello there")
    page.press("#text-input", "Enter")
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('Hello there')", timeout=15000)


@ALL
def test_pending_mic_permission_prompt_is_explained(make_page, browser):
    p = make_page(browser)
    p.page.add_init_script("Object.defineProperty(MediaDevices.prototype, 'getUserMedia', {configurable: true, value: () => new Promise(() => {})})")
    page = p.goto()
    page.click("#talk")
    page.wait_for_function("document.querySelector('.talk-label').textContent.includes('Waiting for microphone')", timeout=5000)
    assert "Allow" in page.inner_text("#status")
    # the page stays usable while the browser prompt is open
    page.locator(".chip").nth(0).click()
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)


@ALL
def test_no_media_recorder_still_usable_by_text(make_page, browser):
    p = make_page(browser)
    p.page.add_init_script("delete window.MediaRecorder")
    page = p.goto()
    assert "Type your question" in page.inner_text("#status")
    page.locator(".chip").nth(3).click()
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)


@CHROMIUM_ONLY
def test_in_app_browser_gets_a_hint(make_page, browser):
    p = make_page(browser, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile WhatsApp/2.24")
    page = p.goto()
    assert page.is_visible("#inapp-hint")


# ----------------------------------------------------------------------------- history and plain-language UI
def ask_chip(p, n):
    page = p.page
    page.locator(".chip").nth(n).click()
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)
    p.wait_state("idle", 30000)


@ALL
def test_no_technical_details_on_the_page(make_page, browser):
    p = make_page(browser)
    page = p.goto("?debug=1")  # even with the old debug switch, nothing technical is shown
    ask_chip(p, 0)
    text = page.inner_text("body").lower()
    for word in ("request_id", "request id", "stt_ms", "first_audio", "bedrock", "polly", "whisper", "groq", "latency"):
        assert word not in text, word
    assert page.locator("#debug").count() == 0


def open_history(page):
    page.click("#history-open")
    page.wait_for_selector("#history-drawer[open]")


def close_history(page):
    page.click("#history-close")
    page.wait_for_selector("#history-drawer:not([open])", state="attached")


@ALL
def test_history_opens_from_the_top_bar_and_lists_every_exchange_newest_first(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    assert page.is_visible("#history-open") and page.is_hidden("#history-drawer")
    assert page.is_hidden("#history-count")
    open_history(page)
    assert "20 messages" in page.inner_text("#history-empty")
    assert page.is_hidden("#history-clear")
    close_history(page)
    ask_chip(p, 0)
    assert page.inner_text("#history-count") == "1"
    ask_chip(p, 1)
    ask_chip(p, 2)
    assert page.inner_text("#history-count") == "3"
    open_history(page)
    items = page.locator("#history-list .h-item")
    assert items.count() == 3
    assert "top three areas" in items.nth(0).inner_text() and "life story" in items.nth(2).inner_text()
    page.keyboard.press("Escape")
    page.wait_for_selector("#history-drawer:not([open])", state="attached")
    page.reload()
    page.wait_for_function("document.querySelectorAll('.chip').length > 0")
    assert page.inner_text("#history-count") == "3"
    assert p.errors == []


@ALL
def test_history_drawer_closes_on_backdrop_click_and_space_does_not_record(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    open_history(page)
    page.keyboard.press("Space")
    page.wait_for_timeout(300)
    assert p.state() == "idle"
    page.mouse.click(20, 400)  # the dimmed area left of the panel
    page.wait_for_selector("#history-drawer:not([open])", state="attached")


@ALL
def test_history_keeps_only_the_last_20_messages_and_can_be_cleared(make_page, browser):
    import json
    seeded = []
    for i in range(10):
        seeded += [{"role": "user", "text": f"old question {i}"}, {"role": "assistant", "text": f"old answer {i}"}]
    p = make_page(browser)
    page = p.goto()
    page.evaluate("v => localStorage.setItem('voicebot.history.v1', v)", json.dumps(seeded))
    page.reload()
    page.wait_for_function("document.querySelectorAll('.chip').length > 0")
    assert page.inner_text("#history-count") == "10"
    ask_chip(p, 0)  # 11th exchange: the oldest drops out, 20 messages remain
    assert page.inner_text("#history-count") == "10"
    open_history(page)
    texts = page.inner_text("#history-list")
    assert "old question 0" not in texts and "old question 1" in texts and "life story" in page.locator("#history-list .h-item").first.inner_text()
    assert len(json.loads(page.evaluate("localStorage.getItem('voicebot.history.v1')"))) == 20
    page.click("#history-clear")
    assert page.locator("#history-list .h-item").count() == 0 and page.is_visible("#history-empty")
    assert page.is_hidden("#history-count")
    page.reload()
    page.wait_for_function("document.querySelectorAll('.chip').length > 0")
    assert page.is_hidden("#history-count")


@ALL
def test_top_bar_stays_in_view_while_scrolling(make_page, browser):
    p = make_page(browser, viewport={"width": 390, "height": 600}, has_touch=browser != "firefox")
    page = p.goto()
    ask_chip(p, 0)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(300)
    assert page.evaluate("window.scrollY") > 50
    box = page.locator(".topbar").bounding_box()
    assert box["y"] == 0 and box["height"] >= 44
    assert page.is_visible("a[href='/about/']") and page.is_visible("#history-open")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@ALL
def test_sample_questions_shrink_to_one_strip_after_the_first_question(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    before = page.locator("#chips").bounding_box()["height"]
    assert page.inner_text("#ask-label") == "Try one of these"
    ask_chip(p, 0)
    assert page.inner_text("#ask-label") == "More questions"
    assert page.locator("#chips").bounding_box()["height"] < before / 2
    assert page.locator(".chip").count() == 8


@ALL
def test_static_files_must_be_revalidated(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    assert page.evaluate("fetch('/js/app.js').then(r => r.headers.get('cache-control'))") == "no-cache"


@ALL
def test_technical_page_is_linked_readable_and_fits_a_phone(make_page, browser):
    p = make_page(browser, viewport={"width": 360, "height": 740}, device_scale_factor=2, has_touch=browser != "firefox")
    page = p.goto()
    page.click(".topnav >> text=How it's built")
    page.wait_for_url("**/about/")
    assert page.inner_text("h1") == "How this voice bot is built"
    for heading in ("Design decisions", "What I'd change with more time", "What else could be added", "Known limits"):
        assert page.locator("h2", has_text=heading).count() == 1
    assert page.locator(".decision").count() >= 8
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate("document.querySelector('.diagram img').complete && document.querySelector('.diagram img').naturalWidth > 0")
    for a in page.locator(".toc a").all():
        assert a.bounding_box()["height"] >= 44
    assert page.get_attribute(".topnav a[aria-current]", "href") == "/about/"
    page.click(".topnav >> text=Voice bot")
    page.wait_for_selector("#talk")
    assert p.errors == []


# ----------------------------------------------------------------------------- no canned filler audio
@CHROMIUM_ONLY
def test_slow_turn_shows_thinking_state_and_plays_no_filler(make_page, server, browser):
    server.behaviour.first_token_delay = 1.6
    p = make_page(browser)
    page = p.goto()
    played = []
    page.on("response", lambda r: played.append(r.url) if r.url.endswith(".mp3") else None)
    page.locator(".chip").nth(0).click()
    page.wait_for_function("document.querySelector('#talk').dataset.state === 'thinking'")
    assert "thinking" in page.inner_text("#status").lower()
    page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)
    p.wait_state("idle", 40000)
    assert [u for u in played if "filler" in u] == []


# ----------------------------------------------------------------------------- layout and accessibility
@ALL
def test_mobile_layout_has_no_sideways_scroll_and_big_tap_targets(make_page, browser):
    p = make_page(browser, viewport={"width": 360, "height": 740}, device_scale_factor=2, has_touch=browser != "firefox")
    page = p.goto()
    page.locator(".chip").nth(0).click()
    page.wait_for_selector("#turn-a:not([hidden])")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for sel in ("#talk", ".btn-send", ".chip", "#text-input"):
        h = page.locator(sel).first.bounding_box()["height"]
        assert h >= 44, f"{sel} is {h}px tall"
    # the answer must be on screen without scrolling once it starts streaming (phones)
    page.wait_for_function("document.querySelector('#a-caption').textContent.length > 20")
    page.wait_for_timeout(800)
    box = page.locator("#turn-a").bounding_box()
    assert box["y"] < 740, f"answer starts at y={box['y']}, below the fold"


@ALL
def test_keyboard_focus_is_visible_and_controls_are_named(make_page, browser):
    p = make_page(browser)
    page = p.goto()
    # Safari does not Tab to buttons by default, so for WebKit focus programmatically after a key press.
    for _ in range(6):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.id") == "talk":
            break
    else:
        page.focus("#talk")
    assert page.evaluate("document.activeElement.id") == "talk"
    outline = page.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
    assert outline[0] != "none" and outline[1] >= 2, outline
    names = page.evaluate("""[...document.querySelectorAll('button, input')].filter(e => !e.hidden && e.offsetParent !== null)
        .filter(e => !(e.getAttribute('aria-label') || e.textContent.trim() || (e.labels && e.labels[0]?.textContent) || e.placeholder)).length""")
    assert names == 0
    assert page.get_attribute("#status", "role") == "status"
