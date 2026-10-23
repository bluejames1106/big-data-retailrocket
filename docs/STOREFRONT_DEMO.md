# Retail Signal · Giao diện cửa hàng mô phỏng

## Bản cập nhật này dùng để làm gì?

Giao diện giúp trình diễn hệ gợi ý dưới dạng một cửa hàng: tìm sản phẩm, lọc danh mục, mở chi tiết, thêm giỏ và bấm **Mua ngay** ngay trên thẻ. Model vẫn xử lý **Item ID RetailRocket** và các hành vi `view`, `addtocart`, `transaction`; tên, ảnh, giá và danh mục chỉ phục vụ hiển thị.

Danh mục khám phá có **161 ID thật xuất hiện trong `events.csv`**, bao phủ 32 loại hình sản phẩm minh họa. ID 276494 được bổ sung để có bàn phím trong danh mục. Đây là một tập sản phẩm dùng trình diễn, không phải toàn bộ 235.061 sản phẩm của dữ liệu.

Bộ lọc có **19 danh mục cụ thể**, bên cạnh nút **Tất cả**: Laptop, Điện thoại, Máy tính bảng, Màn hình, Bàn phím & chuột, Phụ kiện điện thoại, Tai nghe, Loa, Máy ảnh, Đồng hồ, Quần áo, Giày, Túi & balo, Kính mắt, Sách, Văn phòng phẩm, Nhà bếp, Đèn và Bình nước. Mỗi danh mục đều có ít nhất một sản phẩm. Việc chia nhóm chỉ áp dụng cho danh mục minh họa; danh sách 161 ID, tên/ảnh cố định theo ID và thứ tự gợi ý của API không đổi.

## Chạy nhanh

