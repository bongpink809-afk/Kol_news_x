# X KOL News Bot

Bot theo dõi tin tức từ danh sách tài khoản X (Twitter) theo ngách, phát hiện
bài đăng mới và gửi thông báo qua Telegram — đúng topic của từng ngách.

Polling chia **2 tier** theo mức độ cần gấp của ngách (xem mục 5), để giảm số
request/ngày mà không bỏ lỡ tin.

**Chỉ đọc dữ liệu.** Bot không đăng bài, không reply, không follow.

Nguồn dữ liệu tweet: [TwitterAPI.io](https://twitterapi.io) — endpoint
`GET https://api.twitterapi.io/twitter/user/last_tweets`
([docs](https://docs.twitterapi.io/api-reference/endpoint/get_user_last_tweets)).

## Cấu trúc file

```
.
├── .env.example
├── accounts.json           # ngách -> danh sách username X
├── topics.json             # ngách -> message_thread_id của topic Telegram
├── tiers.json              # ngách -> tier (hot/daily) + interval_minutes
├── last_seen.json          # tweet ID + thời điểm check gần nhất cho từng username
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
   TWITTERAPI_KEY=your_twitterapi_io_key
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=-100xxxxxxxxxx
   ```
   Lấy `TWITTERAPI_KEY` bằng cách đăng ký tài khoản tại
   [twitterapi.io](https://twitterapi.io) và tạo API key trong dashboard.
   Tài khoản mới được tặng $0.1 credit miễn phí, không cần thẻ — đủ dùng thử
   ở quy mô project này trong vài tháng đầu.
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
     topic. Lưu ý cơ chế 2-tier (mục 5): nếu chạy lại ngay trong vài phút, các
     ngách tier `daily` (interval 1440 phút) sẽ bị bỏ qua vì chưa tới lượt —
     đây là hành vi đúng, không phải lỗi. Muốn ép check lại ngay để test, xoá
     `last_checked_at` (hoặc cả entry) của username đó trong `last_seen.json`.
   - Nếu response từ TwitterAPI.io không đúng như code kỳ vọng, log sẽ in ra
     payload thô (giới hạn ký tự) để debug — nâng mức log lên DEBUG bằng cách
     sửa `logging.basicConfig(level=logging.DEBUG, ...)` trong `main.py` nếu
     cần xem full payload.

## 3. Chạy tự động bằng GitHub Actions

Workflow `.github/workflows/poll.yml` chạy mỗi 30 phút (nhịp nền — đủ để tier
`hot` 60 phút luôn được check đúng lúc, xem mục 5), cài dependencies, chạy
`main.py`, rồi tự commit lại `last_seen.json` nếu có thay đổi (GitHub Actions
không có ổ đĩa persistent giữa các lần chạy).

### Thêm secrets vào GitHub repo

1. Push repo này lên GitHub.
2. Vào **Settings → Secrets and variables → Actions → New repository secret**.
3. Thêm 3 secrets:
   - `TWITTERAPI_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Đảm bảo **Settings → Actions → General → Workflow permissions** đặt là
   **Read and write permissions**, để workflow có thể commit lại
   `last_seen.json`.
5. Workflow sẽ tự chạy theo lịch (`*/30 * * * *`), hoặc bạn có thể chạy thủ
   công qua tab **Actions → Poll X accounts and notify Telegram → Run
   workflow**.

## 4. Thêm/bớt tài khoản hoặc ngách

- **Thêm tài khoản vào ngách có sẵn**: mở `accounts.json`, thêm username
  (không kèm `@`) vào mảng của ngách tương ứng.
- **Thêm ngách mới**:
  1. Thêm key ngách mới + danh sách username vào `accounts.json`.
  2. Tạo topic mới trong Telegram group, lấy `message_thread_id` (xem mục 1).
  3. Thêm ngách đó vào `topics.json` với `message_thread_id` tương ứng.
  4. Thêm ngách đó vào tier phù hợp trong `tiers.json` (xem mục 5) — nếu quên,
     bot vẫn chạy bình thường nhưng sẽ check ngách đó ở **mọi** lần chạy (mỗi
     30 phút) và ghi cảnh báo trong log, thay vì theo đúng tier mong muốn.
- **Xoá tài khoản/ngách**: xoá khỏi `accounts.json` (và `topics.json` +
  `tiers.json` nếu xoá cả ngách). Không cần dọn `last_seen.json` — các entry
  thừa không ảnh hưởng gì đến việc chạy bot.

## 5. Cơ chế 2-tier polling

Để giảm số request gọi TwitterAPI.io mỗi ngày, các ngách được chia vào 2 tier trong
`tiers.json`, mỗi tier có `interval_minutes` riêng:

```json
{
  "hot": {
    "interval_minutes": 60,
    "niches": ["Tin tức lớn"]
  },
  "daily": {
    "interval_minutes": 1440,
    "niches": ["Tin các chain lớn", "Onchain", "..."]
  }
}
```

- GitHub Actions vẫn chạy `main.py` mỗi 30 phút (nhịp nền), nhưng với mỗi tài
  khoản, bot chỉ thực sự gọi API TwitterAPI.io nếu đã trôi qua đủ
  `interval_minutes` của tier chứa ngách đó kể từ lần check gần nhất
  (`last_checked_at` lưu trong `last_seen.json`). Chưa tới lượt thì bỏ qua —
  không tính là lỗi.
- Khi tới lượt check, bot lấy **toàn bộ** tweet mới tích luỹ từ lần check
  trước (không chỉ tweet mới nhất) và gửi hết theo thứ tự thời gian cũ→mới,
  vì tier `daily` cách nhau 24 tiếng nên 1 tài khoản có thể có nhiều hơn 1
  tweet mới trong khoảng đó.
- TwitterAPI.io trả tối đa 20 tweet/lần gọi (không phân trang trong code
  này). Nếu 1 tài khoản đăng nhiều hơn 20 tweet mới trong 1 chu kỳ check, log
  sẽ cảnh báo
  khả năng bị bỏ sót tweet cũ hơn — hiếm khi xảy ra với các tài khoản trong
  danh sách, nhưng nếu thấy cảnh báo này thường xuyên với 1 tài khoản, cân
  nhắc chuyển ngách đó sang tier check nhanh hơn.
- **Đổi tier cho 1 ngách**: chỉ cần sửa `tiers.json` (thêm/xoá tên ngách khỏi
  mảng `niches` của tier tương ứng, hoặc đổi `interval_minutes`), không cần
  sửa code, không cần deploy lại gì khác — lần chạy tiếp theo của GitHub
  Actions sẽ áp dụng ngay.
- `last_seen.json` giờ lưu cả `last_tweet_id` và `last_checked_at` cho mỗi
  username. Format cũ (chỉ có tweet ID dạng số/`"..."`) vẫn đọc được bình
  thường — bot tự nâng cấp entry đó lên format mới ngay lần check kế tiếp,
  không cần bạn sửa tay file này.

## Lưu ý

- Không hardcode key/token trong code — chỉ đọc từ `.env` (local) hoặc GitHub
  Secrets (production).
- Bot chỉ đọc dữ liệu tweet công khai, không đăng bài/reply/follow.
- Nếu TwitterAPI.io đổi format response, sửa lại phần parse trong
  `fetch_last_tweets()` trong `main.py` cho khớp.
- QPS limit của TwitterAPI.io **theo số dư credit trong tài khoản**, không cố
  định (xem [twitterapi.io/qps-limits](https://twitterapi.io/qps-limits)):
  ≥1.000 credit → 3 QPS, ≥5.000 → 6, ≥10.000 → 10, ≥50.000 → 20. Tài khoản
  mới/số dư thấp bị giới hạn chỉ 1 request/5 giây. Vì project này chỉ gọi
  ~130 request/ngày (không hề gấp), `main.py` mặc định delay an toàn nhất —
  `TWITTERAPI_REQUEST_DELAY_SECONDS = 5.0` — giữa các lần gọi API. Sau khi
  nạp thêm credit và biết chắc mình ở tier QPS nào, có thể giảm hằng số này
  xuống. Delay 1s giữa các tin Telegram (`TELEGRAM_REQUEST_DELAY_SECONDS`)
  không đổi, đó là giới hạn của Telegram, không liên quan TwitterAPI.io.
- Giá TwitterAPI.io hiện tại: **$0.15/1.000 tweet** (~$0.00015/request tối
  thiểu), tính theo lượt dùng thật, không có gói tháng cố định.
- Tài khoản `MarginATM` (ngách "Tin tức nhanh") đã bị gỡ khỏi `accounts.json`
  vì không còn hoạt động.
