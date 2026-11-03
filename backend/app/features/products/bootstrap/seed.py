"""商品演示数据：服务启动时初始化基础商品，并在首次运行时追加中文商品。"""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.products.models import ProductModel

# [商品演示数据 / 基础英文样本] 新数据库首次启动时插入，后续不覆盖管理员商品。
DEMO_PRODUCTS = [
    {"name": "Keychron K2 Pro Wireless Keyboard", "category": "Electronics", "price": 105, "stock": 28, "status": "Active", "image_url": "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=800&q=85", "color": "Black", "rating": 4.9, "reviews": 1248, "shipping": "Regular"},
    {"name": "Ultra-Light Gaming Mouse", "category": "Electronics", "price": 32, "stock": 7, "status": "Active", "image_url": "https://images.unsplash.com/photo-1527814050087-3793815479db?auto=format&fit=crop&w=800&q=85", "color": "Black", "rating": 4.8, "reviews": 208, "shipping": "Next Day"},
    {"name": "Wireless Over-Ear Headphones", "category": "Electronics", "price": 45, "stock": 0, "status": "Out of stock", "image_url": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=800&q=85", "color": "Blue", "rating": 4.6, "reviews": 83, "shipping": "Regular"},
    {"name": "Multi-Platform Game Controller", "category": "Electronics", "price": 120, "stock": 16, "status": "Active", "image_url": "https://images.unsplash.com/photo-1600080972464-8e5f35f63d08?auto=format&fit=crop&w=800&q=85", "color": "Black", "rating": 4.7, "reviews": 156, "shipping": "Next Day"},
    {"name": "Premium Braided USB-C Cable", "category": "Tools", "price": 18, "stock": 124, "status": "Active", "image_url": "https://images.unsplash.com/photo-1615526675159-e248c3021d3f?auto=format&fit=crop&w=800&q=85", "color": "Black", "rating": 4.5, "reviews": 62, "shipping": "Economic"},
    {"name": "Ergonomic Wireless Mouse", "category": "Electronics", "price": 68, "stock": 42, "status": "Active", "image_url": "https://images.unsplash.com/photo-1615663245857-ac93bb7c39e7?auto=format&fit=crop&w=800&q=85", "color": "Red", "rating": 4.9, "reviews": 391, "shipping": "Regular"},
    {"name": "Compact USB-C Hub 7-in-1", "category": "Electronics", "price": 49, "stock": 5, "status": "Low stock", "image_url": "https://images.unsplash.com/photo-1625842268584-8f3296236761?auto=format&fit=crop&w=800&q=85", "color": "Blue", "rating": 4.4, "reviews": 118, "shipping": "Instant"},
    {"name": "Protective Travel Case", "category": "Fashion", "price": 47, "stock": 31, "status": "Active", "image_url": "https://images.unsplash.com/photo-1622560480654-d96214fdc887?auto=format&fit=crop&w=800&q=85", "color": "Black", "rating": 4.3, "reviews": 134, "shipping": "Economic"},
    {"name": "Studio Monitor Headphones", "category": "Electronics", "price": 149, "stock": 12, "status": "Active", "image_url": "https://images.unsplash.com/photo-1546435770-a3e426bf472b?auto=format&fit=crop&w=800&q=85", "color": "Black", "rating": 4.8, "reviews": 287, "shipping": "Next Day"},
    {"name": "Minimal Ceramic Desk Lamp", "category": "Furniture", "price": 89, "stock": 19, "status": "Active", "image_url": "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?auto=format&fit=crop&w=800&q=85", "color": "Orange", "rating": 4.2, "reviews": 46, "shipping": "Regular"},
    {"name": "Mechanical Keyboard Wrist Rest", "category": "Electronics", "price": 24, "stock": 0, "status": "Draft", "image_url": "https://images.unsplash.com/photo-1595225476474-87563907a212?auto=format&fit=crop&w=800&q=85", "color": "Red", "rating": 4.1, "reviews": 32, "shipping": "Economic"},
    {"name": "Smart Watch Series 5", "category": "Electronics", "price": 199, "stock": 9, "status": "Low stock", "image_url": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=800&q=85", "color": "Orange", "rating": 4.6, "reviews": 512, "shipping": "Instant"},
]


# [商品演示数据 / 中文目录模板] 每类包含基础价格和商品名称，生成器据此构造库存等字段。
CHINESE_PRODUCT_GROUPS = [
    ("数码电子", 699, ["星河 X1 智能手机", "轻舟 Mini 5G 手机", "云屏 11 英寸平板电脑", "灵犀无线蓝牙耳机", "远山降噪头戴耳机", "极光智能手表", "迅影机械键盘", "轻羽无线鼠标", "拓界八口扩展坞", "曜石便携固态硬盘"]),
    ("家居生活", 89, ["云朵人体工学椅", "暖光护眼台灯", "静音循环空气扇", "柔棉四季空调被", "原木折叠边桌", "轻音加湿器", "简约收纳置物架", "全遮光遮阳窗帘", "恒温电热水壶", "柔光落地阅读灯"]),
    ("厨房用品", 29, ["麦饭石不粘煎锅", "耐热玻璃保鲜盒", "手冲咖啡分享壶", "多功能料理剪刀", "硅胶烘焙垫", "双层不锈钢饭盒", "家用手动绞肉机", "陶瓷调味料罐", "折叠沥水篮", "便携保温焖烧杯"]),
    ("运动户外", 59, ["轻量缓震跑步鞋", "速干透气运动上衣", "双肩户外徒步背包", "折叠露营月亮椅", "防风防水登山外套", "瑜伽防滑加厚垫", "铝合金登山杖", "便携运动水壶", "夜跑感应安全灯", "加宽弹力拉力带"]),
    ("办公文具", 12, ["A5 方格手账本", "按动中性笔十支装", "桌面无线充电器", "人体工学腕托", "可调节笔记本支架", "静音无线办公鼠标", "磁吸白板便签套装", "多层文件收纳盒", "护眼屏幕挂灯", "便携式标签打印机"]),
    ("服饰配件", 39, ["轻薄防晒连帽外套", "简约真皮卡包", "复古帆布托特包", "柔软羊毛围巾", "防水轻便双肩包", "经典针扣皮带", "透气棉麻棒球帽", "轻盈保暖羽绒马甲", "简约钛钢项链", "旅行收纳首饰盒"]),
    ("影音设备", 129, ["桌面蓝牙音箱", "高清无线领夹麦克风", "便携式迷你投影仪", "双接口录音麦克风", "家庭影院回音壁", "复古黑胶唱片机", "无线会议全向麦", "4K 视频采集卡", "入耳式监听耳机", "便携数码录音笔"]),
    ("美妆个护", 19, ["温和氨基酸洁面乳", "高保湿补水面霜", "便携负离子吹风机", "声波电动牙刷", "多功能洁面仪", "轻透防晒乳", "精华液分装旅行瓶", "恒温卷发棒", "天然植物护手霜", "硅胶头皮按摩梳"]),
    ("宠物用品", 25, ["可拆洗宠物窝垫", "自动循环饮水机", "猫咪逗趣羽毛玩具", "便携宠物外出包", "耐咬磨牙玩具", "双碗防滑喂食垫", "宠物毛发清理刷", "智能定时喂食器", "可调节宠物牵引绳", "猫砂除臭收纳桶"]),
    ("母婴用品", 35, ["柔软纯棉口水巾", "轻便折叠婴儿推车", "恒温冲奶保温杯", "可调节儿童餐椅", "婴儿安抚睡袋", "便携式暖奶器", "硅胶辅食分格盒", "儿童防撞安全护角", "透气新生儿包被", "吸盘式宝宝辅食碗"]),
]


def build_chinese_products() -> list[dict[str, object]]:
    """按固定规则生成多分类中文演示商品，便于重复启动时保持数据一致。"""
    colors = ["Black", "Blue", "Red", "Orange"]
    shipping_options = ["Regular", "Next Day", "Economic", "Instant"]
    products: list[dict[str, object]] = []
    for group_index, (category, base_price, names) in enumerate(CHINESE_PRODUCT_GROUPS):
        for item_index, name in enumerate(names):
            index = group_index * 10 + item_index
            stock = (index * 37 + 11) % 121
            if index % 19 == 0:
                stock = 0
                status = "Out of stock"
            elif index % 13 == 0:
                stock = 5
                status = "Low stock"
            elif index % 29 == 0:
                status = "Draft"
            else:
                status = "Active"
            products.append(
                {
                    "name": name,
                    "category": category,
                    "price": base_price + item_index * (7 + group_index % 5),
                    "stock": stock,
                    "status": status,
                    "image_url": DEMO_PRODUCTS[index % len(DEMO_PRODUCTS)]["image_url"],
                    "color": colors[(index + group_index) % len(colors)],
                    "rating": Decimal(f"{4 + (index % 11) / 10:.1f}"),
                    "reviews": (index * 47 + 23) % 1800,
                    "shipping": shipping_options[(index + group_index) % len(shipping_options)],
                }
            )
    return products


CHINESE_PRODUCTS = build_chinese_products()


def seed_products_if_needed(db: Session) -> None:
    """补齐演示商品：基础目录为空时初始化英文商品，再按批次补中文目录。"""
    product_count = db.scalar(select(func.count()).select_from(ProductModel)) or 0
    if not product_count:
        db.add_all(
            ProductModel(
                **{
                    **item,
                    "price": Decimal(str(item["price"])),
                    "rating": Decimal(str(item["rating"])),
                }
            )
            for item in DEMO_PRODUCTS
        )
        db.commit()

    chinese_names = {item["name"] for item in CHINESE_PRODUCTS}
    existing_chinese_names = set(
        db.scalars(select(ProductModel.name).where(ProductModel.name.in_(chinese_names)))
    )
    if existing_chinese_names:
        return

    db.add_all(ProductModel(**item) for item in CHINESE_PRODUCTS)
    db.commit()
