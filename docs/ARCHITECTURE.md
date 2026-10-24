# Kiến trúc hiện tại: pipeline Big Data và gợi ý theo phiên

Cập nhật ngày 24/10/2026 theo snapshot `strength-20261022-v1` trong ZIP
`storefront-v2`. Hệ thống có hai đường chạy cần trình bày riêng:

1. **Demo cửa hàng đang phục vụ:** FastAPI đọc model bundle và hai SQLite đã
   xuất sẵn; nhận ngữ cảnh phiên từ trình duyệt và tính Top K. Model chính là
   **conditional item-item kết hợp chuyển tiếp**, trộn độ mạnh liên kết 75/25.
2. **Pipeline dữ liệu Phase 1–7:** replay CSV qua Kafka, Spark, Delta/HDFS và
   MongoDB/Redis; có nhánh huấn luyện ALS ban đầu. Pipeline và các thí nghiệm
   graph chạy riêng, không chạy lại khi khách bấm Xem/Mua trên cửa hàng.

Mã nguồn không đồng nghĩa mọi thành phần đã được chạy trong lần demo hiện tại.
Lần kiểm tra ZIP ngày 24/10 chỉ kiểm tra snapshot/API/cửa hàng; không phải một
lần xác minh mới toàn bộ Kafka → Spark → HDFS → Airflow. Xem
[kiểm tra ZIP](ZIP_DEMO_VERIFICATION.md) và [ví dụ dữ liệu thật](REAL_DATA_EXAMPLE.md).

## Luồng phục vụ đang dùng

```mermaid
flowchart LR
  H["CSV lịch sử đã làm sạch"] --> T["Huấn luyện graph offline"]
  T --> I["SQLite: neighbors và transitions"]
  H --> S["SQLite: seen_items"]
  T --> B["Bundle: cấu hình, popularity, phiên bản, metrics"]
  UI["Cửa hàng: ngữ cảnh trong tab"] -->|"POST events + k"| API["FastAPI"]
  B --> API
  I --> API
  S -->|"lọc lịch sử nếu có visitorid"| API
  MR["MongoDB / Redis: lịch sử và cache user"] --> API
  API --> R["Xếp hạng 75% item-item + 25% chuyển tiếp"]
  R --> F["Loại ID đã tương tác, bổ sung popularity nếu thiếu"]
  F -->|"Top K ID + score"| UI
```

Demo snapshot khởi động ba service: **api, mongodb, redis**. Không cần bật
Kafka/Spark/HDFS/Airflow để đọc snapshot. MongoDB/Redis mới, chưa có projection
lịch sử, vẫn chạy được vì bundle và SQLite chứa dữ liệu phục vụ cần thiết.
Frontend gán tên/ảnh/giá/danh mục minh họa cố định theo ID; các thuộc tính này
không tham gia mô hình và không phải thông tin sản phẩm thật.

### Thành phần và trách nhiệm

| Thành phần | Vai trò hiện tại |
| --- | --- |
| `service/static/` | Cửa hàng, giỏ mô phỏng, ngữ cảnh phiên, trình bày kết quả |
| `POST /api/session/recommendations` | Tính lại gợi ý từ ngữ cảnh gửi lên; không cache kết quả phiên dùng chung |
| `pipeline/session_model.py` | Chọn seed, đọc hàng xóm SQLite, gọi scorer dùng chung với offline |
| `pipeline/session_transition.py` | Học chuyển tiếp và hàm `strength_fused_rank` |
| `pipeline/session_upgrade.py` | `EventData`, tạo cohort và `SparseGraph` |
| `model_bundle.json` | Cấu hình đang chọn, popularity, metrics, phiên bản; còn giữ gợi ý user tĩnh |
| `session_neighbors.sqlite` | Hai bảng `neighbors` và `transitions`, tra theo seed |
| `seen_items.sqlite` | Lịch sử user đầy đủ để loại ID đã thấy khi truyền visitor ID |
| `GET /api/recommendations/{visitorid}` | Luồng user tĩnh `item_knn_hybrid`; ngoài 1.000 user có gợi ý xuất sẵn thì fallback |
| `GET /api/metrics` | Đọc KPI/metrics lịch sử đúng run, không chấm lại từ lượt bấm thử |

