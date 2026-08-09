# X KOL News Bot

Bot theo dõi tin tức từ danh sách tài khoản X (Twitter) theo ngách, phát hiện
bài đăng mới và gửi thông báo qua Telegram — đúng topic của từng ngách.

**Chỉ đọc dữ liệu.** Bot không đăng bài, không reply, không follow.

Nguồn dữ liệu tweet: [Sorsa API](https://api.sorsa.io) — endpoint
`POST https://api.sorsa.io/v3/user-tweets`
([docs](https://docs.sorsa.io/api-reference/tweets/user-tweets)).

## Cấu trúc file

```
.
├── .env.example
├── accounts.json           # ngách -> danh sách username X
├── topics.json             # ngách -> message_thread_id của topic Telegram
├── last_seen.json          # tweet ID mới nhất đã xử lý cho từng username
├── main.py
├── requirements.txt
└── .github/workflows/poll.yml
```

## 1. Chuẩn bị Telegram Group + Topics

1. Tạo một Group Telegram mới (không phải Channel).
2. Vào **Group Settings → Edit → Topics** và bật **Topics** (Forum mode). Group
   phải bật tính năng này trước khi topic mới xuất hiện.
3. Tạo lần lượt các topic có tên trùng với từng ngách trong `accounts.json`
   (ví dụ: "Tin tức lớn", "Onchain", "Sàn", ...).
4. Thêm bot vào group (Add Member), sau đó cấp quyền admin hoặc ít nhất quyền
   gửi tin nhắn trong topic (Group Settings → Administrators, hoặc Permissions
   nếu group cho phép member thường gửi trong topic).

### Lấy `message_thread_id` của từng topic

`message_thread_id` chính là ID của tin nhắn đầu tiên tạo ra topic đó. Cách
lấy:

1. Lấy `chat_id` của group: thêm bot @RawDataBot (hoặc @userinfobot) vào group,
   hoặc gọi `https://api.telegram.org/bot<TOKEN>/getUpdates` sau khi gửi một
   tin nhắn bất kỳ trong group — trường `chat.id` trong response chính là
   `TELEGRAM_CHAT_ID` (group id thường là số âm, ví dụ `-1001234567890`).
2. Vào từng topic, gửi một tin nhắn bất kỳ (ví dụ "test").
3. Gọi API `getUpdates` để xem tin nhắn đó:
   ```
   https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
   ```
4. Trong response, tìm object `message` tương ứng với tin nhắn bạn vừa gửi.
   Trường `message_thread_id` (hoặc `message_id` nếu đó là tin nhắn đầu tiên
   của topic — hai giá trị này bằng nhau khi tin nhắn đó chính là tin mở topic)
   chính là ID cần điền vào `topics.json`.
   - Lưu ý: `getUpdates` chỉ trả về các update chưa được đọc. Nếu bot đã có
     webhook hoặc đã đọc hết update, hãy gửi tin nhắn mới trong topic rồi gọi
     lại `getUpdates` ngay sau đó.
5. Lặp lại cho từng topic, điền kết quả vào `topics.json`:
   ```json
   {
     "Tin tức lớn": 2,
     "Onchain": 5,
     ...
   }
   ```
   Ngách nào chưa có `message_thread_id` (giá trị `null`) sẽ được gửi vào
   topic **General** của group, kèm cảnh báo trong log.

## 2. Cài đặt & chạy thử local

1. Cài Python 3.9+.
2. Cài dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` thành `.env` và điền:
   ```
   SORSA_API_KEY=your_sorsa_api_key
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=-100xxxxxxxxxx
   ```
   Lấy `SORSA_API_KEY` bằng cách đăng ký tài khoản tại
   [api.sorsa.io](https://api.sorsa.io) và tạo API key trong dashboard (mục
   Key/Usage trong [tài liệu Sorsa](https://docs.sorsa.io)).
4. Điền `message_thread_id` thật vào `topics.json` (xem mục 1).
5. Chạy thử:
   ```bash
   python main.py
   ```
   - Lần chạy đầu tiên cho mỗi username sẽ chỉ lưu tweet ID mới nhất vào
     `last_seen.json`, **không gửi** tin nhắn (tránh spam tin cũ khi mới cài
     đặt).
   - Chạy `python main.py` lần thứ hai (sau khi có tweet mới thật, hoặc sau khi
     xoá thử một username khỏi `last_seen.json`) để kiểm tra bot gửi tin đúng
     topic.
   - Nếu response từ Sorsa không đúng như code kỳ vọng, log sẽ in ra
     payload thô (giới hạn ký tự) để debug — nâng mức log lên DEBUG bằng cách
     sửa `logging.basicConfig(level=logging.DEBUG, ...)` trong `main.py` nếu
     cần xem full payload.

## 3. Chạy tự động bằng GitHub Actions

Workflow `.github/workflows/poll.yml` chạy mỗi 20 phút, cài dependencies,
chạy `main.py`, rồi tự commit lại `last_seen.json` nếu có thay đổi (GitHub
Actions không có ổ đĩa persistent giữa các lần chạy).

### Thêm secrets vào GitHub repo

1. Push repo này lên GitHub.
2. Vào **Settings → Secrets and variables → Actions → New repository secret**.
3. Thêm 3 secrets:
   - `SORSA_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Đảm bảo **Settings → Actions → General → Workflow permissions** đặt là
   **Read and write permissions**, để workflow có thể commit lại
   `last_seen.json`.
5. Workflow sẽ tự chạy theo lịch (`*/20 * * * *`), hoặc bạn có thể chạy thủ
   công qua tab **Actions → Poll X accounts and notify Telegram → Run
   workflow**.

## 4. Thêm/bớt tài khoản hoặc ngách

- **Thêm tài khoản vào ngách có sẵn**: mở `accounts.json`, thêm username
  (không kèm `@`) vào mảng của ngách tương ứng.
- **Thêm ngách mới**:
  1. Thêm key ngách mới + danh sách username vào `accounts.json`.
  2. Tạo topic mới trong Telegram group, lấy `message_thread_id` (xem mục 1).
  3. Thêm ngách đó vào `topics.json` với `message_thread_id` tương ứng.
- **Xoá tài khoản/ngách**: xoá khỏi `accounts.json` (và `topics.json` nếu xoá
  cả ngách). Không cần dọn `last_seen.json` — các entry thừa không ảnh hưởng
  gì đến việc chạy bot.

## Lưu ý

- Không hardcode key/token trong code — chỉ đọc từ `.env` (local) hoặc GitHub
  Secrets (production).
- Bot chỉ đọc dữ liệu tweet công khai, không đăng bài/reply/follow.
- Nếu Sorsa đổi format response, sửa lại phần parse trong
  `fetch_last_tweets()` trong `main.py` cho khớp.
- Sorsa cho phép 20 request/giây trên mọi gói — thoải mái hơn nhiều so với
  giới hạn cần lo trước đây; delay giữa các lần gọi Sorsa trong `main.py`
  (`SORSA_REQUEST_DELAY_SECONDS`) chỉ đặt 0.3s cho an toàn, không phải do giới
  hạn chặt. Delay 1s giữa các tin Telegram (`TELEGRAM_REQUEST_DELAY_SECONDS`)
  giữ nguyên vì đó là giới hạn của Telegram, không liên quan tới Sorsa.
- Sorsa không trả về field permalink cho tweet, nên bot tự dựng link theo dạng
  `https://x.com/<username>/status/<tweet_id>`.
