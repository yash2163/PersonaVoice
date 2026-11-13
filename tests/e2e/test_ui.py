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
    p.wait_state("transcribing", 5000) if p.state() == "recording" else None


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


# ----------------------------------------------------------------------------- fillers
@CHROMIUM_ONLY
def test_filler_plays_only_on_slow_turns(make_page, server, browser):
    def fillers_after_turn(first_token_delay):
        server.behaviour.first_token_delay = first_token_delay
        p = make_page(browser)
        page = p.goto("?debug=1")
        page.locator(".chip").nth(0).click()
        page.wait_for_function("document.querySelector('#a-caption').textContent.includes('last one')", timeout=20000)
        p.wait_state("idle", 40000)
        page.wait_for_selector("#debug:not([hidden])")
        return int(re.search(r"fillers played\s+(\d+)", page.inner_text("#debug")).group(1))

    assert fillers_after_turn(0.0) == 0
    assert fillers_after_turn(1.6) >= 1


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
