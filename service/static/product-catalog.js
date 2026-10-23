"use strict";
// Display-only catalog. Original RetailRocket item IDs remain the model inputs.
// Mapping is deterministic; illustrative names do not reveal real categories.
(() => {
  const entries = Object.freeze([
  {
    "key": "laptop",
    "name": "Laptop Aurora 14",
    "category": "Máy tính",
    "image": "/static/images/catalog/laptop.svg"
  },
  {
    "key": "keyboard",
    "name": "Bàn phím cơ Flow",
    "category": "Phụ kiện máy tính",
    "image": "/static/images/catalog/keyboard.svg"
  },
  {
    "key": "mouse",
    "name": "Chuột không dây Glide",
    "category": "Phụ kiện máy tính",
    "image": "/static/images/catalog/mouse.svg"
  },
  {
    "key": "watch",
    "name": "Đồng hồ Horizon",
    "category": "Đồng hồ",
    "image": "/static/images/catalog/watch.svg"
  },
  {
    "key": "pen",
    "name": "Bút bi Lumi",
    "category": "Văn phòng phẩm",
    "image": "/static/images/catalog/pen.svg"
  },
  {
    "key": "book",
    "name": "Sách Những điều hay",
    "category": "Sách",
    "image": "/static/images/catalog/book.svg"
  },
  {
    "key": "notebook",
    "name": "Vở kẻ ngang Campus",
    "category": "Văn phòng phẩm",
    "image": "/static/images/catalog/notebook.svg"
  },
  {
    "key": "phone",
    "name": "Điện thoại Nova",
    "category": "Điện thoại",
    "image": "/static/images/catalog/phone.svg"
  },
  {
    "key": "phone-case",
    "name": "Ốp lưng Soft Touch",
    "category": "Phụ kiện điện thoại",
    "image": "/static/images/catalog/phone-case.svg"
  },
  {
    "key": "iphone",
    "name": "iPhone mẫu Studio",
    "category": "Điện thoại",
    "image": "/static/images/catalog/iphone.svg"
  },
  {
    "key": "stove",
    "name": "Bếp điện đơn Cook",
    "category": "Nhà bếp",
    "image": "/static/images/catalog/stove.svg"
  },
  {
    "key": "lamp",
    "name": "Đèn bàn Halo",
    "category": "Đồ gia dụng",
    "image": "/static/images/catalog/lamp.svg"
  },
  {
    "key": "power-bank",
    "name": "Sạc dự phòng Pocket",
    "category": "Phụ kiện điện thoại",
    "image": "/static/images/catalog/power-bank.svg"
  },
  {
    "key": "tshirt",
    "name": "Áo thun Everyday",
    "category": "Thời trang",
    "image": "/static/images/catalog/tshirt.svg"
  },
  {
    "key": "shirt",
    "name": "Áo sơ mi Linen",
    "category": "Thời trang",
    "image": "/static/images/catalog/shirt.svg"
  },
  {
    "key": "jacket",
    "name": "Áo khoác Urban",
    "category": "Thời trang",
    "image": "/static/images/catalog/jacket.svg"
  },
  {
    "key": "jeans",
    "name": "Quần jean Straight",
    "category": "Thời trang",
    "image": "/static/images/catalog/jeans.svg"
  },
  {
    "key": "backpack",
    "name": "Balo Daypack",
    "category": "Túi & balo",
    "image": "/static/images/catalog/backpack.svg"
  },
  {
    "key": "headphones",
    "name": "Tai nghe chụp tai Studio",
    "category": "Âm thanh",
    "image": "/static/images/catalog/headphones.svg"
  },
  {
    "key": "earbuds",
    "name": "Tai nghe không dây Airy",
    "category": "Âm thanh",
    "image": "/static/images/catalog/earbuds.svg"
  },
  {
    "key": "speaker",
    "name": "Loa mini Echo",
    "category": "Âm thanh",
    "image": "/static/images/catalog/speaker.svg"
  },
  {
    "key": "camera",
    "name": "Máy ảnh Compact",
    "category": "Thiết bị số",
    "image": "/static/images/catalog/camera.svg"
  },
  {
    "key": "monitor",
    "name": "Màn hình Workview 24",
    "category": "Máy tính",
    "image": "/static/images/catalog/monitor.svg"
  },
  {
    "key": "tablet",
    "name": "Máy tính bảng Canvas",
    "category": "Thiết bị số",
    "image": "/static/images/catalog/tablet.svg"
  },
  {
    "key": "kettle",
    "name": "Ấm đun nước Daily",
    "category": "Nhà bếp",
    "image": "/static/images/catalog/kettle.svg"
  },
  {
    "key": "sneakers",
    "name": "Giày thể thao Move",
    "category": "Thời trang",
    "image": "/static/images/catalog/sneakers.svg"
  },
  {
    "key": "bottle",
    "name": "Bình nước Nomad",
    "category": "Đồ gia dụng",
    "image": "/static/images/catalog/bottle.svg"
  },
  {
    "key": "glasses",
    "name": "Kính mát Weekend",
    "category": "Phụ kiện thời trang",
    "image": "/static/images/catalog/glasses.svg"
  },
  {
    "key": "tote",
    "name": "Túi vải Canvas",
    "category": "Túi & balo",
    "image": "/static/images/catalog/tote.svg"
  },
  {
    "key": "charger",
    "name": "Củ sạc Compact",
    "category": "Phụ kiện điện thoại",
    "image": "/static/images/catalog/charger.svg"
  },
  {
    "key": "clock",
    "name": "Đồng hồ để bàn Round",
    "category": "Đồ gia dụng",
    "image": "/static/images/catalog/clock.svg"
  },
  {
    "key": "rice-cooker",
    "name": "Nồi cơm điện Home",
    "category": "Nhà bếp",
    "image": "/static/images/catalog/rice-cooker.svg"
  }
].map(entry => Object.freeze(entry)));
  const variants = Object.freeze(["01", "02", "03", "04", "05", "06"]);
  function hash(value) {
    let result = 2166136261;
    for (const character of String(value)) {
      result ^= character.charCodeAt(0);
      result = Math.imul(result, 16777619);
    }
    return result >>> 0;
  }
  function forItem(id) {
    const itemKey = String(id);
    const value = hash(itemKey);
    const entry = entries[value % entries.length];
    const variant = variants[hash("variant:" + itemKey) % variants.length];
    return Object.freeze({
      name: entry.name + " · " + variant,
      category: entry.category,
      image: entry.image
    });
  }
  window.ProductCatalog = Object.freeze({
    forItem,
    entries,
    disclosure: "Tên và hình minh họa được gán cố định theo ID để dễ trình bày. Chúng không phải tên, ảnh hoặc danh mục thật từ RetailRocket và không tham gia tính gợi ý."
  });
})();
