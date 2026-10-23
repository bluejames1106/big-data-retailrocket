# Kết quả vượt baseline ở cả ba metric — 22/10/2026

Run **neighbors-20261022-1233**, trạng thái **completed**, quality gate **passed**, đã được kích hoạt cho demo http://localhost:8000/. Bản hybrid trước được giữ tại outputs/runs/hybrid-20261022-1207.

## Kết quả chính: giữ nguyên 1.000 người dùng

| Chỉ số | Baseline phổ biến 7 ngày | Item-item mới | Tăng tương đối |
|---|---:|---:|---:|
| Precision@10 | 0.1000% | **0.1400%** | +40.00% |
| Recall@10 | 0.7450% | **0.8033%** | +7.83% |
| NDCG@10 | 0.4680% | **0.5563%** | +18.86% |

Hit tăng từ **10 lên 14**, trên cùng 10.000 vị trí top-10. So với bản hybrid trước, ba điểm lần lượt tăng từ 0,1100% / 0,6783% / 0,3967% lên 0,1400% / 0,8033% / 0,5563%.

Đây vẫn là cùng 1.000/231.547 user test đủ điều kiện, cùng 910 cold user, 86 cold item-pair, 1.415 novel pair, cùng K=10, cùng cut thời gian và cùng catalog train 218.525 item. Không chuyển sang warm-user-only, không đổi K hoặc nhãn relevant, không lấy mẫu negative item. Baseline 7 ngày giữ nguyên định nghĩa từ lượt trước và được tính lại trên đúng cohort này.

## Kiểm tra phụ: 5.000 người dùng không trùng cohort chính

| Chỉ số | Baseline phổ biến 7 ngày | Item-item mới | Tăng tương đối |
|---|---:|---:|---:|
| Precision@10 | 0.1240% | **0.1720%** | +38.71% |
| Recall@10 | 1.0517% | **1.3025%** | +23.84% |
| NDCG@10 | 0.6390% | **0.8318%** | +30.18% |

Hit tăng từ **62 lên 86**, trên 50.000 vị trí. Giữ cả **4630 cold user**. Cohort được đăng ký trước bằng hash visitorid + seed43, loại toàn bộ 1.000 user chính; không dùng cohort phụ để chọn tham số. Đây là một cohort người dùng khác trong **cùng giai đoạn test**, không phải dữ liệu thời gian tương lai mới.

## Mức chắc chắn của kết quả

| Cohort | Metric | Chênh lệch (điểm %) | Khoảng bootstrap 95% (điểm %) |
|---|---|---:|---:|
| 1000 user | Precision@10 | +0.0400 | [-0.0100; +0.1100] |
| 1000 user | Recall@10 | +0.0583 | [-0.2000; +0.3500] |
| 1000 user | NDCG@10 | +0.0883 | [-0.1527; +0.3601] |
| 5000 user | Precision@10 | +0.0480 | [+0.0220; +0.0760] |
| 5000 user | Recall@10 | +0.2507 | [+0.1064; +0.4115] |
| 5000 user | NDCG@10 | +0.1928 | [+0.0905; +0.3139] |

Khoảng của cohort chính còn chứa 0: 14 hit là ít để khẳng định mức tăng ổn định. Trên cohort phụ, khoảng bootstrap 95% riêng từng metric đều dương. Đây là khoảng percentile bootstrap theo user, 1.000 lần lấy lại mẫu, seed42; không phải khoảng đồng thời cho cả ba metric hoặc bằng chứng về mọi giai đoạn tương lai. Không dùng các khoảng này để đổi mô hình sau khi xem test.

**Kết luận có giới hạn:** mục tiêu vượt baseline ở cả ba điểm đã đạt trên cả hai cohort đã đăng ký. Mức điểm tuyệt đối vẫn thấp vì dữ liệu thưa và phần lớn user là cold. Chưa có cơ sở tuyên bố chất lượng production hoặc hiệu quả kinh doanh/CTR.

## Mô hình đã thay đổi thế nào?

Cấu hình được validation chọn là **item-item kNN dùng Jaccard**, min cooccurrence=1, trọng số item-item=1, ALS=0. ALS được giữ làm đối chứng; không gọi mức tăng này là ALS đã tự tốt lên.

- Từ toàn bộ lịch sử trước mỗi cutoff, lấy tối đa **20 item phân biệt gần nhất cho từng user train** để hạn chế chi phí và tác động của user xem quá nhiều. Đây là lựa chọn mô hình được ghi trước, không lọc user test.
- Đếm số user cùng tương tác hai item. Similarity: `Cij/(Ci+Cj-Cij) × Cij/(Cij+3)`; bỏ self-edge.
- Lấy tối đa **5 item gần nhất trong lịch sử quá khứ** của user cần gợi ý; seed affinity lần lượt 1, 0.8, 0.64... Mỗi seed giữ 100 hàng xóm tốt nhất.
- Cộng điểm similarity theo seed affinity, loại tất cả item đã thấy rồi xếp hạng. Nếu không có tín hiệu item-item hoặc thiếu ứng viên, dùng popularity 7 ngày, mỗi visitor có một phiếu/item.
- Rank fusion giữ các tham số đã đăng ký; cấu hình thắng không dùng ALS hoặc trộn pop khi đã có ranking item-item. Pop vẫn là fallback bắt buộc cho user cold/thiếu tín hiệu.
- Toàn bộ nguồn **2.756.101 dòng**, sau dedup **2.755.641 event** được đọc. Graph dùng last-20/item cap và pop dùng cửa sổ 7 ngày; không nói mọi event đều đóng góp như nhau hoặc vẫn là ALS weighted-count optimization.

