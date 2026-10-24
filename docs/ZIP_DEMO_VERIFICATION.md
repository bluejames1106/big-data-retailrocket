# Kiểm tra demo từ ZIP ngày 24/10/2026

## Kết quả và phạm vi

**Đạt kiểm tra snapshot demo.** Tải trực tiếp asset `storefront-v2` từ GitHub,
giải nén vào thư mục mới, dựng image từ đúng source trong ZIP, dùng project
Compose mới và MongoDB/Redis volume mới. Không lấy snapshot từ thư mục demo cũ.

Đây là kiểm tra **snapshot → FastAPI → cửa hàng**, không phải chạy mới toàn bộ
Kafka, Spark, HDFS, Delta ACID hoặc Airflow. Docker Desktop đã có sẵn, dùng lại
image/layer cache phụ thuộc khi build; chưa phải phép thử cài đặt trên máy hoặc
Docker engine hoàn toàn mới. Không đo chất lượng lại toàn bộ tập test, không
huấn luyện lại graph phục vụ.

| Hạng mục | Kết quả |
| --- | --- |
| Nguồn ZIP | [GitHub Release storefront-v2](https://github.com/bluejames1106/big-data-retailrocket/releases/tag/storefront-v2) |
| Tên / kích thước | `big_data_update_2_storefront_v2.zip` / 66.691.379 byte |
| SHA-256 | `0bcab83532482f9934078c525c2be51eb44ded50aae1a8f2f8fc2e0a41f74fa9` |
| Manifest | 191/191 payload khớp hash, kiểm tra trước giải nén |
| Thư mục mới | `D:\BIGDATA\zip-check-20261024\big_data_update_2` |
| Project / URL kiểm tra | `retailrocket-zipcheck20261024` / `http://localhost:8002/` |
| Image | `retailrocket-zipcheck:20261024`, build từ thư mục mới |
| Snapshot | `strength-20261022-v1`, không stale |
| Phụ thuộc | MongoDB và Redis báo `ok`; API `ready` |
| Hồi quy session | 59 kiểm thử đạt |
| Hồi quy service | 17 kiểm thử đạt |
| HTTP | 100/100 yêu cầu thành công, concurrency 4, 3 warmup |
| Độ trễ HTTP host → Docker | mean 194,54 ms; p95 242,36 ms; p99 290,66 ms |
| Bảo toàn dữ liệu/model | 7 artifact quan trọng giữ nguyên hash trước/sau |

Độ trễ thuộc workload hai seed cố định trên máy này, không phải SLA hoặc tốc
độ Kafka → API. Khởi động Docker ban đầu gặp socket tạm còn sót từ lần tắt
trước; đã giữ lại thư mục socket cũ và khởi động lại, không đổi cấu hình đĩa,
network hoặc dữ liệu model.

## Đã kiểm tra chức năng nào?

- Trang cửa hàng, `/docs`, OpenAPI, JavaScript, CSS và ảnh minh họa trả HTTP 200.
- KPI đọc được, run/model đúng pointer, phiên bản graph đánh giá tách với phục vụ.
- Phiên rỗng nhận popularity; xem món làm thay đổi gợi ý; mua mô phỏng cập nhật
  context; request độc lập không chia sẻ context.
- Top 10 đủ và không trùng; không trả ID trong context; visitor có lịch sử
  được lọc bằng toàn bộ seen trong SQLite.
- Timestamp tương lai bị từ chối bằng HTTP 422.
- Lượt bấm không thay đổi KPI/metrics lịch sử, CSV, pointer, bundle, hai SQLite
  hoặc báo cáo validation/test.
- Giao diện thực tế: nhập ID 129960, chọn xem, kết quả đầu là
  `238677, 336437, 75643`; 129960 bị lọc. Đây là graph full, không dùng để chấm test.

[Bằng chứng HTTP và checksum](../reports/verification/2026-10-24/http-verification.json),
[manifest ZIP](../reports/verification/2026-10-24/archive-verification.json),
[log build](../reports/verification/2026-10-24/image-build.log),
[59 kiểm thử session](../reports/verification/2026-10-24/session-tests.log),
[17 kiểm thử service](../reports/verification/2026-10-24/service-tests.log).
Xem [ví dụ dữ liệu thật](REAL_DATA_EXAMPLE.md) cho phép tái tạo graph train.

## Lệnh chạy tương đương để kiểm tra độc lập

Giải nén vào thư mục mới. Chọn project Compose chưa tồn tại để không dùng lại
volume cũ. PowerShell tại thư mục có `compose.yaml`:

```powershell
docker build -t retailrocket-zipcheck:20261024 .
$env:APP_PORT = '8002'
$zipProject = 'retailrocket-zipcheck20261024'
# Override image, không sửa source hoặc launcher trong ZIP.
@'
services:
  api:
    image: retailrocket-zipcheck:20261024
  app:
    image: retailrocket-zipcheck:20261024
'@ | Set-Content compose.zipcheck.yaml -Encoding utf8
docker compose -p $zipProject -f compose.yaml -f compose.zipcheck.yaml up -d --wait mongodb redis
docker compose -p $zipProject -f compose.yaml -f compose.zipcheck.yaml up -d --no-deps --wait api
Invoke-RestMethod http://127.0.0.1:8002/health/ready
Remove-Item Env:APP_PORT
```

Chỉ khác lệnh launcher đóng gói ở tên project, tag image và port để cô lập lần
thử. Không chỉnh thuật toán hoặc các file phục vụ. Có thể dùng launcher gốc
để demo thường ngày như hướng dẫn [README](../README.md).

Tại repository GitHub mới, chạy verifier stdlib Python (không cần cài SciPy):

```powershell
python scripts/verify_zip_demo.py `
  --project-root D:\BIGDATA\zip-check-20261024\big_data_update_2 `
  --base-url http://127.0.0.1:8002 `
  --output reports/verification/my-http-verification.json
```

Thay đường dẫn và port theo máy. Để chạy đúng bộ hồi quy đã thử:

```powershell
docker run --rm --network none retailrocket-zipcheck:20261024 python -m unittest discover -s tests -p 'test_session*.py' -v
docker run --rm --network none retailrocket-zipcheck:20261024 python -m unittest discover -s tests -p test_service.py -v
```

Không dùng script retrain/runner Phase 1–7 chỉ để bật cửa hàng: các lệnh đó
có thể tạo và kích hoạt model khác với snapshot đang báo cáo.
