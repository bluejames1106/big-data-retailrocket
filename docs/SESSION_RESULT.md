# Kết quả gợi ý theo phiên và thử nghiệm item-item có trọng số

Run: `session-20261022-1327`. Nguồn: `full-20261022-1031`, Delta Silver version 0. Mô hình đối chiếu: `neighbors-20261022-1233`.

## Kết luận để dùng khi demo

Đã triển khai gợi ý theo các thao tác mới trong phiên, API và dashboard; đã thử item-item có trọng số hành vi, time-decay và quan hệ trong phiên. **Các cấu hình có trọng số không vượt item-item hiện tại theo điều kiện đã đăng ký**, nên chúng không được thay thế mô hình đang phục vụ. Demo dùng Jaccard nhị phân tốt hơn, bổ sung khả năng nhận ngữ cảnh phiên và chỉ mục hàng xóm toàn cục. Đây là kết quả thực nghiệm; không chỉnh nhãn, đổi cohort hoặc bỏ lượt trượt để tăng điểm.

## Đã làm gì

- Đọc đúng 2.755.641 sự kiện sạch từ snapshot Delta đầy đủ; không quay về mẫu 10.000 dòng.
- Thử trọng số view=1, addtocart=3, transaction=5, giảm trọng số theo tuổi tương tác với half-life 30 ngày khi học graph; khi suy luận phiên, thử half-life 30 phút và hệ số vị trí 0,8. Dùng giá trị lớn nhất trên mỗi entity-item để lặp view không làm tăng điểm vô hạn.
- Thử graph theo người dùng và graph theo phiên, phiên mới khi khoảng cách giữa hai sự kiện lớn hơn 30 phút; mỗi entity giữ tối đa 20 sản phẩm phân biệt gần nhất. Thử thêm pha trộn 20% độ phổ biến bằng reciprocal-rank fusion. Cả ba ứng viên cố định trước validation.
- Học chỉ mục phục vụ từ toàn bộ lịch sử: 2.671.950 cạnh, 149.492 seed có hàng xóm; 230.267 sản phẩm được xử lý sau cap 20. Không phải mọi sản phẩm trong catalog 235.061 món đều có hàng xóm. SQLite graph 63.721.472 byte, đọc ở chế độ read-only.
- POST `/api/session/recommendations`: tối đa 100 sự kiện, lấy tối đa 5 seed gần nhất; loại món trong ngữ cảnh. Nếu có Visitor ID, lọc thêm lịch sử đầy đủ bằng SQLite và lịch sử online. Khách mới vẫn có thể được cá nhân hóa khi đã xem một món có hàng xóm.
- Dashboard có Xem / + Giỏ / Mua thử, nhập Item ID, xóa phiên, bảng đánh giá phiên riêng. Sự kiện demo chỉ nằm trong tab, không tạo đơn hàng, không sửa dữ liệu train/test. Do cấu hình đang dùng là binary, loại hành vi được ghi nhận nhưng trọng số 1/3/5 chưa được bật trong phục vụ.
- Giữ API gợi ý theo user và 1.000 user xuất sẵn. Mở rộng truy vấn graph theo ngữ cảnh cho các seed có hàng xóm; không tuyên bố đã xuất sẵn gợi ý riêng cho toàn bộ 1,4 triệu user.

## 1. Đánh giá static: cùng cohort chính cũ, K=10

Mỗi user có thể có nhiều sản phẩm mới trong hold-out. Macro trung bình theo 1.000 user; giữ nguyên 910 cold user, 1.415 cặp novel và toàn bộ dự đoán của mô hình hiện tại. Precision chia cho 10, Recall chia cho số món đúng của từng user.

| Cấu hình | Precision@10 | Recall@10 | NDCG@10 | Hit |
|---|---:|---:|---:|---:|
| Item-item hiện tại, tiếp tục phục vụ | 0,1400% | 0,8033% | **0,5563%** | 14 |
| Ứng viên weighted_user | 0,1400% | **0,8867%** | 0,5132% | 14 |
| Phổ biến 7 ngày | 0,1000% | 0,7450% | 0,4680% | 10 |