## Vì sao cách đánh giá vẫn công bằng?

1. Kế hoạch được ghi trước trong docs/NEIGHBORHOOD_EXPERIMENT_PLAN.json và sao vào từng run: family, baseline, cohort chính/phụ, objective và promotion gate.
2. Thử **14 cấu hình** gồm baseline, ALS đối chứng và 12 cấu hình graph/fusion. Chỉ hai fold thời gian trước test được dùng chọn cấu hình. Cut 50/70/85% không đổi; feature chỉ dùng quá khứ.
3. Objective là tối đa hóa mức tăng nhỏ nhất trong ba metric validation so với baseline; tie-break bằng NDCG/Recall/Precision. Không chỉ tối ưu một điểm rồi bỏ qua hai điểm còn lại.
4. Validation đã thắng baseline cả ba: tỷ lệ P/R/NDCG = **1,5909 / 1,3516 / 1,4076**. Policy_selection.json được lưu trước khi chấm test.
5. Chỉ chấm cấu hình được chọn, một lần trên mỗi cohort; không chọn cấu hình khác theo kết quả test. Promotion gate đã đăng ký yêu cầu cả validation và test chính thắng baseline cả ba metric.
6. Test chính vốn đã từng được xem trong các lượt phát triển trước; không gọi nó là benchmark chưa từng thấy. Cohort phụ disjoint được chốt trước lần chạy này, nhưng vẫn cùng thời kỳ RetailRocket.
7. Lưu **primary_test_rankings.jsonl và secondary_test_rankings.jsonl** với actual/seen/predictions/baseline từng user. scripts/verify_neighborhood.py tính lại độc lập các điểm, hit, hash cohort, tính disjoint và điều kiện chọn/kích hoạt. Metric là macro-average theo user, không dùng micro `hits/tổng target` để thay Recall.
8. Giữ exact module đã chạy và SHA-256 của mã/kế hoạch trong evidence để đối chiếu; làm rõ metadata sau chạy chỉ tách mô hình mới khỏi ALS đối chứng, không đổi ranking hoặc metric.

## Demo và giới hạn phục vụ

- Demo: **http://localhost:8000/**; tên **Item-item + dự phòng**, run `neighbors-20261022-1233`. Thử user 1879 hoặc 2194; user 2147483647 nhận popularity fallback có nhãn.
- API cache 100 gợi ý cho **1.000 user hoạt động nhiều nhất**; user ngoài cache, kể cả warm user, nhận pop fallback. Metric đo khả năng mô hình với lịch sử quá khứ trong protocol, không tuyên bố API local cá nhân hóa sẵn toàn bộ 1,4 triệu user.
- SQLite vẫn lọc đầy đủ **2.145.179 cặp user-item** quan sát. Không replay full Kafka hoặc bulk toàn bộ fact/session vào MongoDB; đây là nhánh offline model, Mongo/Redis giữ registry/cache.
- Delta ACID evidence được kế thừa từ run full đã pass; lượt đổi mô hình không viết lại Silver và không tuyên bố đã chạy lại ACID test.

## Kiểm tra và chạy lại

**6 kiểm tra mô hình bằng Spark thật + 17 kiểm tra API** đã pass. Kiểm toán độc lập, cùng-cohort/source/version, model registry và bộ lọc seen/duplicate đã pass. Benchmark **100/100 request**, concurrency4, p95 **283.92 ms**, mean **238.33 ms**; phép đo local, không phải SLA production.

Trên máy hiện tại không cần train lại. Sau khi mở Docker:

```powershell
cd D:\BIGDATA\BigData_Phase_1_7
docker compose up -d --wait mongodb redis
docker compose up -d --no-deps --wait api
```

Tái chạy study từ full model hiện có:

```powershell
.\scripts\run_neighborhood_upgrade.ps1
```

Trên máy mới từ ZIP nguồn, chạy full trước rồi chạy study:

```powershell
.\scripts\run_full_training.ps1
.\scripts\run_neighborhood_upgrade.ps1
```

Nếu một lượt sau không vượt gate, script giữ bản demo trước. ZIP chỉ chứa source/CSV/báo cáo; runtime graph/model/SQLite và Docker volume ở outputs của máy đang chạy, không đi kèm ZIP.

Bằng chứng: outputs/runs/neighbors-20261022-1233/metrics/; bản sao để chia sẻ: example_results/neighbors-20261022-1233/. [Tham khảo item cooccurrence/SAR](https://github.com/recommenders-team/recommenders/blob/main/contrib/sarplus/README.md); [nghiên cứu temporal leakage](https://arxiv.org/abs/2010.11060). Đây là triển khai item-item riêng, không tuyên bố chạy nguyên package SAR của Microsoft.

![Demo item-item và bằng chứng so sánh](../example_results/neighbors-20261022-1233/evidence/dashboard-neighborhood.png)
