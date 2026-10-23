# Dashboard Studio

Dashboard được làm gọn trên cơ sở `big_data_update_2.zip`, phục vụ snapshot `strength-20261022-v1`. Thay đổi tập trung vào trình bày và thao tác demo, không huấn luyện lại mô hình.

## Nội dung chính

1. Ba chỉ số chất lượng theo phiên: Precision@10, Recall@10, NDCG@10; kèm baseline phổ biến, phạm vi 5.000 lượt dự đoán và liên kết cách chấm.
2. Khu vực thử gợi ý: danh sách sản phẩm minh họa, Item ID gốc, thao tác Xem / Giỏ / Mua thử, lịch sử ngữ cảnh trong tab và nút bắt đầu phiên mới.
3. Dữ liệu nguồn: số sự kiện sạch, người dùng, item; phân bố hành vi trong mục mở rộng.
4. Các mục mở rộng: so sánh công bằng, phép chấm phụ, tra cứu theo lịch sử user, phiên bản mô hình, kết nối dịch vụ và độ trễ.

## Tên và hình sản phẩm

`service/static/product-catalog.js` chứa 32 loại sản phẩm và hàm gán cố định dựa trên chuỗi Item ID. Hình SVG cục bộ ở `service/static/images/catalog/`, không cần tải ảnh ngoài mạng. Cùng ID giữ cùng tên và ảnh; tên không phải khóa duy nhất.

Các loại gồm laptop, bàn phím, chuột, đồng hồ, bút, sách, vở, điện thoại, ốp lưng, iPhone minh họa, bếp điện, đèn, sạc dự phòng, áo thun, áo sơ mi, áo khoác, quần jean, balo, tai nghe, loa, máy ảnh, màn hình, máy tính bảng, ấm đun, giày, bình nước, kính, túi, củ sạc, đồng hồ bàn và nồi cơm điện.

Đây là **tên, ảnh và danh mục minh họa**, không phải thông tin thật trong dữ liệu. Chúng không được gửi thành đặc trưng cho mô hình. Gợi ý vẫn dùng ID và hành vi thật; không dùng tên minh họa để kết luận hai ID có quan hệ danh mục thật.

## Tính minh bạch của số liệu

Phép chấm chính dùng tất cả món mới còn lại trong phiên: P@10 4,148%, R@10 28,2803%, NDCG@10 19,7041%. Phép chấm một món tiếp theo và phép chấm lịch sử user được ghi riêng. Số lượng sản phẩm chọn trên dashboard không tính lại báo cáo @10.

Báo cáo so sánh vẫn công khai: cùng ngữ cảnh và đáp án; chỉ dữ liệu trước thời điểm dự đoán được dùng khi đánh giá; lịch sử test từng được xem; chỉ NDCG có khoảng tin cậy mức tăng hoàn toàn dương so bản trước. Snapshot phục vụ là bản refit trên toàn bộ lịch sử, không dùng bản refit đó để chấm lại test.

KPI gốc có liên kết run cũ. Sau kiểm tra trực tiếp CSV với khóa khử trùng của pipeline, các giá trị đếm trùng khớp, nên chỉ sửa metadata liên kết về run đang phục vụ và giữ nguồn gốc. Không đổi kiểm tra an toàn trong `service/store.py`.

## Chạy và demo

```powershell
cd D:\BIGDATA\big_data_update_2
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_local_update2_demo.ps1
```

Mở http://localhost:8000/#session-section . Các lần sau có thể thêm `-SkipBuild` khi image đã được build từ đúng mã nguồn này.

Thử bấm **Xem** một sản phẩm, kiểm tra ID đó hiện trong ngữ cảnh và bị loại khỏi gợi ý mới. Thêm một sản phẩm vào giỏ để thay đổi trọng số ngữ cảnh. Chọn **Phiên mới** để bỏ các tương tác vừa mô phỏng. Mở **So sánh & cách đánh giá** khi cần trình bày chỉ số với giảng viên.

Gói cập nhật: `big_data_update_2_dashboard.zip`. Manifest mới kiểm tra nội dung ZIP; `SOURCE_PACKAGE_SHA256.json` giữ manifest từ gói nguồn. Dữ liệu, model và báo cáo đánh giá được đối chiếu hash trước khi đóng gói; không chạy lại huấn luyện.