### Model thực sự học gì?

Nhánh item-item dùng ma trận **visitor–item nhị phân**: mỗi user chỉ đóng góp
một lần cho một ID, giữ tối đa 20 ID phân biệt gần nhất/user. Với seed A và
ứng viên B, `n(A)` là số user có A, `n(A,B)` là số user có cả hai sau giới hạn:

```text
co_score(A,B) = n(A,B) / n(A) × n(A,B) / (n(A,B) + 3)
```

Hệ số cuối giảm ảnh hưởng của liên kết có ít quan sát. Giữ tối đa 100 hàng xóm
mỗi seed; đây là liên quan qua hành vi, không phải phân loại hai món cùng loại.

Nhánh chuyển tiếp đếm A → B giữa hai nhóm timestamp liên tiếp của cùng user
trong phiên gap 30 phút; B phải mới trong phiên. Hai ID có cùng timestamp
không được dùng để dự đoán lẫn nhau. Giữ tối đa 100 cạnh mỗi seed.

Model nhận tối đa 100 sự kiện/request, chọn 5 ID phân biệt gần nhất làm seed.
Snapshot đang phục vụ có cấu hình sau (cấu hình trong bundle ưu tiên hơn default):

| Tham số | Giá trị đang dùng |
| --- | --- |
| Graph / similarity | `binary_user` / `conditional` |
| Trọng số view / addtocart / transaction | **1 / 1 / 1** |
| Giảm trọng số theo vị trí gần đây | `0.8^r`, r=0 cho seed mới nhất |
| Giảm trọng số liên tục theo số phút | Tắt: `query_half_life_minutes=null` |
| Hệ số chuyển tiếp | 0,25; item-item 0,75 |
| Chuẩn hóa và số mũ | Mỗi seed/nhánh chia cạnh mạnh nhất rồi lấy căn bậc hai |
| Popularity trong điểm fusion | 0; chỉ bổ sung cuối danh sách nếu thiếu |

```text
score(B) = tổng theo seed A của affinity(A) ×
           [0,75 × sqrt(co(A,B)/max_co(A))
            + 0,25 × sqrt(trans(A,B)/max_trans(A))]
```

Cạnh/nhánh không tồn tại đóng góp 0; không tự phân phối lại trọng số. Điểm
không phải xác suất mua và có thể lớn hơn 1 khi cộng nhiều seed. Lọc toàn bộ
ID trong ngữ cảnh, thêm lịch sử user nếu có visitorid; xếp score giảm dần,
hòa điểm thì ID tăng dần. Bổ sung ID phổ biến chưa thấy nếu chưa đủ K.

**Không nói model đang phục vụ coi mua mạnh gấp 5 lần xem.** Mức 1/3/5 còn
ở cấu hình mặc định, nhánh ALS và các thí nghiệm cũ; bundle hiện chọn 1/1/1.
Đổi loại hành vi trên cùng một seed không đảm bảo đổi thứ hạng. ID mới và thứ
tự seed có thể làm thay đổi kết quả.

### Bấm Mua có huấn luyện lại không?

Không. Tab giữ context; Xem/+Giỏ/Mua gửi sự kiện mô phỏng tới scorer cố định.
API trả `persistence=client_context_only`. Nó không ghi vào CSV, Kafka, tập
train hoặc đơn hàng; không cập nhật trọng số model. Mua ngay trên thẻ ghi một
`transaction`, không tự thêm view/cart. Phiên mới xóa context và giỏ; frontend
cũng hết phiên sau khoảng cách tương tác trên 30 phút. API dùng đúng context
được gửi lên, không tự tách phiên từ những request của trình duyệt.

### Đánh giá công bằng và phiên bản

Phép đo chính là Top 10 cho **mọi ID mới còn lại trong phiên**, trên 5.000
query của 4.759 visitor có ngữ cảnh trước truy vấn. Dùng cùng cohort, ngữ cảnh,
bộ lọc và luật tạo ứng viên giữa các phương án, không lấy mẫu negative. Đây
không phải đánh giá mọi user, mọi lượt đầu tiên hay người hoàn toàn chưa có
tương tác. Eligible 38.945 / 274.857 phiên hoạt động; không bỏ qua điều kiện này.

