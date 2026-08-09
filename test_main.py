import unittest
from unittest.mock import patch, MagicMock

import main


# Real TwitterAPI.io /twitter/user/last_tweets response shape, confirmed from a
# live GitHub Actions run log (2026-08-09, run #12): tweets are nested under
# "data", not top-level as the (outdated) public docs describe.
SAMPLE_RESPONSE = {
    "status": "success",
    "code": 0,
    "msg": "success",
    "data": {
        "pin_tweet": None,
        "tweets": [
            {
                "type": "tweet",
                "id": "2086463906772590816",
                "url": "https://x.com/BullTheoryio/status/2086463906772590816",
                "text": "Some tweet text",
                "createdAt": "Sun Aug 09 15:20:00 +0000 2026",
                "author": {"userName": "BullTheoryio"},
            }
        ],
    },
}


class FetchLastTweetsTests(unittest.TestCase):
    @patch("main.requests.get")
    def test_parses_tweets_nested_under_data(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_RESPONSE
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        tweets = main.fetch_last_tweets("BullTheoryio")

        self.assertEqual(len(tweets), 1)
        self.assertEqual(tweets[0]["id"], "2086463906772590816")
        self.assertEqual(main.tweet_id_int(tweets[0]), 2086463906772590816)


class ProcessAccountTests(unittest.TestCase):
    @patch("main.requests.get")
    @patch("main.send_telegram_message")
    def test_new_tweet_is_sent_to_telegram(self, mock_send, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_RESPONSE
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        sent_count, newest_id, fetched_count = main.process_account(
            niche="Tin tức lớn",
            username="BullTheoryio",
            thread_id=123,
            previous_id=2086000000000000000,  # older than the sample tweet's id
        )

        self.assertEqual(fetched_count, 1)
        self.assertEqual(sent_count, 1)
        self.assertEqual(newest_id, 2086463906772590816)
        mock_send.assert_called_once_with(
            "Tin tức lớn",
            "BullTheoryio",
            "Some tweet text",
            "https://x.com/BullTheoryio/status/2086463906772590816",
            123,
        )


class SendTelegramMessageTests(unittest.TestCase):
    @patch("main.time.sleep")
    @patch("main.requests.post")
    def test_retries_once_after_429_and_succeeds(self, mock_post, mock_sleep):
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.ok = False
        rate_limited.json.return_value = {
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests: retry after 9",
            "parameters": {"retry_after": 9},
        }

        success = MagicMock()
        success.status_code = 200
        success.ok = True

        mock_post.side_effect = [rate_limited, success]

        main.send_telegram_message("Tin tức lớn", "BullTheoryio", "text", "https://x.com/1", 123)

        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(10)  # retry_after (9) + 1

    @patch("main.time.sleep")
    @patch("main.requests.post")
    def test_second_429_raises(self, mock_post, mock_sleep):
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.ok = False
        rate_limited.text = "Too Many Requests"
        rate_limited.json.return_value = {"parameters": {"retry_after": 3}}

        mock_post.side_effect = [rate_limited, rate_limited]

        with self.assertRaises(RuntimeError):
            main.send_telegram_message("Tin tức lớn", "BullTheoryio", "text", "https://x.com/1", 123)

        self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
