"""Offline browser selection regressions; real launches are verified separately."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bridge as b


class LoginLaunchTests(unittest.TestCase):
    def launch(self, browser, channel="", platform="nt"):
        environment = {"PRISM_BROWSER_CHANNEL": channel}
        with patch.object(b, "os", SimpleNamespace(name=platform, environ=environment)):
            return b._launch_login_context(SimpleNamespace(chromium=browser))

    def test_explicit_missing_channel_does_not_silently_change_browser(self):
        browser = Mock()
        failure = b.PlaywrightError("Chromium distribution 'chrome' is not found")
        browser.launch_persistent_context.side_effect = failure
        with self.assertRaises(b.PlaywrightError) as raised:
            self.launch(browser, channel="chrome")
        self.assertIs(raised.exception, failure)
        self.assertEqual(browser.launch_persistent_context.call_count, 1)

    def test_profile_failure_is_preserved_without_retry(self):
        for message in ("profile is already in use", "Target page, context or browser has been closed"):
            with self.subTest(message=message):
                browser = Mock()
                failure = b.PlaywrightError(message)
                browser.launch_persistent_context.side_effect = failure
                with self.assertRaises(b.PlaywrightError) as raised:
                    self.launch(browser)
                self.assertIs(raised.exception, failure)
                self.assertEqual(browser.launch_persistent_context.call_count, 1)

    def test_missing_installation_skips_to_next_available_browser(self):
        for message in ("Chromium distribution 'chrome' is not found at path", "Executable doesn't exist at path"):
            with self.subTest(message=message):
                browser = Mock()
                context = object()
                browser.launch_persistent_context.side_effect = [b.PlaywrightError(message), context]
                self.assertIs(self.launch(browser), context)
                self.assertEqual(browser.launch_persistent_context.call_args.kwargs["channel"], "msedge")

    def test_all_missing_retains_bundled_install_error_for_gui(self):
        browser = Mock()
        failure = b.PlaywrightError("Executable doesn't exist at bundled browser path")
        browser.launch_persistent_context.side_effect = [
            b.PlaywrightError("Chromium distribution 'chrome' is not found"),
            b.PlaywrightError("Chromium distribution 'msedge' is not found"),
            failure,
        ]
        with self.assertRaises(b.PlaywrightError) as raised:
            self.launch(browser)
        self.assertIs(raised.exception, failure)
        self.assertNotIn("channel", browser.launch_persistent_context.call_args.kwargs)
        self.assertEqual(browser.launch_persistent_context.call_count, 3)

    def test_non_windows_missing_browser_does_not_try_windows_channels(self):
        browser = Mock()
        failure = b.PlaywrightError("Executable doesn't exist at bundled browser path")
        browser.launch_persistent_context.side_effect = failure
        with self.assertRaises(b.PlaywrightError) as raised:
            self.launch(browser, platform="posix")
        self.assertIs(raised.exception, failure)
        self.assertEqual(browser.launch_persistent_context.call_count, 1)

    def test_programming_error_is_not_hidden_as_installation_failure(self):
        browser = Mock()
        browser.launch_persistent_context.side_effect = TypeError("invalid launch option")
        with self.assertRaises(TypeError):
            self.launch(browser)
        self.assertEqual(browser.launch_persistent_context.call_count, 1)

    def test_windows_uses_chrome_when_launch_succeeds(self):
        browser = Mock()
        context = object()
        browser.launch_persistent_context.return_value = context
        self.assertIs(self.launch(browser), context)
        self.assertEqual(browser.launch_persistent_context.call_count, 1)
        self.assertEqual(browser.launch_persistent_context.call_args.kwargs["channel"], "chrome")
        self.assertFalse(browser.launch_persistent_context.call_args.kwargs["headless"])

    def test_explicit_channel_success_does_not_fall_back(self):
        browser = Mock()
        context = object()
        browser.launch_persistent_context.return_value = context
        self.assertIs(self.launch(browser, channel="msedge"), context)
        self.assertEqual(browser.launch_persistent_context.call_count, 1)
        self.assertEqual(browser.launch_persistent_context.call_args.kwargs["channel"], "msedge")
        self.assertFalse(browser.launch_persistent_context.call_args.kwargs["headless"])


class UnmanagedLoginTests(unittest.TestCase):
    def test_windows_launches_system_browser_without_playwright(self):
        process = Mock()
        with patch.object(b, "_login_channels", return_value=("chrome",)), patch.object(
            b, "_browser_executable", return_value="C:/Chrome/chrome.exe"
        ), patch.object(b.subprocess, "Popen", return_value=process) as launch:
            channel, got = b._launch_unmanaged_login_browser()
        self.assertEqual(channel, "chrome")
        self.assertIs(got, process)
        command = launch.call_args.args[0]
        self.assertEqual(command[0], "C:/Chrome/chrome.exe")
        self.assertIn(f"--user-data-dir={b.PROFILE_DIR}", command)
        self.assertIn("--new-window", command)

    def test_bundled_channel_keeps_playwright_path(self):
        with patch.object(b, "_login_channels", return_value=(None,)), patch.object(
            b.subprocess, "Popen"
        ) as launch:
            self.assertEqual(b._launch_unmanaged_login_browser(), (None, None))
        launch.assert_not_called()

    def test_explicit_missing_system_browser_is_not_silently_changed(self):
        with patch.dict(b.os.environ, {"PRISM_BROWSER_CHANNEL": "chrome"}), patch.object(
            b, "_login_channels", return_value=("chrome",)
        ), patch.object(b, "_browser_executable", return_value=None):
            with self.assertRaises(b.PlaywrightError):
                b._launch_unmanaged_login_browser()

    def test_closed_browser_harvests_profile_before_commit(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(b.time, "time", side_effect=(0, 1)), patch.object(
            b.time, "sleep"
        ), patch.object(b, "_profile_browser_locked", return_value=False), patch.object(
            b, "_harvest_profile_cookies", return_value="prism_oai_access_token=token"
        ) as harvest, patch.object(b, "_commit_login_cookie", return_value=True) as commit:
            b._finish_unmanaged_login(process, "chrome")
        harvest.assert_called_once_with("chrome")
        commit.assert_called_once_with("prism_oai_access_token=token")

    def test_challenge_page_is_detected_without_reading_dom(self):
        page = SimpleNamespace(url="https://auth.openai.com/", title=lambda: "Just a moment...")
        self.assertTrue(b._looks_like_challenge(page))
        normal = SimpleNamespace(url="https://prism.openai.com/", title=lambda: "Prism")
        self.assertFalse(b._looks_like_challenge(normal))

if __name__ == "__main__":
    unittest.main()