Chọn tham số qua hai fold validation theo thời gian. Graph chấm test chỉ đọc
timestamp ≤ **1440538554824**; context đứng trước target. Sau khi giữ cố định
metrics, graph phục vụ được refit trên đủ **2.755.641 event sạch**, có giới hạn
20 ID/user như trên. Không dùng graph full để tự chấm lại tương lai lịch sử.

| Chính sách, cùng phép chấm | Precision@10 | Recall@10 | NDCG@10 |
| --- | ---: | ---: | ---: |
| Strength fusion hiện tại | 4,148% | 28,2803% | 19,7041% |
| Bản trước RRF 75/25 | 4,116% | 28,0809% | 19,3253% |
| Popularity | 0,172% | 0,9148% | 0,4982% |

Recall là trung bình recall/query; không phải hit-rate. 1.731/5.000 query có
ít nhất một hit cho hit-rate 34,62%. Chỉ NDCG có khoảng tin cậy 95% mức tăng
hoàn toàn dương so với RRF. Test đã được xem trong các vòng phát triển; cần
giai đoạn mới để xác nhận. Không có kết quả A/B, CTR hoặc tăng doanh thu thật.

Run `strength-20261022-v1` có `model_version` cấp bundle,
`session_model.model_version` cấp scorer và `metric_model_version` cho graph
đánh giá. API user tĩnh có metrics riêng, không so trực tiếp với session.
Xem [báo cáo JSON](../reports/metrics/session_heldout_comparison.json).

### Kiểm tra tính toàn vẹn snapshot

Đối chiếu checksum ZIP phát hành và `PACKAGE_SHA256.json` cho dữ liệu, bundle,
hai SQLite và báo cáo. `session_model.index_profile.bytes/sha256` trong bundle
gốc còn là thống kê cũ trước khi thêm bảng chuyển tiếp; không dùng hai trường
đó để xác thực toàn file SQLite hiện nay. Manifest gói và `PRAGMA quick_check`
là bằng chứng sử dụng trong lần kiểm tra ZIP. Lần cập nhật tài liệu này giữ
nguyên model và các chỉ số lịch sử.

## Pipeline dữ liệu và các nhánh thí nghiệm Phase 1–7

## Phạm vi triển khai và giới hạn thiết kế

- Lần build/chạy đầu cần Internet để tải image, hệ thống Java, Python packages
  và Kafka/Delta Maven JAR. Các wheel NumPy/SciPy trong `vendor_wheels` chỉ giảm
  một phần tải phụ thuộc, không biến toàn bộ setup thành offline.
- Compose demo dùng một broker Kafka, replication 1, Spark `local[2]`, một
  NameNode và một DataNode HDFS với replication 1. Không có high availability
  hoặc bằng chứng chịu tải của cụm nhiều máy.
- Cổng host bind vào `127.0.0.1`: API mặc định 8000, Kafka 19092, NameNode UI
  19870, Airflow tùy chọn 8088. MongoDB/Redis và job worker không công bố cổng
  host. Đây là giới hạn truy cập demo, không thay thế authentication/TLS và
  vận hành production. Gói ZIP không phải một bản triển khai cloud.
- Đường online hiện tính lại lịch sử người dùng/item bị ảnh hưởng và có bước
  duyệt clean bằng iterator; live lake xây lại snapshot xác định. Cách làm này
  ưu tiên kiểm tra được tính đúng trên demo, không hứa chi phí mỗi event cố định.
- Chỉ một writer ghi lake/projection của một run. Runner có khóa file toàn
  pipeline; live writer phải được người chạy phối hợp dừng trước retrain. Không
  chạy hai live process ghi cùng output/checkpoint.

### Đường đi dữ liệu khi chạy pipeline đầy đủ