Recall của ứng viên tăng nhưng NDCG giảm; Precision thực chất bằng nhau (sai số số thực ở chữ số rất nhỏ không phải cải thiện). Ứng viên cũng không vượt cả ba chỉ số trên validation. Vì vậy **giữ static hiện tại**. Kết quả này không chứng minh cải thiện toàn diện so với mô hình trước.

## 2. Đánh giá theo phiên: cùng 5.000 truy vấn cho cả ba mô hình

Mục tiêu: dự đoán **món mới đầu tiên tiếp theo trong phiên** khi đã có ít nhất một món trong ngữ cảnh. Mỗi phiên tối đa một truy vấn; đồng timestamp không được dùng làm ngữ cảnh cho nhau. Nếu nhiều món mới cùng thời điểm, chọn Item ID nhỏ nhất theo quy tắc cố định. Seen trong phép đo này là các món trước đó của cùng phiên; mua/xem lại món từ phiên cũ vẫn có thể là mục tiêu. API có Visitor ID lọc lịch sử rộng hơn, nên số điểm này tương ứng chế độ phiên ẩn danh.

| Cấu hình | Precision@10 | Recall@10 / Hit-rate | NDCG@10 | Hit / 5.000 |
|---|---:|---:|---:|---:|
| **Item-item hiện tại + cùng ngữ cảnh phiên, đang phục vụ** | **2,7000%** | **27,0000%** | **17,0206%** | **1.350** |
| Ứng viên weighted_session_pop20 | 2,5080% | 25,0800% | 15,4539% | 1.254 |
| Phổ biến 7 ngày | 0,1000% | 1,0000% | 0,4623% | 50 |

Đối chứng `current_item_item` là **thuật toán hiện tại được cấp cùng ngữ cảnh phiên**, không phải lấy điểm static cũ rồi so với điểm phiên mới. Đây là đối chứng mạnh và công bằng: cùng đồ thị chỉ học quá khứ, cùng ngữ cảnh, cùng seen, cùng mục tiêu và cùng K. Việc phục vụ được ngữ cảnh cho khách ẩn danh là chức năng mới; điểm 27% không chứng minh thuật toán weighted tốt hơn Jaccard.

Vì mỗi truy vấn chỉ có một món đúng, Precision@10 tối đa 10%; Recall@10 bằng hit-rate. **Không so trực tiếp 27% Recall phiên với 0,8033% Recall static**: đơn vị lấy trung bình, mục tiêu và điều kiện tham gia khác nhau.

Có 274.857 phiên hoạt động trong kỳ test, chỉ 38.945 phiên đủ điều kiện (khoảng 14,17%); 235.912 phiên không có ngữ cảnh/món mới tiếp theo đủ điều kiện. Lấy 5.000 phiên bằng SHA256 cố định, gồm 4.759 visitor; không chọn theo dự đoán hoặc độ dễ. Giữ 273 mục tiêu chưa có trong catalog train như lượt trượt. Phiên chỉ có một sự kiện không được đại diện bởi điểm conditional này; khi demo chưa có ngữ cảnh, hệ thống dùng phổ biến.

## Độ chắc chắn và giới hạn

Bootstrap ghép cặp 1.000 lần theo cụm visitor, seed 42, giữ trọng số theo số truy vấn. CI 95% của chênh lệch ứng viên weighted so với item-item hiện tại **âm ở cả ba metric**. Riêng Recall: −1,92 điểm phần trăm, CI khoảng [−2,82; −1,09] điểm phần trăm. Vì vậy có bằng chứng ứng viên kém hơn trên cohort này.

Item-item đang phục vụ so với phổ biến, trong cùng phép đo phiên: Recall tăng 26,00 điểm phần trăm, CI [24,81; 27,26]; NDCG tăng 16,56 điểm phần trăm, CI [15,76; 17,47]. Đây là CI từng metric, không phải kiểm định đồng thời cho tất cả metric.

