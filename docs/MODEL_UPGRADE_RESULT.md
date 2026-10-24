# Kết quả cải thiện mô hình ngày 22/10/2026

> **Báo cáo lịch sử của nhánh ALS/hybrid**, không mô tả snapshot đang chọn.
> Các câu “demo hiện tại”, đường dẫn và lệnh bên dưới thuộc thời điểm thí nghiệm
> này. Để chạy bản strength hiện nay, xem [README](../README.md),
> [kiến trúc hiện tại](ARCHITECTURE.md) và [kiểm tra ZIP](ZIP_DEMO_VERIFICATION.md).

Run **hybrid-20261022-1207** đã completed; demo/API/registry/seen-filter đã được kiểm tra thật và passed. Đây là bản cập nhật sau full-20261022-1031; bản cũ được giữ nguyên.

## Chỉ số thế nào là ổn?

Không có một ngưỡng Precision/Recall/NDCG phù hợp cho mọi dự án. Cần cùng K, cùng cohort, cùng nhãn liên quan, cùng cách chia thời gian và cùng catalog. Mốc thực tế đầu tiên cho dự án này là thắng một baseline phổ biến đơn giản, rồi kiểm tra trên nhiều khoảng thời gian và đủ người dùng; không chỉ nhìn xem điểm có khác 0.

- Precision@10: tỷ lệ vị trí top-10 trúng sản phẩm mới người dùng thực sự tương tác trong test. Ví dụ 1% nghĩa trung bình 0,1 sản phẩm trúng mỗi danh sách 10; đây là minh họa cách đọc, không phải chuẩn đạt.
- Recall@10: với từng user, tìm được bao nhiêu trong các sản phẩm test liên quan, rồi lấy trung bình user.
- NDCG@10: ngoài trúng sản phẩm, thưởng nhiều hơn khi sản phẩm trúng nằm ở đầu danh sách. Không phải xác suất mua hay độ chính xác phân loại.

Điểm cũ **thấp**: Precision 0,03% chỉ có 3 hit trên 10.000 vị trí chấm. Không thể gọi hệ thống tốt chỉ vì số điểm khác 0.

## Đã thay đổi gì?

1. Giữ ALS implicit đã học dữ liệu full, thay vì tiếp tục tăng rank một cách mù quáng khi phần lớn test là cold user.
2. Học độ phổ biến bằng **số visitor phân biệt mỗi item**, không để một user xem lặp làm item tăng điểm quá mức.
3. So sánh 12 chính sách: lịch sử 7 ngày / 30 ngày / toàn lịch sử, với bốn trọng số ALS 0 / 0,5 / 0,8 / 1; RRF dùng hằng số 60. Chỉ hai fold validation lịch sử tham gia lựa chọn.
4. Validation chọn **ALS weight=1, popularity window=7 ngày**: giữ thứ hạng ALS cho user có vector; user cold nhận độ phổ biến 7 ngày. Đây là chính sách hybrid theo tình trạng user; lựa chọn không trộn thêm độ phổ biến vào thứ hạng ALS của warm user.
5. Chấm đúng cả phương án dự phòng, giữ người dùng cold trong mẫu số. Lưu riêng baseline và ALS thuần để đối chiếu; không thay metric bằng điểm warm-user đẹp hơn.
6. Export tối đa 100 gợi ý cho mỗi user trong 1.000 user cache thay vì 10; truy vấn k=20 của những user này có thể dùng tiếp danh sách ALS đã tính. Toàn bộ 2.145.179 cặp seen vẫn được lọc bằng SQLite, gồm cả user ngoài cache.
7. Dashboard hiển thị đúng tên thuật toán, model version và điểm baseline; cache Redis giữ đúng nhãn nguồn hybrid.

## Kết quả test cùng cohort và K=10

| Chỉ số | ALS cũ | Baseline phổ biến 7 ngày | Chính sách được validation chọn |
|---|---:|---:|---:|
| Precision | 0,0300% | 0,1000% | **0,1100%** |
| Recall | 0,1333% | **0,7450%** | 0,6783% |
| NDCG | 0,0588% | **0,4680%** | 0,3967% |

So với ALS cũ: Precision tăng **3.67 lần**, Recall **5.09 lần**, NDCG **6.75 lần**. Số hit tăng từ **3 lên 11**, trên cùng 10.000 vị trí. Khoảng 8 hit bổ sung đến từ giải quyết cold-start; metric warm-user vẫn như ALS cũ. Đây không phải bằng chứng ALS latent factors tự cải thiện.