```mermaid
flowchart LR
  CSV[RetailRocket CSV] --> P[Producer JSON có run/event ID]
  P --> K[Kafka: 4 topic]
  K --> V[Spark schema và validation]
  V --> Q[Quarantine Parquet]
  V --> D[Watermark và dedup event ID]
  D --> C[Clean Parquet bền vững]
  C --> W[Window và session query]
  C --> L[Delta Bronze/Silver/Gold trên HDFS]
  C --> O[Mongo events và projections; Redis keys]
  W --> O
  L --> ML[Nhánh ALS ban đầu: MLlib temporal CV]
  ML --> B[Bundle ALS và SQLite seen của run ALS]
  B --> API[FastAPI và dashboard]
  C --> G[Thí nghiệm graph offline từ snapshot sạch]
  G --> GB[Bundle và SQLite graph theo phiên]
  GB --> API
  O --> API
  AF[Airflow theo lịch] --> J[Typed retrain worker]
  J --> ML
```

`pipeline.runner` thực hiện replay hữu hạn: tạo topic → producer → consumer
verify → ingest `availableNow` → feature → snapshot → lake → NoSQL → training
→ ACID check → model registry → kích hoạt latest run. Chỉ đổi
`outputs/latest_run.json` sau khi các bước bắt buộc thành công. API không gọi
Spark để train trong HTTP request.

`pipeline.streaming` là đường liên tục: ingest, feature và query
`lake_online` có checkpoint riêng. Micro-batch mới trên clean kích hoạt xây
lake và publish NoSQL. Luồng này không tự đổi model; train/retrain là một
thao tác có phiên bản riêng.

## Dữ liệu nguồn và hợp đồng sự kiện

RetailRocket được kế thừa từ Phase 1: `events.csv` có 2.756.101 dòng dữ liệu,
khoảng 94,2 MB; mẫu đi kèm có 10.000 dòng thật. Checksum/nguồn có trong
`data/metadata/`. Timestamp gốc là epoch **millisecond**, là thời gian hành vi
lịch sử, không phải đồng hồ khi chạy demo.

| Trường/cơ chế | Hợp đồng |
| --- | --- |
| CSV gốc | `timestamp,visitorid,event,itemid,transactionid` |
| JSON | `schema_version=1`, `event_id`, `run_id`, năm trường nguồn, `event_type`, `event_time`, `ingested_at`; review fields/synthetic là tùy chọn |
| Event ID | SHA-256 của JSON compact `[timestamp,visitorid,event,itemid,transactionid]`; không gồm run ID/offset/thời điểm ingest |
| Kafka key | Visitor ID, giữ cùng người dùng trên cùng partition của mỗi topic |
| `view` | Topic/event type `page_view`, trọng số 1 |
| `addtocart` | Topic/event type `add_to_cart`, trọng số 3 |
| `transaction` | Topic/event type `purchase`, trọng số 5; yêu cầu transaction ID |
| `review` | Topic/event type `review`, trọng số 0; nguồn RetailRocket gốc không có loại này |

Bốn topic có 3 partition và replication 1, retention 7 ngày. Listener nội bộ
là `kafka:29092`; listener máy host là `localhost:19092`. Thứ tự Kafka chỉ có
trong một partition, không có thứ tự thời gian toàn cục giữa topic/partition.
CSV replay cần sort trước; tốc độ phát mô phỏng không phải lưu lượng năm 2015.

Nguồn không có giá, tổng tiền đơn hàng, impressions quảng cáo hay attribution;
không thể suy ra doanh thu/AOV/CTR chuẩn. `category_tree.csv` có
`categoryid,parentid`, không có item→category. Gói không dùng tên/ảnh sản phẩm
hoặc review giả như thuộc tính nguồn thật. Fixture tổng hợp phục vụ kiểm thử
được gắn nhãn tách khỏi dữ liệu RetailRocket.

## Streaming: validation, state và snapshot

Schema JSON explicit giúp tránh drift do suy luận kiểu. Spark tính
`event_time=timestamp_millis(timestamp)` ở UTC. Dòng sai schema/ID/topic, payload
lỗi hoặc purchase thiếu transaction ID được ghi quarantine cùng lý do và
Kafka metadata. Clean giữ schema và metadata truy vết nguồn.

Dedup dùng watermark 10 phút và `dropDuplicatesWithinWatermark(event_id)`.
ID mới nhưng event quá cũ vẫn có thể bị loại; các dòng bị watermark loại được
phản ánh trong progress/state metrics, không tự được sao chép sang quarantine.
Dedup không phải cơ sở dữ liệu ID vĩnh viễn qua mọi run.

