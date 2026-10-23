# Cải tiến xếp hạng bằng độ mạnh liên kết

Ngày 22/10/2026, demo chọn `strength-20261022-v1`. Graph item-item có điều kiện và graph chuyển tiếp vẫn học từ toàn bộ **2.755.641 sự kiện sạch** cho phục vụ. Công thức phục vụ mới chuẩn hóa điểm của từng seed theo cạnh mạnh nhất, lấy căn bậc hai điểm chuẩn hóa, rồi trộn 75% item-item với 25% chuyển tiếp. Bản trước trộn thứ hạng bằng reciprocal-rank fusion. Ngữ cảnh, bộ lọc món đã thấy và tập ứng viên vẫn giữ như cũ.

Chọn công thức và trọng số từ hai fold validation theo thời gian. Khi chấm test, graph chỉ học dữ liệu trước cutoff; graph đang phục vụ dùng bản đã refit trên toàn bộ lịch sử. Không dùng graph phục vụ để tự chấm lại test.

| Cách 2: mọi món mới còn lại trong phiên | Precision@10 | Recall@10 | NDCG@10 |
| --- | ---: | ---: | ---: |
| **Điểm liên kết, đang phục vụ** | **4,148%** | **28,28%** | **19,70%** |
| Trộn thứ hạng, bản trước | 4,116% | 28,08% | 19,33% |

Trên hai fold validation, bản mới tăng cả ba chỉ số. Trên 5.000 truy vấn test lịch sử, có **2.074 món đúng** trong top 10 so với **2.058** trước đó; số phiên có ít nhất một món đúng tăng **1.716 → 1.731**. Phép đo phụ chỉ một món tiếp theo cũng tăng từ **3,004% / 30,04% / 19,21%** lên **3,022% / 30,22% / 19,55%**.

Khoảng tin cậy bootstrap 95% theo cụm visitor cho chênh lệch **NDCG dương**, nhưng khoảng của Precision và Recall vẫn chứa 0. Tập test lịch sử đã từng được xem ở các vòng phát triển trước. Vì vậy đây là cải thiện đo được trên dữ liệu hiện có, **chưa đủ bằng chứng về mức tăng ổn định trên dữ liệu tương lai**.

Thí nghiệm graph tiếp diễn và pha độ phổ biến gần đây không vượt bản đang phục vụ trên validation; xem `outputs/evidence/session_continuation_validation.json` và `outputs/evidence/multilabel_popularity_validation.json`. Bản mới được chọn từ `outputs/evidence/multilabel_strength_validation.json` và chấm một lần trong `outputs/evidence/multilabel_strength_heldout.json`.

API đã thử ở cổng riêng và cổng chính: hai lượt Xem → Thêm giỏ đều trả 10 món, loại món đã tương tác; 26 kiểm thử session/API trong container Linux đạt. Smoke evidence: `outputs/evidence/demo-strength-20261022-v1-20261022-205346-smoke.json`. Snapshot trước vẫn ở `outputs/runs/remaining-20261022-v1/`, pointer cũ trong `outputs/evidence/latest_run_before_strength.json`.