Cần Docker Desktop đang hoạt động. Sau khi giải nén ZIP cập nhật, mở PowerShell trong thư mục `big_data_update_2`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_local_update2_demo.ps1
```

Mở **http://localhost:8000/**. Lần đầu dùng bản ZIP này cần để launcher build image. Những lần sau, nếu image đã đúng phiên bản, có thể thêm `-SkipBuild`. Nếu cổng 8000 đã bị chiếm, thêm `-Port 8002` rồi mở http://localhost:8002/.

Launcher dùng image `bigdata-update2:local`, Docker project `retailrocket-update2` và file `compose.local-update2.yaml`. Snapshot sẵn có đủ để phục vụ demo; không cần chạy lại toàn bộ pipeline để xem giao diện.

## Kịch bản trình diễn

1. **Khám phá sản phẩm:** dùng ô tìm kiếm để tìm tên minh họa hoặc Item ID. Có thể gõ không dấu, chẳng hạn `ban phim`. Chọn danh mục để lọc; nút xóa lọc khôi phục danh mục ban đầu.
2. **Mở chi tiết:** bấm ảnh, tên hoặc nút Xem. Hộp chi tiết giữ ID gốc, ghi rõ tên/ảnh/giá minh họa và mô tả nguồn của sản phẩm. Khi mô hình sẵn sàng và không có yêu cầu đang xử lý, mở chi tiết tạo một sự kiện xem mô phỏng.
3. **Xem gợi ý đổi:** khu vực **Dành cho bạn** đọc lại kết quả API theo ngữ cảnh phiên. Nhãn thứ hạng và Item ID cho phép đối chiếu danh sách mô hình trả về. Có thể đóng hộp chi tiết để xem danh sách mới.
4. **Thêm giỏ:** bấm + Giỏ trên thẻ hoặc trong hộp chi tiết. Thao tác tăng số lượng trong giỏ mô phỏng và ghi một sự kiện `addtocart`, rồi lấy lại gợi ý.
5. **Kiểm tra giỏ:** mở biểu tượng giỏ. Tăng/giảm số lượng, xóa sản phẩm và xóa giỏ chỉ cập nhật giỏ trong tab; các nút này không tự phát thêm sự kiện mô hình. Tổng tiền là tổng **giá minh họa**, không phải doanh thu thực.
6. **Mua ngay:** bấm nút xanh **Mua ngay** ngay trên thẻ ở **Dành cho bạn** hoặc **Khám phá sản phẩm**; không cần mở chi tiết. Một lần bấm trực tiếp ghi đúng một sự kiện `transaction` mô phỏng, rồi gọi lại API để cập nhật gợi ý. Thao tác này không tự thêm lượt xem, không thêm giỏ và không thay đổi số lượng trong giỏ. Hộp chi tiết cũng có nút **Mua ngay · mô phỏng**; mở hộp chi tiết trước đó là một thao tác xem riêng. Chú thích ngay dưới nút và thông báo sau khi bấm xác nhận phạm vi mô phỏng. Nếu API cập nhật gợi ý thất bại, giao diện báo rõ lỗi thay vì thông báo đã cập nhật thành công. Không có đơn hàng, thanh toán hay giao dịch tài chính.
7. **Phiên mới:** xóa ngữ cảnh và giỏ mô phỏng để bắt đầu lại. Tải lại trang cũng bắt đầu trạng thái mới trong tab; dữ liệu nguồn không bị sửa.

Trong lúc gửi yêu cầu, các thao tác tạo tương tác được khóa để tránh ghi trùng hoặc làm mất thứ tự sự kiện. Hộp chi tiết và giỏ có nút đóng; có thể dùng Esc để đóng.

## Hai danh sách cần phân biệt

| Khu vực | Nguồn sản phẩm và thứ tự | Tìm kiếm/danh mục có tác dụng gì? |
| --- | --- | --- |
| Khám phá sản phẩm | Tập 161 ID cố định lấy từ danh sách fallback của bundle hiện tại | Lọc theo tên và danh mục minh họa; không phải kết quả tìm kiếm toàn bộ dữ liệu |
| Dành cho bạn | Danh sách API gợi ý theo phiên, giữ nguyên thứ tự xếp hạng | Không bị sắp xếp hoặc lọc lại theo tên, danh mục hay giá minh họa |

Vì thông tin trình bày được gán theo ID, hai ID có tên hoặc hình cùng loại **không chứng minh chúng cùng loại trong dữ liệu thật**. Ngược lại, hai món được model liên kết có thể có hình minh họa khác loại. Mối liên hệ của model đến từ lịch sử hành vi trên ID.

## Số liệu được giữ nguyên

Đợt cập nhật giao diện không huấn luyện lại và không tính lại chất lượng mô hình. Run phục vụ tiếp tục là `strength-20261022-v1`. Các thao tác mô phỏng không được ghi vào `events.csv`, không tham gia tập kiểm tra và không làm đổi chỉ số Precision/Recall/NDCG đã công bố.

Phép chấm chính xét các món mới còn lại trong cùng phiên, trên 5.000 lượt dự đoán và Top 10 cố định. Precision@10 là 4,148%, Recall@10 là 28,2803%, NDCG@10 là 19,7041% trước khi làm tròn. Chọn hiển thị 5 hoặc 20 món không thay đổi báo cáo này. Không so trực tiếp với phép chấm một món tiếp theo hay phép chấm lịch sử người dùng.

Tập kiểm tra lịch sử đã được xem trong quá trình cải tiến mô hình. Cần dữ liệu ở giai đoạn tương lai để xác nhận khả năng tổng quát hóa. Khoảng tin cậy của mức tăng Precision và Recall so với bản liền trước còn chứa 0; không tuyên bố cả ba chỉ số đều cải thiện chắc chắn.

## Tra cứu API và vận hành

Các mục đánh giá, dữ liệu và **Mô hình & hệ thống** vẫn ở phần thông tin thu gọn. Mở **Tài liệu API** hoặc truy cập http://localhost:8000/docs để xem/thử API. Điểm xếp hạng nằm trong chi tiết của sản phẩm được gợi ý; đó không phải xác suất mua.

Các tệp giao diện chính:

- `service/static/index.html`: bố cục cửa hàng, tìm kiếm, giỏ và hộp chi tiết.
- `service/static/style.css`, `service/static/storefront.css`: giao diện và bố cục thích ứng.
- `service/static/app.js`: gọi API, ngữ cảnh phiên và tương tác cửa hàng.
- `service/static/product-catalog.js`: ánh xạ tên/hình minh họa ổn định theo ID.
- `service/static/storefront-data.js`: 161 ID khám phá, nhóm danh mục và giá minh họa.
- `service/static/images/catalog/`: 32 ảnh SVG cục bộ, dùng được khi không truy cập ảnh bên ngoài.

ZIP cập nhật giữ `SOURCE_PACKAGE_SHA256.json` để đối chiếu gói bạn gửi, `PACKAGE_SHA256.json` chứa hash của nội dung gói mới và `STOREFRONT_PACKAGE_INTEGRITY.json` ghi kiểm tra tính toàn vẹn. Bản ZIP mới là `big_data_update_2_storefront_v2.zip`, giữ riêng các ZIP trước. Bằng chứng cho nút mua và danh mục nằm tại `outputs/evidence/buy_now/`; kiểm tra giao diện cửa hàng trước nằm tại `outputs/evidence/storefront/`.