Clean Parquet là ranh giới bền vững. Window và session đọc clean bằng query
riêng thay vì nối nhiều stateful operator trong một query Spark 3.5. Mỗi sink
có checkpoint độc lập:

- Sliding window dài 5 phút, bước 1 phút; Delta `MERGE` cập nhật theo
  `visitorid,window_start,window_end` bằng giá trị tổng hiện tại.
- Session gap 30 phút; session streaming xuất sau khi watermark chốt và
  `MERGE` theo `visitorid,session_start,session_end`.
- Các đặc trưng gồm số event theo loại, interaction score, item distinct xấp
  xỉ, thời điểm đầu/cuối và `purchase_per_view`; session thêm thời lượng hoạt động.

Watermark tiến theo event time đã quan sát, không tiến chỉ vì chờ ngoài đời.
Replay hữu hạn thường còn phần đuôi window/session chưa chốt.
`phase3.snapshot` tính batch trên clean được chấp nhận, ghi riêng `snapshots/`
với nhãn `is_finite_snapshot`; snapshot không khôi phục dòng đã bị watermark
loại và không đồng nghĩa kết quả session streaming đã finalized. Một event
có thể thuộc nhiều sliding window; không cộng mọi window để suy ra tổng event.

## Delta, HDFS và các phép chiếu NoSQL

Trong Compose, `LAKE_BASE_URI=hdfs://namenode:8020/ecommerce/runs` tạo các bảng:

```text
hdfs://namenode:8020/ecommerce/runs/<run_id>/delta/
  bronze/
  silver/
  gold_interactions/
  acid_checks/<uuid>/
```

Khi không đặt `LAKE_BASE_URI`, đường lake là `outputs/runs/<run_id>/delta/` để
kiểm thử cục bộ. Clean, quarantine, checkpoint, feature streaming, model và
evidence vẫn ở bind mount `outputs/runs/<run_id>/` trên máy chạy.

Bronze lấy accepted clean, MERGE chỉ thêm event ID chưa có. Silver giữ sự kiện
hợp lệ, thêm event time/date và điểm tương tác; review được giữ với điểm 0.
Gold chỉ tổng hợp sự kiện có điểm dương theo user-item, có
`interaction_score` và `count`. Silver/Gold ghi snapshot xác định theo run.
Delta commit là nguyên tử **từng bảng**; các bảng và NoSQL không nằm trong
một distributed transaction.

MongoDB database `bigdata` lưu:

| Collection | Khóa duy nhất/phương thức |
| --- | --- |
| `events` | `(run_id,event_id)`; `$setOnInsert` + upsert |
| `user_features` | `(run_id,visitorid)`; `$set` snapshot lịch sử/seen/window |
| `item_features` | `(run_id,itemid)`; `$set` tổng hợp lịch sử |
| `sessions` | `(run_id,visitorid,session_id)`; upsert và bỏ ID projection cũ khi sự kiện trễ nối session |
| `model_registry` | `(run_id,model_version)`; phiên bản model và metadata |

Redis dùng namespace `bigdata:<run_id>:` cho seen SET, features, window,
session, stream_window, final_session và active_model. Live projection đánh
dấu `provisional=true`: session có thể đổi nếu nhận thêm sự kiện hợp lệ.
`final_session` lấy từ đầu ra session streaming đã chốt, tách khỏi session
provisional; window gần nhất của projection neo vào event cuối của user,
không được diễn giải là 5 phút theo đồng hồ hiện tại.

Mongo facts được ghi trước, projection được tính lại từ facts và ghi SET thay
vì cộng dồn. `foreachBatch` có thể chạy lại; retry hội tụ khi giữ một writer
và cùng nguồn. Có thể nhìn thấy cập nhật từng phần nếu một kho lỗi giữa các
bước; không khẳng định exactly-once xuyên Delta/Mongo/Redis.

`pipeline.acid_check` dùng namespace kiểm tra UUID riêng: ghi bảng, UPDATE,
đọc version cũ, thêm CHECK constraint, thử append chứa dòng vi phạm và xác
minh không có partial commit. Nó không đo concurrent-writer isolation hoặc
tính bền vững sau hỏng toàn cụm.

## Nhánh ALS ban đầu: MLlib và đánh giá theo thời gian

