# Kết quả chạy full dữ liệu ngày 22/10/2026

Run: `full-20261022-1031`. Trạng thái **completed**; kiểm tra model/API và benchmark **passed**.

## Dữ liệu đã dùng

- Đọc đủ **2.756.101 dòng** events.csv, loại **460 dòng trùng**, còn **2.755.641 sự kiện sạch**; không lấy mẫu event train.
- **1.407.580 người dùng**, **235.061 sản phẩm**, **2.145.179 cặp user-item**.
- Xem: 2.664.218; thêm giỏ: 68.966; mua: 22.457.
- Full ở đây là toàn bộ lịch sử CSV trong nhánh offline: validate/dedup → Delta/HDFS → ALS → registry → API. Không phải replay toàn bộ Kafka hoặc ghi toàn bộ fact/session vào MongoDB/Redis. SQLite lưu seen history đầy đủ; MongoDB/Redis giữ model registry.

## Vì sao demo trước có 0/0/0?

Demo cũ chỉ có 9.999 sự kiện sạch. Trong 912 user test đủ điều kiện, **871 user (95,5%) chưa xuất hiện trong train**. ALS chưa có vector cho những user này; các user còn lại cũng không có hit top-10, nên ba điểm đều bằng 0. Ít dữ liệu không tự động gây điểm 0; full data cũng không bảo đảm độ chính xác cao.

## Đã thay đổi gì?

- ALS implicit rank 8 → 16, trọng số view/cart/purchase 1/3/5 được biến đổi `log1p` để giảm tác động xem lặp.
- Chọn regParam 0.05 trong hai cấu hình 0.05/0.1, alpha 20, bằng hai fold thời gian; 4 vòng validation và 8 vòng cho evaluation/serving.
- Chia thời gian trước aggregate. Model đánh giá học 2.341.051 event quá khứ; test gồm 414.590 event sau mốc 85%. Sau khi đóng băng điểm mới refit serving trên toàn bộ 2.755.641 event.
- Cache chỉ các cột ML cần thiết; cache điểm từng fold theo nguồn Delta/version/cấu hình để tiếp tục sau gián đoạn.
- Gợi ý ALS xuất sẵn cho **1.000 user hoạt động nhiều nhất**. Model factor vẫn học toàn bộ 1.407.580 user; user ngoài JSON nhận popularity fallback có nhãn, lọc seen qua SQLite toàn bộ lịch sử.

## Điểm test thật

| Chỉ số @10 | Demo cũ | Full hiện tại |
|---|---:|---:|
| Precision | 0% | 0,0300% |
| Recall | 0% | 0,1333% |
| NDCG | 0% | 0,0588% |

Tương ứng dạng số 0–1: precision=0.00030000000000000003, recall=0.0013333333333333333, ndcg=0.0005877748470213334.

Chấm **1.000/231.547 user test đủ điều kiện**, chọn bằng xxhash64(visitorid,42), quy tắc đã cố định trước khi thấy điểm test. Không lấy mẫu negative item; so với toàn catalog train. **910/1.000 user là cold user và vẫn được tính miss**. Các chỉ số còn thấp, chưa chứng minh chất lượng cao. Demo cũ và full khác thời gian/cohort/cấu hình; không thể quy toàn bộ thay đổi điểm cho một hyperparameter.

## Đã kiểm tra

- Full source/clean counts, số factor user/item và 2.145.179 cặp seen SQLite đúng.
- API/KPI/metric/model version khớp; thử 3 user ALS, một user quan sát ngoài JSON và một user mới: không trùng sản phẩm, không trả seen item.
- Registry MongoDB/Redis, Delta update/time travel/constraint rejection/no partial append: passed.
- 100/100 yêu cầu API thành công, concurrency 4; p95 **307.33 ms**, trung bình **237.83 ms**. Đây là phép đo local cho user được chọn, không phải cam kết SLA.
- 7 kiểm tra training đã qua, gồm ALS Spark thực, lưu/load, lọc seen, log-confidence và cohort hash giữ cold user. Log: outputs/evidence/full-cache-tests.log.

## Demo và chạy lại

Trên máy hiện tại, mở **http://localhost:8000/**, chọn Visitor **1879**, nhấn **Tạo gợi ý**. API docs: http://localhost:8000/docs.

Nếu Docker vừa được khởi động lại, mở PowerShell trong thư mục dự án và chạy:

```powershell
docker compose up -d --wait mongodb redis
docker compose up -d --no-deps --wait api
```

Huấn luyện một lượt full mới:

```powershell
.\scripts\run_full_training.ps1
```

Không cần huấn luyện lại để xem model đã hoàn tất trên máy này. Thư mục outputs/runs/full-20261022-1031/ giữ models, serving và metrics; example_results/full-20261022-1031/ chỉ chứa báo cáo và bằng chứng, không thay thế model chạy thật.

Docker đã được chuyển sang **D:\BIGDATA\DockerData** theo sự đồng ý của chủ máy, kiểm tra SHA-256 trước khởi động và giữ nguyên volume/image. C trống khoảng 23 GB sau chuyển. Bản VHD dự phòng nằm tại D:\BIGDATA\Docker_Backup_20261022; không đưa đĩa Docker vào ZIP dự án.

Bản ZIP cập nhật chứa mã nguồn Phase 1–7, CSV nguồn và báo cáo chạy full. Không chứa Docker volumes, model runtime hoặc checkpoint; giải nén trên máy khác rồi chạy script full để tái tạo. File ZIP không mang theo cấu hình Docker toàn máy.

Tham khảo thuật toán: [Spark 3.5.6 ALS](https://spark.apache.org/docs/3.5.6/api/scala/org/apache/spark/ml/recommendation/ALS.html). Vị trí WSL dùng CustomWslDistroDir theo [hướng dẫn của Docker](https://github.com/docker/for-win/issues/14129).

![Dashboard và gợi ý thực tế](../example_results/full-20261022-1031/evidence/dashboard-full-data.png)
