# Đánh giá tất cả món mới còn lại trong phiên

Ngày 22/10/2026, demo chọn `remaining-20261022-v1`. Bảng chính dùng **cách 2**: sau ngữ cảnh tại thời điểm gợi ý, mọi món mới khách tương tác cho đến cuối cùng phiên là đáp án đúng. Mỗi món chỉ tính một lần; các sự kiện cùng timestamp của mục tiêu đầu tiên đều có thể là đáp án. Chỉ dùng hành vi xảy ra trước thời điểm dự đoán làm ngữ cảnh. Phần tương lai bị chặn ở cuối khoảng đánh giá, không lấy dữ liệu ngoài fold.

Giữ nguyên cohort 5.000 truy vấn/period, cùng ngữ cảnh và quy tắc lọc món đã thấy như phép đánh giá một món tiếp theo. Hash cohort gốc đã được kiểm tra trước khi thay nhãn. Trong tập test lịch sử, **1.776/5.000 phiên** có hơn một món đúng, tổng cộng **10.279 món đúng phân biệt**; 3.224 phiên chỉ có một món đúng. Vì vậy Precision@10 không còn trần cố định 10% cho mọi truy vấn, nhưng nhiều truy vấn vẫn có trần 10% riêng.

| Mô hình, cách 2 | Precision@10 | Recall@10 | NDCG@10 |
| --- | ---: | ---: | ---: |
| **Đang phục vụ, 25% chuyển tiếp** | **4,116%** | **28,08%** | **19,33%** |
| Ứng viên, 20% chuyển tiếp | 4,118% | 28,03% | 19,27% |
| Phổ biến | 0,172% | 0,91% | 0,50% |

Trọng số 20% được chọn từ hai fold validation theo NDCG trung bình và tăng cả ba chỉ số trung bình validation so với 25%. Trên test lịch sử, nó chỉ nhỉnh hơn Precision 0,002 điểm phần trăm, trong khi Recall và NDCG thấp hơn. Bootstrap theo cụm visitor không xác nhận mức tăng. Vì vậy **không thay mô hình đang phục vụ**; graph và trọng số 25% vẫn dùng cho các request API. Test này đã từng được xem ở các vòng phát triển trước, nên chưa phải bằng chứng tương lai độc lập.

Bảng phụ **cách 1** chỉ chấm một món mới tiếp theo của cùng mô hình đang phục vụ: Precision@10 **3,004%**, Recall@10 **30,04%**, NDCG@10 **19,21%**. Không so trực tiếp tỷ lệ của hai bảng vì số đáp án mỗi truy vấn khác nhau.

## Bằng chứng và chạy lại

- `outputs/evidence/session_multilabel_validation.json`: phân bố nhãn và chọn trọng số ở hai fold.
- `outputs/evidence/session_multilabel_heldout.json`: kết quả test và bootstrap.
- `outputs/runs/remaining-20261022-v1/`: bundle chỉ số cách 2, bảng phụ cách 1 và graph phục vụ đã kiểm tra.
- `outputs/evidence/demo-remaining-20261022-v1-20261022-203649-smoke.json`: smoke test sau khi chọn snapshot.
- 24 kiểm thử session/API trong container Linux đạt. Kiểm thử API chạy trực tiếp bằng Python trên Windows vướng khóa SQLite tạm khi dọn thư mục; môi trường demo là container Linux.

Chạy lại demo bằng `scripts/run_session_demo.ps1 -SkipBuild`. Snapshot `transition-20261022-v1` vẫn còn; có thể khôi phục pointer từ `outputs/evidence/latest_run_before_multilabel.json`. Cần một giai đoạn dữ liệu tương lai mới để xác nhận chất lượng tổng quát.

## Thí nghiệm tiếp theo: quan hệ với mọi món về sau

Sau khi chọn cách 2 làm phép đo chính, đã thử graph nối mỗi món mới trong phiên với mọi món mới xuất hiện **sau** nó (cùng timestamp không tạo cạnh; tối đa 20 món phân biệt/phiên). Pha graph này với mô hình đang phục vụ ở các mức 0%, 10%, 20%, 30%, 40%, 50%, 75%, 100%, chỉ chọn theo hai fold validation.

Mức được chọn là **0%**, tức mô hình đang phục vụ. Ví dụ ở mức 20%, trung bình hai fold có Precision@10 **3,983%**, Recall@10 **26,285%**, NDCG@10 **18,402%**, thấp hơn mức 0% tương ứng **4,006% / 26,431% / 18,439%**. Không chấm thêm trên test để chọn một trọng số khác và không thay snapshot. Bằng chứng: `outputs/evidence/session_continuation_validation.json`.