Phần này mô tả `pipeline/train.py` và runner Phase 1–7 ban đầu, **không phải
thuật toán chính của snapshot strength đang chọn**. Chạy runner/retrain ALS
có thể kích hoạt một run ALS mới; Airflow hiện không tự chạy chuỗi thí nghiệm
strength hoặc tự chọn lại graph champion.

Training đọc Delta Silver, dedup trước khi chia thời gian; ID user/item phải
là số nguyên không âm int32. Chỉ view/cart/purchase tham gia ALS. Mỗi phần
train tự tổng hợp count và rating `view+3*cart+5*purchase`. Pipeline lưu
`VectorAssembler(view_count,cart_count,purchase_count)` cùng ALS implicit;
ALS dùng user ID/item ID/rating, không dùng vector assembler làm features.

Các mốc timestamp xấp xỉ phân vị 50%, 70%, 85% được lấy **trước tổng hợp
user-item**. Hai rolling fold lần lượt train≤50%/validate(50%,70%] và
train≤70%/validate(70%,85%]. Chọn tham số theo NDCG@K trung bình, phụ Recall@K;
train≤85% và test>85% chỉ dùng đánh giá cuối. Cold user/item được tính miss,
seen item bị loại khỏi đích ranking. Chỉ số gồm Precision/Recall/NDCG@K,
catalog/user coverage; không báo RMSE trên implicit feedback.

Sau khi đóng băng test metrics, model phục vụ được refit trên mọi sự kiện
đã quan sát. `metric_model_version` và `serving_model_version` phân biệt hai
mô hình. Recommendations toàn user được ghi Parquet, JSON xuất mặc định tối
đa 5.000 user; SQLite giữ seen của toàn bộ user đã quan sát. User ngoài phạm
vi JSON dùng popularity và vẫn lọc seen. Điểm xếp hạng không phải xác suất mua.

API tải bundle có kiểm tra schema/version; ghi model/index xong trước bundle,
đổi tên nguyên tử file JSON và chỉ kích hoạt latest sau toàn pipeline.
Cache gợi ý Redis theo run/model/user/k có TTL 60 giây. Cache hit vẫn lọc lại
seen từ bundle/SQLite/Mongo/Redis; dịch vụ hỏng được báo degraded, không giả
định lịch sử online luôn mới. Dashboard lấy KPI/model của đúng run, hiển thị
visitor purchase conversion theo distinct visitor; CTR/doanh thu để `null`.

## Điều phối và kiểm chứng

Airflow là profile `orchestration`, SequentialExecutor/SQLite cho demo. DAG
`retailrocket_daily_retrain` chạy lúc 02:00 Asia/Ho_Chi_Minh, `catchup=False`,
một active run. DAG gửi `request_id` và `quick` tới typed worker nội bộ,
không mount Docker socket hoặc nhận shell command tùy ý. Worker chỉ cho một
job active, lưu trạng thái và dùng request ID băm để chống submit trùng.
Worker retrain từ clean đã commit của latest run; lịch không tự ingest nguồn
mới. Khi worker restart giữa job, cần kiểm tra job/khóa pipeline rồi trigger
request ID mới; không tự coi job đang dở là đã hoàn thành.

Các bản ghi cần dùng khi báo cáo kết quả:

- `metrics/producer.json`, `kafka_verify.json`: ack và consumer đúng run.
- Progress Spark: offset, batch time, watermark và state metrics.
- `kpis.json`, `online_publish.json`, `acid_check.json`: lake, projections và
  kiểm tra Delta của đúng run.
- `model_metrics.json`: fold/grid, heldout, cold-start, phạm vi model phục vụ.
- `run_report.json`, `e2e_verification.json`: trạng thái toàn pipeline và đối
  chiếu API/store/model.
- `benchmark.json`: HTTP latency/requests per second với số request,
  concurrency và môi trường ghi rõ; không gọi nó là Kafka→API latency hoặc SLA.

Tham chiếu đóng góp gốc nằm trong `references/phase1` và `references/phase4`.
Không chạy các Compose cũ song song để ghép; Compose thống nhất đã quy định
phiên bản, đường dẫn và namespace cho toàn bộ Phase 1–7.

