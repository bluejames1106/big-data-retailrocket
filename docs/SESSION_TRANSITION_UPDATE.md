# Cải tiến gợi ý món tiếp theo trong phiên

Ngày 22/10/2026, demo chọn `transition-20261022-v1`. Mô hình kết hợp 75% xếp hạng item-item có điều kiện với 25% xếp hạng chuyển tiếp theo phiên bằng reciprocal-rank fusion. Chỉ nhánh gợi ý theo phiên thay đổi; gợi ý static giữ nguyên.

## Dữ liệu và phép đo

- Đọc toàn bộ 2.756.101 dòng CSV; khử trùng lặp còn 2.755.641 sự kiện. Hash của các cohort validation và test trùng với lần chấm gốc.
- Quan hệ chuyển tiếp chỉ học các cặp ở **hai nhóm timestamp kế tiếp trong cùng phiên**. Các món ở cùng timestamp không dự đoán lẫn nhau. Món đích phải mới so với lịch sử trước đó của phiên. Với mỗi seed, giữ tối đa 100 món chuyển tiếp thường gặp nhất.
- Chọn trọng số 25% trên hai fold validation theo thời gian, rồi chấm một lần trên tập test lịch sử. Graph đánh giá chỉ học dữ liệu trước cutoff; graph phục vụ mới được refit trên toàn bộ lịch sử sau khi chấm.

| Tập | Bản trước đúng/5.000 | Bản mới đúng/5.000 | NDCG@10 trước | NDCG@10 mới |
| --- | ---: | ---: | ---: | ---: |
| Validation fold 1 | 1.323 | 1.365 | 17,00% | 17,91% |
| Validation fold 2 | 1.470 | 1.513 | 18,58% | 19,21% |
| Test lịch sử | 1.439 | 1.502 | 18,37% | 19,21% |

Trên test lịch sử, Precision@10 tăng **2,878% → 3,004%**, Recall@10 **28,78% → 30,04%**, NDCG@10 **18,37% → 19,21%**. Chênh lệch Recall có khoảng tin cậy bootstrap 95% theo cụm visitor khoảng **+0,70 đến +1,83 điểm phần trăm**. Tập test này đã từng được xem trong quá trình phát triển, nên cần thêm dữ liệu ở giai đoạn tương lai mới trước khi kết luận mức tăng sẽ tổng quát hóa hoặc tạo tác động kinh doanh.

## Bằng chứng và vận hành

- `outputs/evidence/session_transition_validation.json`: chọn trọng số trên hai fold.
- `outputs/evidence/session_transition_heldout.json`: kết quả test lịch sử và bootstrap.
- `outputs/runs/transition-20261022-v1/`: bundle, SQLite graph chứa cả `neighbors` và `transitions`, báo cáo và lineage.
- `outputs/evidence/demo-transition-20261022-v1-20261022-202036-smoke.json`: smoke test API chính sau kích hoạt.
- 22 kiểm thử cho phần session, transition và API đều đạt; chỉ mục SQLite `quick_check=ok` với 454.978 cạnh chuyển tiếp được lưu.

Để quay lại snapshot trước, chép nội dung `outputs/evidence/latest_run_before_transition.json` vào `outputs/latest_run.json`, rồi chạy `scripts/run_session_demo.ps1 -SkipBuild`. Các snapshot cũ không bị ghi đè.