Hai fold validation dùng các mốc thời gian 50%→70% và 70%→85%; khóa cấu hình riêng cho static/phiên bằng trung bình NDCG, rồi Recall/Precision. Test ở phần cuối 15% không dùng để chọn trọng số. Static primary đã được xem ở các lần làm trước; phiên mới vẫn dùng cùng giai đoạn dữ liệu lịch sử, **chưa phải test ở tương lai mới**. Sau khi chấm, graph phục vụ được refit trên 100% lịch sử; không chấm graph full đó trên chính test cũ.

Không dùng negative sampling. Truy xuất ứng viên có giới hạn: top 100 hàng xóm mỗi seed, top 100 điểm item tổng hợp, rồi dự phòng phổ biến; không chấm toàn catalog bằng dense ranking. Training giữ tối đa 20 item/entity; “full data” nghĩa là đọc đủ snapshot, không có nghĩa giữ mọi cặp lịch sử trong graph. Các giới hạn này có thể làm một số mục tiêu không thể được truy xuất; chúng vẫn được tính là trượt.

## Kiểm tra và bằng chứng

- `metrics/experiment_plan.json`, `policy_selection.json`, bốn file validation và hai file promotion: cấu hình, thời điểm khóa và điều kiện thay thế.
- `static_test_rankings.jsonl`, `session_test_rankings.jsonl`: từng mục tiêu, ngữ cảnh quá khứ, seen và ba danh sách dự đoán; có thể tự tính lại điểm.
- `independent_session_audit.json`: kiểm toán hash/cohort, mọi timestamp, không gợi ý seen/trùng, tính lại P/R/NDCG, tái tạo chọn cấu hình và quyết định giữ model. Static hiện tại trùng chính xác ranking cũ.
- `served_session_comparison.json`: điểm của cấu hình thực sự đang phục vụ, CI so với phổ biến, độ phủ chỉ mục và SQLite quick_check.
- `provenance_and_clarifications.json`: hash mã thực sự đã chạy và chú thích phạm vi retrieval; không sửa ranking hay chọn cấu hình sau test.
- Thử HTTP và giao diện: phiên mới dùng phổ biến; xem Item 434782 đổi sang hàng xóm; thêm giỏ Item 215363 đổi tiếp, loại cả hai món đã thấy. Ảnh `evidence/session-demo.png`, `evidence/session-metrics.png`.

Xem [SESSION_GUIDE.md](SESSION_GUIDE.md) để chạy demo hoặc huấn luyện lại. ZIP gồm source Phase 1–7, full CSV, evidence và snapshot phục vụ tối thiểu; không gồm đĩa Docker, HDFS, ALS binary hoặc Spark checkpoint. Demo dùng snapshot không cần huấn luyện lại. Muốn tái tạo toàn pipeline trên máy mới phải dựng lại dữ liệu/Delta/model theo hướng dẫn.

```powershell
cd D:\BIGDATA\BigData_Phase_1_7
powershell -ExecutionPolicy Bypass -File scripts\run_session_demo.ps1
```

Mở `http://localhost:8000/#session-section`. Giải nén ZIP vào một thư mục riêng trước khi chạy; không đè lẫn với model/output từ một lần chạy khác.

## Xác minh lần chạy cuối

76 kiểm thử liên quan đều đạt. Kiểm toán độc lập đạt cho cả hai protocol và quyết định giữ mô hình. HTTP phiên thực: 100/100 yêu cầu thành công, concurrency 4, ba warm-up; p95 khoảng 323.1 ms trên máy demo. Không suy diễn thành SLA hay tải production.

Snapshot trích xuất từ ZIP đã chạy trong container riêng ở cổng 8001, không có HDFS/Redis/MongoDB; 100/100 yêu cầu và kiểm tra lọc lịch sử/đổi ngữ cảnh đều đạt. Benchmark trên host Windows qua localhost có p95 2311.5 ms; đây là đường client/mạng khác phép đo container→container ở trên, không so trực tiếp hai độ trễ. Log riêng: `evidence/session-portable-http.log`. Container kiểm tra đã được dừng; demo chính tiếp tục ở cổng 8000.
