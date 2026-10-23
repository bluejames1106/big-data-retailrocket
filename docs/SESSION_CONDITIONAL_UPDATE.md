# Thí nghiệm cải tiến gợi ý theo phiên

Ngày 22/10/2026, chọn `conditional_user` cho snapshot demo `conditional-20261022-v1`. Chỉ nhánh gợi ý theo phiên đổi graph; gợi ý static và dữ liệu nguồn giữ theo bundle trước.

## Phương pháp

- Đọc 2.756.101 dòng CSV, khử 460 dòng trùng theo các trường tạo event ID, còn 2.755.641 sự kiện. Hash cohort ở cả hai fold validation và test trùng với lần đánh giá gốc.
- Dùng cùng graph người dùng nhị phân, cap 20 món/entity, top 100 hàng xóm/seed và shrinkage 3. Công thức cũ: Jaccard `co / (freq(seed) + freq(candidate) - co)`. Công thức mới: `co / freq(seed)`. Cả hai nhân thêm `co / (co + 3)`.
- Thử một số cấu hình similarity/shrinkage trên hai fold validation, chọn `conditional/3` vì Recall@10 và NDCG@10 đều tăng ở cả hai. Chấm test cuối một lần sau khi chọn.
- Phục vụ refit graph trên toàn bộ lịch sử, chỉ dùng kết quả graph trước cutoff để tính các chỉ số test. API đã kiểm tra trên cổng riêng rồi mới chuyển `outputs/latest_run.json`.

| Tập | Jaccard đúng/5.000 | Conditional đúng/5.000 | Jaccard NDCG@10 | Conditional NDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| Validation fold 1 | 1.294 | 1.323 | 16,28% | 17,00% |
| Validation fold 2 | 1.434 | 1.470 | 17,35% | 18,58% |
| Test lịch sử | 1.350 | 1.439 | 17,02% | 18,37% |

Trên test lịch sử, Precision@10 tăng từ **2,70% lên 2,878%** và Recall@10 từ **27,00% lên 28,78%**. Chênh lệch Recall có khoảng tin cậy bootstrap 95% theo cụm visitor khoảng **+1,18 đến +2,34 điểm phần trăm**. Đây là khoảng tin cậy cho phép so sánh này, không sửa được việc test cũ đã được xem trong quá trình phát triển. Cần một giai đoạn dữ liệu tương lai mới trước khi kết luận chất lượng tổng quát hoặc tác động kinh doanh.

Kiểm toán lỗi cũng cho thấy 4.996/5.000 truy vấn chỉ có một món trong ngữ cảnh. Tăng top 100 lên top 200 hàng xóm giúp tăng candidate recall, nhưng không đổi top 10; tăng cap 20 lên 50 làm top 10 giảm nhẹ. Các phương án đó không được đưa vào phục vụ.

## Bằng chứng và vận hành

- `outputs/evidence/session_miss_diagnosis.json`: phân nhóm lượt trượt của mô hình cũ.
- `outputs/evidence/session_coverage_experiment.json`: thử cap và neighbor limit trên validation.
- `outputs/evidence/session_similarity_experiment.json`: so sánh công thức trên validation.
- `outputs/evidence/session_conditional_heldout.json`: phép chấm test và bootstrap.
- `outputs/runs/conditional-20261022-v1/`: bundle, SQLite graph, báo cáo và lineage của snapshot mới.
- `outputs/evidence/demo-conditional-20261022-v1-20261022-200007-smoke.json`: kiểm tra API sau khi kích hoạt.

Để quay lại demo Jaccard cũ, đổi `outputs/latest_run.json` sang nội dung trong `outputs/evidence/latest_run_before_conditional.json`, rồi chạy `scripts/run_session_demo.ps1 -SkipBuild`. Không dùng điểm của graph refit toàn lịch sử để tự chấm lại test cũ.