**Đánh giá thẳng:** cả ba điểm cao hơn bản cũ nhưng vẫn thấp. Chính sách hybrid chưa vượt baseline về Recall và NDCG trên test. Validation đã chọn hybrid vì NDCG validation cao nhất; không đổi sang baseline sau khi xem test để làm đẹp kết quả. Với chỉ 1.000 user và 11 hit, không tuyên bố khác biệt có ý nghĩa thống kê hoặc chất lượng production.

## Tính công bằng và giới hạn

- Cùng source Delta version 0, đủ **2.755.641 event sạch** từ 2.756.101 dòng raw; ALS evaluation/serving factors cũ được tái sử dụng, không huấn luyện lại factors cuối. Độ phổ biến được học lại theo khoảng thời gian tương ứng; toàn nguồn được đọc, không lấy CSV sample.
- Cùng cut thời gian 50/70/85%, cùng 1.000/231.547 user test chọn bằng hash int visitorid + seed42, cùng 1.415 novel item-user target, cùng 910 cold user và 86 cold item-pair. Baseline ALS tính lại khớp đủ ba điểm cũ.
- Chỉ chọn tham số bằng validation; policy_selection.json được ghi trước test_comparison.json. Tuy nhiên đây là **test period đã từng được xem trong quá trình phát triển dự án**, không quảng bá như một benchmark mới hoàn toàn chưa thấy.
- Không negative sampling để làm tăng metric; ALS lấy top-100 ứng viên từ toàn catalog train, popularity học toàn catalog trong cửa sổ và đủ danh sách để lọc seen. Không giảm K, loại cold user hoặc chuyển nhãn target sau khi thấy điểm.
- Điểm chính mô tả policy với ALS factors cho user đã có trong train. API local chỉ cache 1.000 user hoạt động nhiều nhất; warm user ngoài cache cũng nhận popularity fallback. Không tuyên bố API đã cá nhân hóa toàn bộ 1,4 triệu user.
- Việc chọn baseline phổ biến là mốc học thuật cần thiết; không nên báo cáo chỉ mức tăng so với ALS yếu mà bỏ qua baseline đang cao hơn về hai metric.

## Kiểm tra và chạy demo

- 7 kiểm tra fusion/temporal bằng Spark thật: passed; bao gồm không dùng future event, một phiếu/visitor-item, metric giữ cold user, lọc seen và thứ hạng deterministic.
- 17 kiểm tra FastAPI/cache/lineage: passed, có cả hybrid/popularity cache source.
- Full verification: cùng cohort, tái tạo điểm ALS cũ, chọn argmax validation, API/metric/registry đúng phiên bản, không trả seen/duplicate item.
- Delta ACID: giữ bằng chứng passed của nguồn full; lượt đổi policy không ghi lại lake hoặc chạy lại ACID test.
- Benchmark thực: **100/100** yêu cầu thành công, concurrency4; p95 **345.73 ms**, trung bình **239.43 ms**. Không phải SLA production.

Demo hiện tại: **http://localhost:8000/**. Bấm Cập nhật dữ liệu hoặc tải lại trang nếu còn thấy bản full cũ. Model mới có tên ALS + dự phòng 7 ngày; thử user 1879 hoặc 2194, và thử 2147483647 để xem nhãn fallback cho user mới.

Trên máy này không cần train lại. Nếu khởi động lại Docker:

```powershell
cd D:\BIGDATA\BigData_Phase_1_7
docker compose up -d --wait mongodb redis
docker compose up -d --no-deps --wait api
```

Chạy lại nâng cấp từ full model đang có:

```powershell
.\scripts\run_model_upgrade.ps1
```

Trên máy mới, ZIP chỉ chứa mã nguồn/CSV/báo cáo, không chứa runtime model/volumes. Chạy lần lượt:

```powershell
.\scripts\run_full_training.ps1
.\scripts\run_model_upgrade.ps1
```

Bằng chứng trong `outputs/runs/hybrid-20261022-1207/metrics/`; bản sao để phân phối trong `example_results/hybrid-20261022-1207/`. Xem policy_validation_fold_1/2.json, policy_selection.json, test_comparison.json và model_metrics.json.

Định nghĩa metric tham khảo [Recommenders evaluation](https://recommenders-team.github.io/recommenders/evaluation.html). Các lưu ý so sánh cách tính tham khảo [Quality Metrics in Recommender Systems](https://arxiv.org/abs/2206.12858).

![Dashboard sau nâng cấp](../example_results/hybrid-20261022-1207/evidence/dashboard-hybrid.png)
