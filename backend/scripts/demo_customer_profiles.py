"""Masked demo profiles, unsuitable for real delivery or courier API requests."""

_REGIONS = [
    ("北京市", "北京市", "朝阳区", "100102"),
    ("上海市", "上海市", "浦东新区", "201204"),
    ("浙江省", "杭州市", "西湖区", "310030"),
    ("河北省", "石家庄市", "长安区", "050000"),
    ("广东省", "广州市", "天河区", "510665"),
    ("广东省", "深圳市", "南山区", "518057"),
    ("四川省", "成都市", "武侯区", "610041"),
    ("湖北省", "武汉市", "洪山区", "430074"),
    ("江苏省", "南京市", "建邺区", "210019"),
    ("山东省", "青岛市", "市南区", "266071"),
    ("陕西省", "西安市", "雁塔区", "710065"),
    ("福建省", "厦门市", "思明区", "361008"),
    ("湖南省", "长沙市", "岳麓区", "410013"),
    ("重庆市", "重庆市", "渝北区", "401147"),
]

DEMO_CUSTOMERS = [
    dict(name=f"演示客户{index:02d}", phone=f"138****{index:04d}",
         email=f"demo.customer{index:02d}@example.com", country="中国",
         province=province, city=city, district=district,
         detail=f"演示地址{index:02d}（虚构，不可投递）",
         postal_code=postal_code, longitude=None, latitude=None)
    for index, (province, city, district, postal_code) in enumerate(_REGIONS, 1)
]


def address_values(profile):
    return {"recipient_name": profile["name"], **{
        field: profile[field] for field in (
            "phone", "country", "province", "city", "district", "detail",
            "postal_code", "longitude", "latitude",
        )
    }}
