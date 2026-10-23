"use strict";
// Display catalog only. IDs are copied from the current bundle's observed-item fallback list.
// Names, groups and prices are illustrative and never used as model features.
(() => {
  const ids = Object.freeze([434782,17114,187946,245085,461686,320130,76060,96924,188051,5411,314579,190000,158220,219512,350629,198784,227112,65273,403576,190536,416357,363125,153778,9877,162416,218746,384302,142466,394678,455183,425186,441668,303715,7943,112782,206880,215715,241555,303960,400946,150445,151444,209994,359251,367102,51650,102306,429094,99187,314801,325852,350103,287664,298196,400600,56323,122604,247909,390591,357474,224231,254418,460187,225778,298009,334520,370653,386947,17108,158666,204070,447661,461190,111530,361499,38965,213834,66752,161431,218794,260650,266900,12217,13925,62283,176162,215363,338660,341482,395845,403757,459659,45175,91755,120822,232509,302563,315393,315545,320844,24734,82586,354153,461091,130865,182785,393028,102605,162055,233734,428085,50467,84160,103242,161623,184998,243355,351044,8300,33245,45337,54502,110283,118780,170390,277490,319602,325310,369933,393744,415562,449571,455006,9211,89871,215051,216888,273936,340825,368060,422671,426031,454818,463804,41039,58663,63543,89775,108486,131745,171878,185628,203908,215596,308438,340960,365202,384157,395435,463663,276494]);
  const prices = Object.freeze({laptop:16990000, keyboard:890000, mouse:349000, watch:1290000,
    pen:12000, book:129000, notebook:25000, phone:6490000, "phone-case":129000,
    iphone:21990000, stove:890000, lamp:349000, "power-bank":499000, tshirt:199000,
    shirt:349000, jacket:599000, jeans:449000, backpack:499000, headphones:1190000,
    earbuds:799000, speaker:599000, camera:8490000, monitor:3490000, tablet:5990000,
    kettle:399000, sneakers:699000, bottle:179000, glasses:299000, tote:149000,
    charger:299000, clock:249000, "rice-cooker":1290000});
  const displayGroups = Object.freeze({"laptop":"Laptop","monitor":"Màn hình","phone":"Điện thoại","iphone":"Điện thoại","tablet":"Máy tính bảng","keyboard":"Bàn phím & chuột","mouse":"Bàn phím & chuột","phone-case":"Phụ kiện điện thoại","power-bank":"Phụ kiện điện thoại","charger":"Phụ kiện điện thoại","headphones":"Tai nghe","earbuds":"Tai nghe","speaker":"Loa","camera":"Máy ảnh","watch":"Đồng hồ","clock":"Đồng hồ","tshirt":"Quần áo","shirt":"Quần áo","jacket":"Quần áo","jeans":"Quần áo","sneakers":"Giày","backpack":"Túi & balo","tote":"Túi & balo","glasses":"Kính mắt","book":"Sách","pen":"Văn phòng phẩm","notebook":"Văn phòng phẩm","stove":"Nhà bếp","kettle":"Nhà bếp","rice-cooker":"Nhà bếp","lamp":"Đèn","bottle":"Bình nước"});
  function describe(id) {
    const item = window.ProductCatalog.forItem(id);
    const key = item.image.split("/").at(-1).replace(".svg", "");
    const base = prices[key] || 199000;
    const variation = (Number(id) % 5) * 0.025;
    return Object.freeze({...item, priceVnd: Math.round(base * (1 + variation) / 1000) * 1000,
      group: displayGroups[key] || item.category});
  }
  window.ShopCatalog = Object.freeze({items:ids, describe,
    groups:Object.freeze(["Tất cả","Laptop","Điện thoại","Máy tính bảng","Màn hình","Bàn phím & chuột","Phụ kiện điện thoại","Tai nghe","Loa","Máy ảnh","Đồng hồ","Quần áo","Giày","Túi & balo","Kính mắt","Sách","Văn phòng phẩm","Nhà bếp","Đèn","Bình nước"]),
    disclosure:"Tên, hình, danh mục và giá đều là minh họa; không phải thông tin thật của các Item ID RetailRocket."});
})();
